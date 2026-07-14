# PostgreSQL 高可用集群部署

生产集群由四个节点组成：

- 香港：初始 PostgreSQL 主库、etcd、Web 主入口、CRM worker
- 新加坡：同步 PostgreSQL 副本、etcd、Web 备用入口、CRM worker
- 美国：异步 PostgreSQL 副本、etcd、CRM worker
- 群晖：异步 PostgreSQL 副本、CRM worker、R2 备份镜像

所有节点均运行本机 HAProxy，应用只连接 `db.mlmll.cn:5433`。HAProxy 根据 Patroni 状态把连接发送到当前主库。数据库、Patroni 和 etcd 的公网连接都要求集群私有 CA 签发的客户端证书。

PostgreSQL 容器内部使用 `5432`，节点之间统一通过公网 `15432` 连接，避免占用服务器上已有数据库的 `5432`。

## 部署文件

- `compose.cluster.yml`：四个节点共用的生产编排
- `env.cluster.example`：环境变量模板
- `../infra/pki/generate.sh`：生成私有 CA、节点证书和节点配置
- `../infra/pgbackrest/`：R2 归档、备份和恢复校验

旧的 `compose.nas.yml` 和 `compose.worker.yml` 只保留给原文件模式服务回滚使用，不用于新集群。

## 节点配置

每台机器使用独立 `.env`。默认每节点为 5 个查询通道、2 个移库通道：

```text
CRM_QUERY_WORKERS=5
CRM_TRANSFER_WORKERS=2
```

节点差异：

```text
# 香港
COMPOSE_PROFILES=etcd
CRM_NODE_ID=hk
CRM_NODE_HOST=hk.mlmll.cn
CRM_APP_PORT=5012

# 新加坡
COMPOSE_PROFILES=etcd
CRM_NODE_ID=sg
CRM_NODE_HOST=sg.mlmll.cn
CRM_APP_PORT=5014

# 美国
COMPOSE_PROFILES=etcd
CRM_NODE_ID=us
CRM_NODE_HOST=us.mlmll.cn
CRM_APP_PORT=5013

# 群晖
COMPOSE_PROFILES=nas
CRM_NODE_ID=nas
CRM_NODE_HOST=mlmll.cn
CRM_APP_PORT=5011
```

所有密码、令牌和 R2 密钥只写入服务器 `.env`，不要提交到 Git。

## 安全上线顺序

1. 备份现有 NAS 数据并生成文件清单和 SHA-256 清单。
2. 生成 PKI，将每个节点自己的 `infra/generated/<node>` 安全传到对应服务器。
3. 先启动香港、新加坡、美国的 etcd，再启动四节点 Patroni 和 HAProxy。
4. 验证主从复制、同步副本和自动故障转移。
5. 创建应用数据库，执行旧数据迁移的 dry-run、apply 和 verify。
6. 启动四节点应用，验证跨节点任务、日志和通道状态。
7. 验证 R2 完整备份可恢复后，再切换 Cloudflare 公网入口。

上线过程中不删除原 JSON、HTML、Excel、Docker 卷或旧容器。只有新集群全部验收后才停止旧入口。

## 配置校验

```bash
docker compose --env-file deploy/env.cluster.example \
  -f deploy/compose.cluster.yml config --quiet
```

启动单个云节点：

```bash
docker compose --env-file .env -f deploy/compose.cluster.yml \
  --profile etcd up -d --build
```

启动群晖节点：

```bash
docker compose --env-file .env -f deploy/compose.cluster.yml \
  --profile nas up -d --build
```

生产切换、故障转移、数据迁移和回滚的逐项命令记录在 `docs/operations/postgresql-cutover.md`。
