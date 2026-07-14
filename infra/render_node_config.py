#!/usr/bin/env python3
import argparse
from pathlib import Path


ROOT = Path(__file__).resolve().parent
TEMPLATE_DIR = ROOT / "templates"
HAPROXY_TEMPLATE = ROOT / "haproxy" / "haproxy.cfg"

NODES = {
    "hk": {
        "host": "hk.mlmll.cn",
        "nosync": "false",
        "failover_priority": "90",
        "etcd": True,
    },
    "sg": {
        "host": "sg.mlmll.cn",
        "nosync": "false",
        "failover_priority": "100",
        "etcd": True,
    },
    "us": {
        "host": "us.mlmll.cn",
        "nosync": "true",
        "failover_priority": "50",
        "etcd": True,
    },
    "nas": {
        "host": "mlmll.cn",
        "nosync": "true",
        "failover_priority": "0",
        "etcd": False,
    },
}


def _render(template_path: Path, replacements: dict[str, str]) -> str:
    text = template_path.read_text(encoding="utf-8")
    for name, value in replacements.items():
        text = text.replace(f"__{name}__", str(value))
    return text


def render_node(node_id: str, output_dir: Path | str) -> dict[str, str | None]:
    if node_id not in NODES:
        raise ValueError(f"未知节点: {node_id}")
    node = NODES[node_id]
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    replacements = {
        "NODE_ID": node_id,
        "NODE_HOST": node["host"],
        "NOSYNC": node["nosync"],
        "FAILOVER_PRIORITY": node["failover_priority"],
    }

    patroni_path = output / "patroni.yml"
    patroni_path.write_text(
        _render(TEMPLATE_DIR / "patroni.yml.tpl", replacements),
        encoding="utf-8",
    )
    hba_path = output / "pg_hba.conf"
    hba_path.write_text(
        _render(TEMPLATE_DIR / "pg_hba.conf.tpl", replacements),
        encoding="utf-8",
    )
    haproxy_path = output / "haproxy.cfg"
    haproxy_path.write_text(HAPROXY_TEMPLATE.read_text(encoding="utf-8"), encoding="utf-8")

    etcd_path = None
    if node["etcd"]:
        etcd_file = output / "etcd.env"
        etcd_file.write_text(
            _render(TEMPLATE_DIR / "etcd.env.tpl", replacements),
            encoding="utf-8",
        )
        etcd_path = str(etcd_file)

    return {
        "patroni": str(patroni_path),
        "pg_hba": str(hba_path),
        "haproxy": str(haproxy_path),
        "etcd_env": etcd_path,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Render one CRM cluster node configuration")
    parser.add_argument("--node", required=True, choices=sorted(NODES))
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    output = args.output or ROOT / "generated" / args.node
    render_node(args.node, output)
    print(f"rendered {args.node} -> {output}")


if __name__ == "__main__":
    main()
