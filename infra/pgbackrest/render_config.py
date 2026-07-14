#!/usr/bin/env python3
import os
from pathlib import Path


TEMPLATE = Path("/etc/pgbackrest/pgbackrest.conf.tpl")
OUTPUT = Path("/etc/pgbackrest/pgbackrest.conf")
REQUIRED = (
    "R2_BUCKET",
    "R2_S3_ENDPOINT",
    "R2_ACCESS_KEY_ID",
    "R2_SECRET_ACCESS_KEY",
)


def main() -> None:
    missing = [name for name in REQUIRED if not os.environ.get(name)]
    if missing:
        raise SystemExit("missing pgBackRest environment: " + ", ".join(missing))
    text = TEMPLATE.read_text(encoding="utf-8")
    for name in REQUIRED:
        text = text.replace(f"__{name}__", os.environ[name])
    OUTPUT.write_text(text, encoding="utf-8")
    OUTPUT.chmod(0o600)


if __name__ == "__main__":
    main()
