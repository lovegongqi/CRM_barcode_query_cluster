scope: crm-barcode-postgres
namespace: /service/
name: __NODE_ID__

restapi:
  listen: 0.0.0.0:8008
  connect_address: __NODE_HOST__:8008
  certfile: /run/cluster-secrets/patroni-server.crt
  keyfile: /run/cluster-secrets/patroni-server.key
  cafile: /run/cluster-secrets/ca.crt
  verify_client: required

ctl:
  cacert: /run/cluster-secrets/ca.crt
  certfile: /run/cluster-secrets/patroni-client.crt
  keyfile: /run/cluster-secrets/patroni-client.key

etcd3:
  hosts: hk.mlmll.cn:2379,sg.mlmll.cn:2379,us.mlmll.cn:2379
  protocol: https
  cacert: /run/cluster-secrets/ca.crt
  cert: /run/cluster-secrets/etcd-client.crt
  key: /run/cluster-secrets/etcd-client.key

bootstrap:
  dcs:
    ttl: 30
    loop_wait: 10
    retry_timeout: 10
    maximum_lag_on_failover: 1048576
    synchronous_mode: true
    synchronous_mode_strict: false
    postgresql:
      use_pg_rewind: true
      use_slots: true
      parameters:
        password_encryption: scram-sha-256
        ssl: "on"
        ssl_ca_file: /run/cluster-secrets/ca.crt
        ssl_cert_file: /run/cluster-secrets/postgres-server.crt
        ssl_key_file: /run/cluster-secrets/postgres-server.key
        archive_mode: "on"
        archive_command: pgbackrest --stanza=crm-barcode archive-push %p
        archive_timeout: 300s
      pg_hba:
        - local all all trust
        - hostssl postgres postgres 127.0.0.1/32 scram-sha-256
        - hostssl postgres postgres 0.0.0.0/0 scram-sha-256 clientcert=verify-ca
        - hostssl crm_barcode crm_app 0.0.0.0/0 scram-sha-256 clientcert=verify-ca
        - hostssl replication crm_replica 0.0.0.0/0 scram-sha-256 clientcert=verify-ca
        - hostssl postgres crm_rewind 0.0.0.0/0 scram-sha-256 clientcert=verify-ca
        - host all all 0.0.0.0/0 reject
  initdb:
    - encoding: UTF8
    - data-checksums

postgresql:
  listen: 0.0.0.0:5432
  connect_address: __NODE_HOST__:15432
  data_dir: /var/lib/postgresql/data
  bin_dir: /usr/lib/postgresql/16/bin
  pgpass: /run/patroni/pgpass
  authentication:
    replication:
      username: crm_replica
    superuser:
      username: postgres
    rewind:
      username: crm_rewind
  parameters:
    unix_socket_directories: /var/run/postgresql

tags:
  noloadbalance: false
  clonefrom: false
  nosync: __NOSYNC__
  failover_priority: __FAILOVER_PRIORITY__
