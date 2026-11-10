"""FLUSHDB the queue's Redis."""
import _common  # noqa: F401
from backend.cloud.queue import Queue

Queue().r.flushdb()
print("redis flushed")
