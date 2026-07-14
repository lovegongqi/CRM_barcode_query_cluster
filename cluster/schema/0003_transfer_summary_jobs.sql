ALTER TABLE jobs
    DROP CONSTRAINT jobs_type_check;

ALTER TABLE jobs
    ADD CONSTRAINT jobs_type_check CHECK (
        type IN ('query', 'transfer', 'transfer_summary', 'service_close', 'library_lookup')
    );
