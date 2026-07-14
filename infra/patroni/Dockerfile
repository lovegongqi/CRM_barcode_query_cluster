FROM postgres:16-bookworm

ARG PATRONI_VERSION=4.1.4

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates cron curl gettext-base pgbackrest python3-pip \
    && pip3 install --no-cache-dir --break-system-packages \
        "patroni[etcd3]==${PATRONI_VERSION}" "psycopg[binary]>=3.2,<3.3" \
    && rm -rf /var/lib/apt/lists/*

COPY infra/pgbackrest/pgbackrest.conf.tpl /etc/pgbackrest/pgbackrest.conf.tpl
COPY infra/pgbackrest/render_config.py /usr/local/bin/render-pgbackrest-config
COPY infra/pgbackrest/backup_if_primary.sh /usr/local/bin/backup-if-primary
COPY infra/pgbackrest/backup.cron /etc/cron.d/crm-pgbackrest
COPY infra/patroni/entrypoint.sh /usr/local/bin/patroni-entrypoint

RUN chmod 0755 \
        /usr/local/bin/render-pgbackrest-config \
        /usr/local/bin/backup-if-primary \
        /usr/local/bin/patroni-entrypoint \
    && chmod 0644 /etc/cron.d/crm-pgbackrest

ENTRYPOINT ["/usr/local/bin/patroni-entrypoint"]
CMD ["patroni", "/etc/patroni/patroni.yml"]
