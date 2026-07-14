local all all trust
hostssl postgres postgres 127.0.0.1/32 scram-sha-256
hostssl crm_barcode crm_app 0.0.0.0/0 scram-sha-256 clientcert=verify-ca
hostssl replication crm_replica 0.0.0.0/0 scram-sha-256 clientcert=verify-ca
host all all 0.0.0.0/0 reject
