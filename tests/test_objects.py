import hashlib
import io
from dataclasses import replace

import pytest

from cluster.config import ClusterConfig
from cluster.objects import R2ObjectStore


class FakeS3:
    def __init__(self):
        self.objects = {}

    def put_object(self, Bucket, Key, Body, ContentType, Metadata):
        self.objects[(Bucket, Key)] = {
            "body": bytes(Body),
            "content_type": ContentType,
            "metadata": Metadata,
        }

    def get_object(self, Bucket, Key):
        return {"Body": io.BytesIO(self.objects[(Bucket, Key)]["body"])}

    def delete_object(self, Bucket, Key):
        self.objects.pop((Bucket, Key), None)


@pytest.fixture()
def object_config():
    base = ClusterConfig.from_env({})
    return replace(
        base,
        r2_endpoint_url="https://example.r2.cloudflarestorage.com",
        r2_bucket="crm-test",
        r2_access_key_id="key",
        r2_secret_access_key="secret",
    )


def test_r2_upload_records_sha256(tmp_path, object_config):
    source = tmp_path / "5312503010858.html"
    source.write_bytes(b"result")
    fake_s3 = FakeS3()

    record = R2ObjectStore(object_config, client=fake_s3).put_file(
        "results",
        source,
        source.name,
        "text/html",
    )

    assert record.sha256 == hashlib.sha256(b"result").hexdigest()
    assert record.object_key == "results/5312503010858.html"
    assert fake_s3.objects[("crm-test", record.object_key)]["body"] == b"result"


def test_download_cache_rejects_hash_mismatch(tmp_path, object_config):
    fake_s3 = FakeS3()
    fake_s3.objects[("crm-test", "results/a.html")] = {
        "body": b"changed",
        "content_type": "text/html",
        "metadata": {},
    }
    target = tmp_path / "cache" / "a.html"
    store = R2ObjectStore(object_config, client=fake_s3)

    with pytest.raises(ValueError, match="SHA-256"):
        store.cache_file("results/a.html", target, hashlib.sha256(b"original").hexdigest())

    assert not target.exists()
    assert not target.with_suffix(".html.part").exists()


def test_delete_is_idempotent(object_config):
    fake_s3 = FakeS3()
    store = R2ObjectStore(object_config, client=fake_s3)

    store.delete("results/missing.html")
    store.delete("results/missing.html")
