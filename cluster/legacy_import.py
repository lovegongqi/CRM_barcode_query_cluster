import hashlib
import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from cluster.db import Database
from cluster.objects import ObjectRecord, R2ObjectStore


@dataclass
class MigrationReport:
    run_id: str
    source_node: str
    dry_run: bool = False
    verified: bool = False
    source_counts: dict = field(default_factory=dict)
    imported: dict = field(default_factory=dict)
    skipped_matching: int = 0
    source_hashes: dict = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


class LegacyImporter:
    def __init__(
        self,
        data_dir: str | Path,
        catalog,
        objects: R2ObjectStore,
        source_node: str,
        report_dir: str | Path | None = None,
        parse_html=None,
        record_builder=None,
    ):
        self.data_dir = Path(data_dir)
        self.catalog = catalog
        self.objects = objects
        self.source_node = source_node
        self.report_dir = Path(report_dir) if report_dir else None
        self.parse_html = parse_html
        self.record_builder = record_builder

    @staticmethod
    def source_hash(path: str | Path) -> str:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()

    def inventory(self) -> dict:
        config_files = sorted((self.data_dir / "config").glob("*.json"))
        active_html = sorted((self.data_dir / "barcode").glob("*.html"))
        archived_html = sorted((self.data_dir / "barcode" / "archived").glob("*.html"))
        result_files = self._files_under(self.data_dir / "results")
        export_files = sorted((self.data_dir / "barcode").glob("*.xlsx"))
        all_files = config_files + active_html + archived_html + result_files + export_files
        return {
            "counts": {
                "config_json": len(config_files),
                "barcode_html": len(active_html) + len(archived_html),
                "active_barcode_html": len(active_html),
                "archived_barcode_html": len(archived_html),
                "result_files": len(result_files),
                "export_files": len(export_files),
            },
            "hashes": {
                path.relative_to(self.data_dir).as_posix(): self.source_hash(path)
                for path in all_files
            },
            "config_files": config_files,
            "active_html": active_html,
            "archived_html": archived_html,
            "result_files": result_files,
            "export_files": export_files,
        }

    def run(self, dry_run: bool = False) -> MigrationReport:
        inventory = self.inventory()
        report = MigrationReport(
            run_id=str(uuid.uuid4()),
            source_node=self.source_node,
            dry_run=dry_run,
            source_counts=inventory["counts"],
            source_hashes=inventory["hashes"],
        )
        if dry_run:
            return report

        self._record_run(report, "running")
        metadata = self._load_barcode_metadata(inventory["config_files"], report)
        self._import_config(inventory["config_files"], report)
        for path in inventory["active_html"]:
            self._import_barcode_html(path, False, metadata, report)
        for path in inventory["archived_html"]:
            self._import_barcode_html(path, True, metadata, report)
        for path in inventory["result_files"]:
            self._import_asset("results-assets", path, report)
        for path in inventory["export_files"]:
            self._import_asset("exports", path, report)

        report.verified = not report.errors
        self._record_run(report, "succeeded" if report.verified else "failed")
        self._write_report(report)
        return report

    def verify(self, run_id: str) -> MigrationReport:
        inventory = self.inventory()
        report = MigrationReport(
            run_id=run_id,
            source_node=self.source_node,
            source_counts=inventory["counts"],
            source_hashes=inventory["hashes"],
        )
        for path in inventory["active_html"] + inventory["archived_html"]:
            barcode = path.stem
            expected_hash = self.source_hash(path)
            object_key = f"results/{path.name}"
            problems = []
            remote = self.objects.head(object_key)
            if not remote:
                problems.append("R2 对象缺失")
            elif remote.sha256 != expected_hash:
                problems.append("R2 哈希不一致")
            row = self.catalog.get_barcode(barcode)
            if not row:
                problems.append("PostgreSQL 条码记录缺失")
            elif row.get("object_sha256") != expected_hash:
                problems.append("PostgreSQL 哈希不一致")
            if problems:
                report.errors.append(f"{barcode}: " + "，".join(problems))
        report.verified = not report.errors
        self._record_run(report, "verified" if report.verified else "failed")
        self._write_report(report)
        return report

    @staticmethod
    def _files_under(directory: Path) -> list[Path]:
        if not directory.exists():
            return []
        return sorted(path for path in directory.rglob("*") if path.is_file())

    @staticmethod
    def _read_json(path: Path):
        return json.loads(path.read_text(encoding="utf-8"))

    def _load_barcode_metadata(self, config_files: list[Path], report: MigrationReport) -> dict:
        path = next((item for item in config_files if item.name == "barcode_data.json"), None)
        if not path:
            return {}
        try:
            value = self._read_json(path)
            return value if isinstance(value, dict) else {}
        except Exception as error:
            report.errors.append(f"读取 {path.name} 失败: {error}")
            return {}

    def _import_config(self, config_files: list[Path], report: MigrationReport) -> None:
        for path in config_files:
            try:
                value = self._read_json(path)
                if path.name == "runtime_config.json" and isinstance(value, dict):
                    self.catalog.set_runtime_config("global", value)
                elif path.name == "product_library.json" and isinstance(value, dict):
                    self.catalog.replace_product_rules(list(value.values()))
                elif path.name == "distributor_history.json" and isinstance(value, list):
                    self.catalog.upsert_distributors(value)
                elif path.name == "distributor_history_deleted.json" and isinstance(value, list):
                    self.catalog.set_deleted_distributors(value)
                elif path.name == "accounts.json" and isinstance(value, list):
                    self.catalog.replace_accounts(value)
                elif path.name == "crm_credentials.json" and isinstance(value, dict):
                    for owner_key, row in value.items():
                        if isinstance(row, dict):
                            self.catalog.save_credentials(
                                owner_key,
                                bool(row.get("remember")),
                                str(row.get("username") or ""),
                                str(row.get("password") or ""),
                            )
                report.imported["config_json"] = report.imported.get("config_json", 0) + 1
            except Exception as error:
                report.errors.append(f"导入 {path.name} 失败: {error}")

    def _import_barcode_html(
        self,
        path: Path,
        archived: bool,
        metadata_by_barcode: dict,
        report: MigrationReport,
    ) -> None:
        barcode = path.stem
        source_sha256 = self.source_hash(path)
        object_key = f"results/{path.name}"
        remote = self.objects.head(object_key)
        if remote and remote.sha256 == source_sha256:
            object_record = remote
            report.skipped_matching += 1
        else:
            object_record = self.objects.put_file("results", path, path.name, "text/html")
        if object_record.sha256 != source_sha256:
            report.errors.append(f"{barcode}: 上传后 R2 哈希不一致")
            return

        metadata = dict(metadata_by_barcode.get(barcode) or {})
        metadata["archived"] = bool(metadata.get("archived") or archived)
        fields = self.parse_html(path) if self.parse_html else {}
        extra = self.record_builder(barcode, fields, metadata) if self.record_builder else {}
        payload = dict(extra or {})
        payload.update(
            {
                "barcode": barcode,
                "object_key": object_record.object_key,
                "object_sha256": source_sha256,
                "fields": fields,
                "remark": str(metadata.get("remark") or ""),
                "archived": metadata["archived"],
                "archive_time": metadata.get("archiveTime") or None,
                "current_dealer_override": str(metadata.get("currentDealerOverride") or ""),
                "transfer_updated_at": metadata.get("transferUpdatedAt") or None,
                "query_slot_id": str(metadata.get("querySlotId") or ""),
                "query_updated_at": metadata.get("queryUpdatedAt") or None,
                "metadata": metadata,
            }
        )
        self.catalog.upsert_barcode(payload)
        report.imported["barcode_html"] = report.imported.get("barcode_html", 0) + 1

    def _import_asset(self, category: str, path: Path, report: MigrationReport) -> None:
        relative_name = path.relative_to(self.data_dir).as_posix().replace("/", "_")
        object_key = f"{category}/{relative_name}"
        source_sha256 = self.source_hash(path)
        remote = self.objects.head(object_key)
        if remote and remote.sha256 == source_sha256:
            report.skipped_matching += 1
            return
        content_type = (
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            if path.suffix.lower() == ".xlsx"
            else "application/octet-stream"
        )
        uploaded = self.objects.put_file(category, path, relative_name, content_type)
        if uploaded.sha256 != source_sha256:
            report.errors.append(f"{path.name}: 上传后 R2 哈希不一致")
            return
        report.imported[category] = report.imported.get(category, 0) + 1

    def _record_run(self, report: MigrationReport, status: str) -> None:
        database = getattr(self.catalog, "db", None)
        if not isinstance(database, Database):
            return
        now = datetime.now(timezone.utc)
        database.execute(
            """
            INSERT INTO migration_runs(
                id, source_node, status, counts_json, hashes_json,
                started_at, finished_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (id) DO UPDATE SET
                status = EXCLUDED.status,
                counts_json = EXCLUDED.counts_json,
                hashes_json = EXCLUDED.hashes_json,
                finished_at = EXCLUDED.finished_at
            """,
            (
                report.run_id,
                report.source_node,
                status,
                json.dumps(report.source_counts, ensure_ascii=False),
                json.dumps(report.source_hashes, ensure_ascii=False),
                now,
                None if status == "running" else now,
            ),
        )

    def _write_report(self, report: MigrationReport) -> None:
        if not self.report_dir:
            return
        self.report_dir.mkdir(parents=True, exist_ok=True)
        target = self.report_dir / f"{report.run_id}.json"
        temporary = target.with_suffix(".json.part")
        temporary.write_text(
            json.dumps(report.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.replace(target)
