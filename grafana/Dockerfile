FROM grafana/grafana:latest

# The plugin must live outside /var/lib/grafana — that is the data volume, and
# a fresh volume would shadow anything the image put there.
USER root
RUN mkdir -p /opt/grafana-plugins && \
    grafana cli --pluginsDir /opt/grafana-plugins plugins install frser-sqlite-datasource
USER 472

COPY provisioning /etc/grafana/provisioning
COPY dashboards /var/lib/grafana-dashboards

# PREINSTALL_DISABLED stops the background download of Grafana's own app
# plugins (Pyroscope, Loki explore, …) — pointless here, and they cannot
# write to the root-owned plugin dir anyway.
ENV GF_PATHS_PLUGINS=/opt/grafana-plugins \
    GF_PLUGINS_PREINSTALL_DISABLED=true \
    GF_AUTH_ANONYMOUS_ENABLED=true \
    GF_AUTH_ANONYMOUS_ORG_ROLE=Viewer \
    GF_AUTH_DISABLE_LOGIN_FORM=true \
    GF_AUTH_BASIC_ENABLED=false \
    GF_DASHBOARDS_DEFAULT_HOME_DASHBOARD_PATH=/var/lib/grafana-dashboards/wallconnectorlog.json
