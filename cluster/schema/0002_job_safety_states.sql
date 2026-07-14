ALTER TABLE job_items
    DROP CONSTRAINT job_items_status_check;

ALTER TABLE job_items
    ADD CONSTRAINT job_items_status_check CHECK (
        status IN (
            'pending',
            'leased',
            'running',
            'submitted_to_crm',
            'succeeded',
            'failed',
            'cancelled',
            'skipped',
            'needs_review'
        )
    );
