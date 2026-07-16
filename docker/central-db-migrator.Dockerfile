FROM python:3.12-slim

WORKDIR /app
COPY requirements-central-db.txt /tmp/requirements-central-db.txt
RUN pip install --no-cache-dir -r /tmp/requirements-central-db.txt

ENTRYPOINT ["python", "/app/tools/migrate_sqlite_to_postgres.py"]
