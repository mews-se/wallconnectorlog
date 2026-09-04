FROM python:3.13-alpine

RUN adduser -D -u 1000 app && apk add --no-cache su-exec
WORKDIR /app
COPY entrypoint.sh wallconnectorlog.py ./
RUN chmod 755 entrypoint.sh

# The Grafana link's default lives here, so the compose file only has to name
# WC_GRAFANA_URL and .env decides whether to override it.
ENV WC_DB=/data/wallconnectorlog.db WC_PORT=4680 WC_GRAFANA_URL=:3399
VOLUME /data
EXPOSE 4680

# Root only long enough for the entrypoint to fix bind-mount ownership;
# it drops to the app user before starting anything.
ENTRYPOINT ["/app/entrypoint.sh"]

# Reads WC_PORT so the check follows the configured port.
HEALTHCHECK --interval=60s --timeout=5s --start-period=30s \
  CMD python -c "import os,sys,urllib.request; \
      p=os.environ.get('WC_PORT','4680'); \
      sys.exit(0 if urllib.request.urlopen(f'http://127.0.0.1:{p}/healthz',timeout=4).status==200 else 1)"

CMD ["python", "-u", "wallconnectorlog.py"]
