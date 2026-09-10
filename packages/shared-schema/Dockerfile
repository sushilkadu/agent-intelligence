# Build context is packages/shared-schema itself (docker-compose.yml
# sets `context: ./packages/shared-schema`) -- this package has no
# relative-path installs of sibling packages, so it needs no repo-root
# context.
#
# python:3.11-slim, matching every other service image in this repo --
# see services/crawler-service/Dockerfile's comment on why 3.11.
FROM python:3.11-slim

WORKDIR /app
COPY . /app

# Installs shared_schema itself (editable) plus its [migrations]
# extra (sqlalchemy, alembic, psycopg2-binary) -- everything
# `alembic upgrade head` needs, nothing more.
RUN pip install --no-cache-dir -e ".[migrations]"

CMD ["alembic", "upgrade", "head"]
