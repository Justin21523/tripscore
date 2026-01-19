FROM python:3.10-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN useradd -m -u 10001 appuser

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY src /app/src
COPY data /app/data
COPY scripts /app/scripts

ENV PYTHONPATH=/app/src \
    TRIPSCORE_CACHE_DIR=/app/.cache/tripscore

RUN mkdir -p /app/.cache/tripscore && chown -R appuser:appuser /app

USER appuser

EXPOSE 8000

CMD ["uvicorn", "tripscore.api.app:app", "--host", "0.0.0.0", "--port", "8000"]

