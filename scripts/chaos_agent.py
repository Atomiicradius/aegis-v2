"""chaos_agent.py -- localhost chaos-injection HTTP server (stdlib only).

Exposes fault scenarios from chaos.py over HTTP so the ChaosDock UI can trigger
injection without shell access. Destructive operations require --enable; without
it /run returns 403.

    python scripts/chaos_agent.py                # read-only mode (--enable not set)
    python scripts/chaos_agent.py --enable       # enables POST /run
    python scripts/chaos_agent.py --enable --port 8099 --api http://localhost:8010

Endpoints (CORS: Access-Control-Allow-Origin: http://localhost:5173):
  GET  /status      {enabled, api, running, host}
  GET  /scenarios   [{name, label, kind, n}, ...]
  POST /run         {scenario} -> starts fault in background, returns {ok, scenario}
  POST /abort       signals running fault to stop (best-effort)

The agent only binds to 127.0.0.1; it cannot be reached from other hosts.
Destructive endpoints: POST /run with --enable. No auth needed beyond that
restriction + localhost binding.
"""
from __future__ import annotations

import argparse
import http.server
import json
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# Scenario registry (name -> (label, kind, n))
# kind: "worker_kill" | "redis_restart" | "redis_kill"
SCENARIOS: dict[str, tuple[str, str, int | None]] = {
    "kill1": ("kill 1 of 4 workers", "worker_kill", 1),
    "kill2": ("kill 2 of 4 workers", "worker_kill", 2),
    "kill3": ("kill 3 of 4 workers", "worker_kill", 3),
    "redis_restart": ("graceful redis restart", "redis_restart", None),
    "redis_kill": ("hard redis kill", "redis_kill", None),
}

ap = argparse.ArgumentParser(description="chaos_agent: localhost fault-injection server")
ap.add_argument("--enable", action="store_true", help="enable destructive POST /run endpoint")
ap.add_argument("--port", type=int, default=8099)
ap.add_argument("--api", default="http://localhost:8010", help="cloud API for timeline events")
a = ap.parse_args()

# Shared state
_running_lock = threading.Lock()
_running: str | None = None   # name of currently running scenario, or None
_abort = threading.Event()


# ---- helpers ----------------------------------------------------------------

def _compose(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", "compose", *args], cwd=REPO, capture_output=True, text=True, check=False)


def _docker(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", *args], cwd=REPO, capture_output=True, text=True, check=False)


def _running_worker_ids() -> list[str]:
    r = _compose("ps", "-q", "--status", "running", "worker")
    return [x for x in r.stdout.strip().splitlines() if x]


def _post_timeline(type_: str, detail: dict) -> None:
    """Best-effort: post a chaos event to the cloud API timeline."""
    try:
        body = json.dumps({"type": type_, "source": "chaos_agent", "detail": detail}).encode()
        req = urllib.request.Request(a.api + "/api/cloud/timeline", data=body,
                                     headers={"Content-Type": "application/json"}, method="POST")
        urllib.request.urlopen(req, timeout=3)
    except Exception:  # noqa: BLE001
        pass


def _inject_fault(scenario: str) -> None:
    """Execute the fault. Runs in a daemon thread. Posts timeline events."""
    global _running
    label, kind, n = SCENARIOS[scenario]
    _post_timeline("chaos_fault", {"scenario": scenario, "label": label, "kind": kind})
    try:
        if kind == "worker_kill":
            ids = _running_worker_ids()
            victims = ids[-n:] if n and len(ids) >= n else ids[:1]
            if victims:
                _docker("kill", *victims)
        elif kind == "redis_restart":
            _compose("restart", "redis")
        elif kind == "redis_kill":
            rid = _compose("ps", "-q", "redis").stdout.strip().split()
            if rid:
                _docker("kill", *rid)
            time.sleep(1)
            _compose("up", "-d", "redis")
        _post_timeline("chaos_recover", {"scenario": scenario, "label": label})
    except Exception as exc:  # noqa: BLE001
        _post_timeline("chaos_fault", {"scenario": scenario, "error": str(exc)})
    finally:
        with _running_lock:
            _running = None


# ---- HTTP handler -----------------------------------------------------------

import re as _re
_CORS_ALLOW = _re.compile(r"^http://(localhost|127\.0\.0\.1)(:\d+)?$")


class _Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):  # silence request logs
        pass

    def _origin(self) -> str:
        o = self.headers.get("Origin", "")
        return o if _CORS_ALLOW.match(o) else "http://localhost:5173"

    def _send(self, code: int, body: dict) -> None:
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Access-Control-Allow-Origin", self._origin())
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        # Chrome Private Network Access: allow localhost pages to reach 127.0.0.1
        self.send_header("Access-Control-Allow-Private-Network", "true")
        self.end_headers()
        self.wfile.write(data)

    def do_OPTIONS(self):  # pre-flight
        self._send(204, {})

    def do_GET(self):
        if self.path == "/status":
            with _running_lock:
                r = _running
            self._send(200, {"enabled": a.enable, "api": a.api, "running": r, "host": "127.0.0.1"})
        elif self.path == "/scenarios":
            out = [{"name": k, "label": v[0], "kind": v[1], "n": v[2]} for k, v in SCENARIOS.items()]
            self._send(200, out)
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body: dict = {}
        if length:
            try:
                body = json.loads(self.rfile.read(length))
            except Exception:  # noqa: BLE001
                pass

        if self.path == "/run":
            if not a.enable:
                self._send(403, {"ok": False, "error": "agent started without --enable; destructive ops disabled"})
                return
            scenario = body.get("scenario", "")
            if scenario not in SCENARIOS:
                self._send(422, {"ok": False, "error": f"unknown scenario '{scenario}'"})
                return
            with _running_lock:
                if _running:
                    self._send(409, {"ok": False, "error": f"already running '{_running}'"})
                    return
                _running = scenario  # type: ignore[assignment]
                # update module-level variable
                globals()["_running"] = scenario
            _abort.clear()
            t = threading.Thread(target=_inject_fault, args=(scenario,), daemon=True)
            t.start()
            self._send(200, {"ok": True, "scenario": scenario, "label": SCENARIOS[scenario][0]})

        elif self.path == "/abort":
            _abort.set()
            with _running_lock:
                r = _running
            self._send(200, {"ok": True, "was_running": r})

        else:
            self._send(404, {"error": "not found"})


# ---- entry point ------------------------------------------------------------

if __name__ == "__main__":
    srv = http.server.HTTPServer(("127.0.0.1", a.port), _Handler)
    mode = "ENABLED (--enable)" if a.enable else "READ-ONLY (--enable to allow /run)"
    print(f"chaos_agent listening on http://127.0.0.1:{a.port}  mode={mode}  api={a.api}", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nchaos_agent stopped", flush=True)
        sys.exit(0)
