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

## Notes

Generated barcode HTML files, exported spreadsheets, browser sessions, logs, and local credentials are intentionally ignored by Git.
