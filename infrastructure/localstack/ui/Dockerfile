# MarketPulse — LocalStack console (read-only boto3 dashboard). Dev tool only.
FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py index.html ./

EXPOSE 8080
CMD ["python", "app.py"]
