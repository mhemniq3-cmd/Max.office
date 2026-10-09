FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . /app/cloud_server

ENV PYTHONPATH=/app
ENV PORT=8000
EXPOSE 8000

CMD ["python", "cloud_server/run_server.py"]
