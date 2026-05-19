FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && python -m playwright install --with-deps chromium

COPY . .

RUN mkdir -p /app/barcode /app/results /app/session

EXPOSE 5001

CMD ["gunicorn", "-w", "1", "--threads", "4", "--timeout", "300", "--bind", "0.0.0.0:5001", "app:app"]
