# Apache-only TimescaleDB. No Timescale License (tsl) module is built or shipped.
FROM docker.io/library/postgres:16.15-bookworm@sha256:d81f7b51697014367236d07ec58b54153fc28f828b72bf247fe5c7487520bcb0 AS build
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl cmake build-essential postgresql-server-dev-16 libssl-dev && rm -rf /var/lib/apt/lists/*
WORKDIR /build
RUN curl -fsSL https://codeload.github.com/timescale/timescaledb/tar.gz/refs/tags/2.19.3 -o source.tar.gz \
 && echo 'ef2c9019f3e292f77e274a95386e550cb8c358ec0e053f6b7097565f61381e01  source.tar.gz' | sha256sum -c - \
 && tar xzf source.tar.gz --strip-components=1 \
 && ./bootstrap -DAPACHE_ONLY=1 -DUSE_TELEMETRY=OFF -DSEND_TELEMETRY_DEFAULT=OFF -DREGRESS_CHECKS=OFF -DWARNINGS_AS_ERRORS=OFF \
 && cmake --build build --parallel 1 \
 && cmake --install build \
 && test -z "$(find /usr/lib/postgresql/16/lib -name '*timescale*tsl*')"
FROM docker.io/library/postgres:16.15-bookworm@sha256:d81f7b51697014367236d07ec58b54153fc28f828b72bf247fe5c7487520bcb0
COPY LICENSE NOTICE /usr/share/doc/liquidheat/
COPY --from=build /usr/lib/postgresql/16/lib/timescaledb* /usr/lib/postgresql/16/lib/
COPY --from=build /usr/share/postgresql/16/extension/timescaledb* /usr/share/postgresql/16/extension/
COPY --from=build /build/LICENSE /usr/share/doc/timescaledb/LICENSE
COPY --from=build /build/LICENSE-APACHE /usr/share/doc/timescaledb/LICENSE-APACHE

LABEL org.opencontainers.image.licenses="Apache-2.0 AND PostgreSQL"

LABEL org.opencontainers.image.title="LiquidHeat" \
      org.opencontainers.image.authors="Erik Adler" \
      org.opencontainers.image.source="https://github.com/adlererik/LiquidHeat" \
      org.opencontainers.image.version="1.0.0"
