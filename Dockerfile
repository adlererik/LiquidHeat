FROM docker.io/library/python:3.12.14-slim-bookworm@sha256:392307d22300de8b5986851a12d9176dfc0fc073e65bf6523ebd7dcbeb23564e
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1
WORKDIR /srv
COPY requirements.lock .
RUN pip install --no-cache-dir -r requirements.lock && useradd -r -u 10001 app
COPY LICENSE NOTICE ./
COPY app ./app
COPY migrations ./migrations
COPY scripts ./scripts
COPY tests ./tests
USER 10001
CMD ["python","-m","app.engine"]

LABEL org.opencontainers.image.licenses="Apache-2.0"

LABEL org.opencontainers.image.title="LiquidHeat" \
      org.opencontainers.image.authors="Erik Adler" \
      org.opencontainers.image.source="https://github.com/adlererik/LiquidHeat" \
      org.opencontainers.image.version="1.3.0"
