FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY tracker ./tracker

# One process only: all state lives in memory, and a second replica would double the Kalshi request rate.
# Railway sets $PORT; default to 8000 elsewhere.
CMD ["sh", "-c", "exec uvicorn tracker.app:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips='*'"]
