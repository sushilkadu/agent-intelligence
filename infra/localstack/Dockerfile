# Build context is infra/localstack itself (docker-compose.yml sets
# `context: ./infra/localstack`) -- this one-shot setup script has no
# dependency on packages/ or any service code, just boto3.
FROM python:3.11-slim

WORKDIR /app
COPY setup.py /app/setup.py
RUN pip install --no-cache-dir "boto3>=1.34,<2.0"

CMD ["python", "setup.py"]
