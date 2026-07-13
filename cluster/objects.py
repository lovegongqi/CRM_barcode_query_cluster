import hashlib
import os
from dataclasses import dataclass
from pathlib import Path

import boto3

from cluster.config import ClusterConfig
from cluster.db import Database


@dataclass(frozen=True)
class ObjectRecord:
    object_key: str
    category: str
    sha256: str
    size_bytes: int
    content_type: str


class R2ObjectStore:
    def __init__(
        self,
        config: ClusterConfig,
        database: Database | None = None,
        client=None,
    ):
        self.bucket = config.r2_bucket
        self.database = database
        self.client = client or boto3.client(
            "s3",
            endpoint_url=config.r2_endpoint_url,
            aws_access_key_id=config.r2_access_key_id,
            aws_secret_access_key=config.r2_secret_access_key,
            region_name="auto",
        )

    def put_file(
        self,
        category: str,
        source_path: str | Path,
        object_name: str,
        content_type: str,
    ) -> ObjectRecord:
        data = Path(source_path).read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        object_key = f"{category.strip('/')}/{object_name.lstrip('/')}"
        self.client.put_object(
            Bucket=self.bucket,
            Key=object_key,
            Body=data,
            ContentType=content_type,
            Metadata={"sha256": digest},
        )
        record = ObjectRecord(
            object_key=object_key,
            category=category.strip("/"),
            sha256=digest,
            size_bytes=len(data),
            content_type=content_type,
        )
        if self.database:
            self.database.execute(
                """
                INSERT INTO object_records(
                    object_key, category, sha256, size_bytes,
                    content_type, created_at, deleted_at
                ) VALUES (%s, %s, %s, %s, %s, now(), NULL)
                ON CONFLICT (object_key) DO UPDATE SET
                    category = EXCLUDED.category,
                    sha256 = EXCLUDED.sha256,
                    size_bytes = EXCLUDED.size_bytes,
                    content_type = EXCLUDED.content_type,
                    created_at = now(),
                    deleted_at = NULL
                """,
                (
                    record.object_key,
                    record.category,
                    record.sha256,
                    record.size_bytes,
                    record.content_type,
                ),
            )
        return record

    def get_bytes(self, object_key: str) -> bytes:
        response = self.client.get_object(Bucket=self.bucket, Key=object_key)
        return response["Body"].read()

    def cache_file(
        self,
        object_key: str,
        target_path: str | Path,
        expected_sha256: str,
    ) -> Path:
        target = Path(target_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(target.suffix + ".part")
        try:
            data = self.get_bytes(object_key)
            temporary.write_bytes(data)
            actual_sha256 = hashlib.sha256(data).hexdigest()
            if actual_sha256 != expected_sha256:
                raise ValueError("R2 文件 SHA-256 校验失败")
            os.replace(temporary, target)
            return target
        finally:
            temporary.unlink(missing_ok=True)

    def delete(self, object_key: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=object_key)
        if self.database:
            self.database.execute(
                """
                UPDATE object_records
                SET deleted_at = now()
                WHERE object_key = %s
                """,
                (object_key,),
            )
