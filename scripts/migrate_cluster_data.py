#!/usr/bin/env python3
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from cluster.config import ClusterConfig
from cluster.legacy_import import LegacyImporter
from cluster.services import build_cluster_services


def _record_builder(barcode, fields, metadata):
    import app

    info = app._barcode_product_info(
        {
            "barcode": barcode,
            "fields": fields,
            "currentDealerOverride": metadata.get("currentDealerOverride") or "",
        }
    )
    return {
        "product_name": info.get("product_name") or "",
        "product_code": info.get("product_code") or "",
        "current_dealer": info.get("current_dealer") or "",
        "service_dealer": info.get("service_dealer") or "",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="迁移 CRM 旧 JSON/HTML 到 PostgreSQL 和 R2")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--apply", action="store_true")
    parser.add_argument("--verify", action="store_true")
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--report-dir", default="migration-reports")
    parser.add_argument("--source-node", default="nas")
    args = parser.parse_args()

    config = ClusterConfig.from_env()
    services = build_cluster_services(config)
    try:
        import app

        importer = LegacyImporter(
            args.data_dir,
            services.catalog,
            services.objects,
            source_node=args.source_node,
            report_dir=args.report_dir,
            parse_html=lambda path: app.extract_fields_from_html(str(path)),
            record_builder=_record_builder,
        )
        report = importer.run(dry_run=args.dry_run)
        if args.apply and args.verify and not report.errors:
            report = importer.verify(report.run_id)
        print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
        return 0 if not report.errors and (args.dry_run or not args.verify or report.verified) else 1
    finally:
        services.close()


if __name__ == "__main__":
    raise SystemExit(main())
