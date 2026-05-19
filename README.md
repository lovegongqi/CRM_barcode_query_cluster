# Barcode CRM Query Tool

Flask + Playwright tool for CRM barcode lookup and local result management.

## Setup

1. Install dependencies:

```bash
pip install -r requirements.txt
playwright install chromium
```

2. Create local config files:

```bash
cp config.example.json config.json
cp accounts.example.json accounts.json
```

3. Fill in `config.json` and `accounts.json` locally.

## Run

```bash
python app.py
```

Open:

```text
http://127.0.0.1:5001/
http://127.0.0.1:5001/crm
```

## Docker

Create local runtime files:

```bash
cp config.docker.example.json config.json
cp accounts.example.json accounts.json
mkdir -p barcode results session
```

Edit `config.json` and `accounts.json`, then run:

```bash
docker compose up -d --build
```

Open:

```text
http://SERVER_IP:5001/
http://SERVER_IP:5001/crm
```

For a multi-architecture image that works on common x86_64 and ARM64 cloud servers:

```bash
docker buildx create --use --name multiarch-builder
docker buildx build \
  --platform linux/amd64,linux/arm64 \
  -t YOUR_DOCKERHUB_USER/crm-barcode-query:latest \
  --push .
```

Then on the server:

```bash
# Change docker-compose.yml image to YOUR_DOCKERHUB_USER/crm-barcode-query:latest first.
docker compose pull
docker compose up -d
```

The Docker image does not include local credentials, browser sessions, or query output. These are mounted through `docker-compose.yml`.

## Notes

Generated barcode HTML files, exported spreadsheets, browser sessions, logs, and local credentials are intentionally ignored by Git.
