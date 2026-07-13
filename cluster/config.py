import os
from dataclasses import dataclass
from typing import Mapping


@dataclass(frozen=True)
class ClusterConfig:
    enabled: bool
    node_id: str
    node_name: str
    node_role: str
    database_url: str
    r2_endpoint_url: str
    r2_bucket: str
    r2_access_key_id: str
    r2_secret_access_key: str
    credentials_key: str

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "ClusterConfig":
        values = os.environ if env is None else env
        enabled = (
            values.get("CRM_CLUSTER_MODE") == "postgresql"
            and values.get("CRM_DESKTOP_APP") != "1"
        )
        return cls(
            enabled=enabled,
            node_id=values.get("CRM_NODE_ID", "standalone-1").strip(),
            node_name=values.get("CRM_NODE_NAME", "单机节点").strip(),
            node_role=values.get("CRM_NODE_ROLE", "standalone").strip(),
            database_url=values.get("DATABASE_URL", "").strip(),
            r2_endpoint_url=values.get("R2_ENDPOINT_URL", "").strip(),
            r2_bucket=values.get("R2_BUCKET", "").strip(),
            r2_access_key_id=values.get("R2_ACCESS_KEY_ID", "").strip(),
            r2_secret_access_key=values.get("R2_SECRET_ACCESS_KEY", "").strip(),
            credentials_key=values.get("CRM_CREDENTIALS_KEY", "").strip(),
        )

    def validate(self) -> None:
        if not self.enabled:
            return
        required = {
            "DATABASE_URL": self.database_url,
            "R2_ENDPOINT_URL": self.r2_endpoint_url,
            "R2_BUCKET": self.r2_bucket,
            "R2_ACCESS_KEY_ID": self.r2_access_key_id,
            "R2_SECRET_ACCESS_KEY": self.r2_secret_access_key,
            "CRM_CREDENTIALS_KEY": self.credentials_key,
        }
        missing = [name for name, value in required.items() if not value]
        if missing:
            raise ValueError("缺少集群配置: " + ", ".join(missing))

