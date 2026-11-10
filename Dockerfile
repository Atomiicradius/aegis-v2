FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/repo:/repo/backend \
    OFFLINE=1 \
    EVENT_POOL=pool \
    VALIDATION_SET=bounded \
    LLM_MODE=stub

WORKDIR /repo

COPY backend/requirements.txt backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt

# See .dockerignore: frontend, docs, main.py, orchestrator, storage and
# trajectory.py are deliberately NOT in the image.
COPY backend backend
COPY scripts scripts

CMD ["python", "scripts/smoke.py"]
