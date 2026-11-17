FROM python:3.11-slim

LABEL maintainer="Miss Sona"
LABEL description="Industrial-grade Telegram Group Management Bot — Miss Sona"

# System deps: Tesseract OCR binary for AI-OCR Shield
RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr \
    tesseract-ocr-eng \
    && rm -rf /var/lib/apt/lists/* \
    && apt-get clean

WORKDIR /app

# Python deps (cached layer)
COPY requirements.txt ./
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Bot code
COPY miss_sona.py ./

# Env defaults (override via --env-file)
ENV DB_PATH=/app/data/miss_sona.db
ENV LOG_LEVEL=INFO

# Persist DB outside container
VOLUME ["/app/data"]

CMD ["python", "miss_sona.py"]
