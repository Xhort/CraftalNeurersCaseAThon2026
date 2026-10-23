FROM python:3.14-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app/lade_eta_lab

COPY lade_eta_lab/requirements-production.txt ./requirements-production.txt
RUN pip install --no-cache-dir --requirement requirements-production.txt

COPY lade_eta_lab/ ./
COPY index.html /app/index.html

EXPOSE 8080

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "1"]
