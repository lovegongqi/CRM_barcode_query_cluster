FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TZ=Asia/Shanghai

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && python -m playwright install --with-deps chromium

COPY . .

RUN mkdir -p /app/data/barcode /app/data/results /app/session /app/legacy/barcode /app/legacy/results

EXPOSE 5001

CMD ["sh", "-c", "Xvfb :99 -screen 0 1920x1080x24 >/tmp/xvfb.log 2>&1 & export DISPLAY=:99; exec gunicorn -w 1 --threads 4 --timeout 300 --bind 0.0.0.0:5001 app:app"]
