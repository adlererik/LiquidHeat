FROM docker.io/library/nginx:1.28.3-alpine@sha256:a8b39bd9cf0f83869a2162827a0caf6137ddf759d50a171451b335cecc87d236
COPY LICENSE NOTICE /usr/share/doc/liquidheat/
COPY nginx.conf /etc/nginx/nginx.conf
COPY frontend /usr/share/nginx/html
RUN rm -f /usr/share/nginx/html/Dockerfile && chown -R nginx:nginx /usr/share/nginx/html
USER nginx
EXPOSE 8080

LABEL org.opencontainers.image.licenses="Apache-2.0 AND BSD-2-Clause"

LABEL org.opencontainers.image.title="LiquidHeat" \
      org.opencontainers.image.authors="Erik Adler" \
      org.opencontainers.image.source="https://github.com/adlererik/LiquidHeat" \
      org.opencontainers.image.version="1.3.0"
