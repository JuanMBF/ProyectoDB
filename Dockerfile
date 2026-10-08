FROM python:3.12-slim AS app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY alembic.ini pyproject.toml ./
COPY migrations ./migrations
COPY sql ./sql
COPY preciosdb ./preciosdb

# Run as an unprivileged user; /app/data holds the SQLite database when MySQL is not configured.
RUN useradd --create-home --uid 1000 precios \
    && mkdir -p /app/data \
    && chown precios:precios /app/data
USER precios

ENTRYPOINT ["python", "-m", "preciosdb"]
CMD ["--help"]


# Same image plus the test dependencies: runs the integration tests against the compose databases.
FROM app AS test

USER root
COPY requirements-dev.txt .
RUN pip install -r requirements-dev.txt
COPY tests ./tests
USER precios

ENTRYPOINT ["python", "-m", "pytest", "-p", "no:cacheprovider"]
CMD ["-m", "integration"]
