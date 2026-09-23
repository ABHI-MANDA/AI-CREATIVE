FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=10000

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements-production.txt requirements.txt
RUN pip install --no-cache-dir -r requirements-production.txt

COPY . .

RUN useradd --create-home --uid 10001 appuser && chown -R appuser:appuser /app
USER appuser

EXPOSE 10000
CMD ["gunicorn", "--worker-class", "gthread", "--threads", "4", "--timeout", "900", "--bind", "0.0.0.0:10000", "backend.app:app"]
