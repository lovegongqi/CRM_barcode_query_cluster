import hashlib
import json
from unittest.mock import Mock

from cluster.legacy_import import LegacyImporter
from cluster.objects import ObjectRecord


def _legacy_tree(tmp_path):
    config_dir = tmp_path / "config"
    barcode_dir = tmp_path / "barcode"
    archive_dir = barcode_dir / "archived"
    config_dir.mkdir()
    archive_dir.mkdir(parents=True)
    (config_dir / "runtime_config.json").write_text(
        json.dumps({"query_workers": 5}, ensure_ascii=False),
        encoding="utf-8",
    )
    (config_dir / "barcode_data.json").write_text(
        json.dumps(
            {
                "5312503010858": {"remark": "active"},
                "5322503310162": {"remark": "archive", "archived": True},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (barcode_dir / "5312503010858.html").write_bytes(b"<html>active</html>")
    (archive_dir / "5322503310162.html").write_bytes(b"<html>archive</html>")
    return tmp_path


def test_dry_run_never_writes(tmp_path):
    source = _legacy_tree(tmp_path)
    catalog = Mock()
    objects = Mock()
    importer = LegacyImporter(source, catalog, objects, source_node="nas")

    report = importer.run(dry_run=True)

    assert report.source_counts["barcode_html"] == 2
    assert report.source_counts["config_json"] == 2
    catalog.upsert_barcode.assert_not_called()
    catalog.set_runtime_config.assert_not_called()
    objects.put_file.assert_not_called()


def test_matching_r2_hash_is_not_uploaded_twice(tmp_path):
    source = _legacy_tree(tmp_path)
    catalog = Mock()
    objects = Mock()
    importer = LegacyImporter(source, catalog, objects, source_node="nas")
    matching_path = source / "barcode" / "5312503010858.html"
    matching_hash = hashlib.sha256(matching_path.read_bytes()).hexdigest()

    def head(object_key):
        if object_key == "results/5312503010858.html":
            return ObjectRecord(object_key, "results", matching_hash, matching_path.stat().st_size, "text/html")
        return None

    objects.head.side_effect = head
    objects.put_file.return_value = ObjectRecord(
        "results/5322503310162.html",
        "results",
        hashlib.sha256(b"<html>archive</html>").hexdigest(),
        len(b"<html>archive</html>"),
        "text/html",
    )

    report = importer.run()

    assert report.skipped_matching == 1
    assert objects.put_file.call_count == 1
    assert catalog.upsert_barcode.call_count == 2
    published = [call.args[0] for call in catalog.upsert_barcode.call_args_list]
    assert {row["barcode"] for row in published} == {"5312503010858", "5322503310162"}
    assert next(row for row in published if row["barcode"] == "5322503310162")["archived"] is True


def test_verify_reports_missing_or_changed_objects(tmp_path):
    source = _legacy_tree(tmp_path)
    catalog = Mock()
    objects = Mock()
    importer = LegacyImporter(source, catalog, objects, source_node="nas")
    objects.head.return_value = None
    catalog.get_barcode.return_value = None

    report = importer.verify("test-run")

    assert report.verified is False
    assert len(report.errors) == 2
