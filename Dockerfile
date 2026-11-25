# Stage 1: Build rmapi (ddvk fork supporting modern reMarkable Cloud sync)
FROM golang:bookworm AS rmapi-builder

RUN git clone --depth 1 https://github.com/ddvk/rmapi.git /src/rmapi \
    && cd /src/rmapi \
    && CGO_ENABLED=0 go build -ldflags="-s -w" -o /out/rmapi .

# Stage 2: Python runtime with serif and Unicode fallback fonts
FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    OUTPUT_DIR=/app/pdfs \
    RMAPI_BIN=/usr/local/bin/rmapi \
    XDG_CONFIG_HOME=/config

RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates \
        fonts-dejavu-core \
        fonts-liberation2 \
        tzdata \
    && rm -rf /var/lib/apt/lists/*

COPY --from=rmapi-builder /out/rmapi /usr/local/bin/rmapi

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY make_paper.py .

RUN mkdir -p /app/pdfs /config/rmapi

VOLUME ["/app/pdfs", "/config/rmapi"]

CMD ["python3", "/app/make_paper.py"]
