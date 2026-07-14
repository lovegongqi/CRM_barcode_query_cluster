from dataclasses import dataclass
from pathlib import Path

from cluster.catalog import CatalogRepository
from cluster.config import ClusterConfig
from cluster.crypto import CredentialCipher
from cluster.db import Database
from cluster.jobs import JobRepository
from cluster.migrations import MigrationRunner
from cluster.objects import R2ObjectStore


@dataclass
class ClusterServices:
    config: ClusterConfig
    database: Database
    catalog: CatalogRepository
    objects: R2ObjectStore
    jobs: JobRepository | None = None

    def publish_barcode_result(
        self,
        barcode: str,
        html_path: str | Path,
        fields: dict,
        metadata: dict | None = None,
    ) -> None:
        record = self.objects.put_file(
            "results",
            html_path,
            f"{barcode}.html",
            "text/html",
        )
        payload = dict(metadata or {})
        payload.update(
            {
                "barcode": barcode,
                "object_key": record.object_key,
                "object_sha256": record.sha256,
                "fields": fields,
            }
        )
        self.catalog.upsert_barcode(payload)

    def close(self) -> None:
        self.database.close()


def build_cluster_services(config: ClusterConfig) -> ClusterServices:
    config.validate()
    database = Database(config.database_url, min_size=1, max_size=10)
    MigrationRunner(database).apply()
    cipher = CredentialCipher(config.credentials_key)
    catalog = CatalogRepository(database, cipher)
    objects = R2ObjectStore(config, database=database)
    jobs = JobRepository(database)
    return ClusterServices(config, database, catalog, objects, jobs)
