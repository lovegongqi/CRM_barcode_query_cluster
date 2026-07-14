# PostgreSQL 集群切换与恢复

## 生产拓扑

| 节点 | 源码目录 | 应用端口 | 角色 |
| --- | --- | --- | --- |
| 香港 | `/opt/crm-barcode-ha` | `5112` | Web 主入口、数据库首选主库、etcd、CRM worker |
| 新加坡 | `/opt/crm-barcode-ha` | `5114` | Web 备用入口、同步副本、etcd、CRM worker |
| 美国 | `/opt/crm-barcode-ha` | `5113` | 异步副本、etcd、CRM worker |
| 群晖 | `/volume1/docker/crm-barcode-ha` | `5011` | 异步副本、CRM worker、R2 镜像 |

公网入口是 `https://crm.mlmll.cn`。Cloudflare 默认使用香港，香港不健康时使用新加坡。旧群晖单机服务的 `5002` 端口在集群验收前保留。

节点间开放的 TCP 端口：PostgreSQL `15432`、Patroni `8008`、etcd `2379/2380`。这些服务同时要求私有 CA 客户端证书和密码。应用只连接本机 HAProxy 的 `5433`。

## Compose 命令

云服务器在源码目录执行：

```bash
docker compose --env-file .env -f deploy/compose.cluster.yml ps
```

群晖使用 Container Manager 自带的 Docker：

```bash
/var/packages/ContainerManager/target/usr/bin/docker compose \
  --env-file .env -f deploy/compose.cluster.yml ps
```

`.env`、`infra/generated/<node>` 和 Docker 数据卷均不得删除或提交到 Git。

## 日常检查

在任意云节点查看数据库角色和复制延迟：

```bash
docker compose --env-file .env -f deploy/compose.cluster.yml \
  exec -T patroni patronictl -c /etc/patroni/patroni.yml list
```

正常状态应满足：

- 只有一个 `Leader`。
- 新加坡在香港主库正常时为 `Sync Standby`。
- 其余节点为 `Replica`，状态为 `streaming`。
- 四节点时间线一致，复制延迟为 `0` 或短暂的极小值。

检查 Web：

```bash
curl -fsS https://crm-hk-origin.mlmll.cn/readyz
curl -fsS https://crm-sg-origin.mlmll.cn/readyz
curl -fsS https://crm.mlmll.cn/readyz
```

## 发布应用

先更新各节点 `.env` 中的 `CRM_IMAGE` 和 `CRM_APP_VERSION`，再只更新应用容器：

```bash
docker compose --env-file .env -f deploy/compose.cluster.yml pull app
docker compose --env-file .env -f deploy/compose.cluster.yml up -d --no-deps app
```

顺序为新加坡、美国、群晖、香港。每更新一个节点都等待 `readyz` 通过后再继续。基础设施配置变化时先运行：

```bash
python3 infra/render_node_config.py --node <hk|sg|us|nas>
docker compose --env-file .env -f deploy/compose.cluster.yml up -d --force-recreate haproxy
```

## 手动切换主库

切换前确认四节点复制正常且延迟为零。香港切到新加坡：

```bash
docker compose --env-file .env -f deploy/compose.cluster.yml \
  exec -T patroni patronictl -c /etc/patroni/patroni.yml \
  switchover crm-barcode-postgres --leader hk --candidate sg --force
```

切回香港：

```bash
docker compose --env-file .env -f deploy/compose.cluster.yml \
  exec -T patroni patronictl -c /etc/patroni/patroni.yml \
  switchover crm-barcode-postgres --leader sg --candidate hk --force
```

命令返回 `503 Switchover status unknown` 时不要立即重复执行。持续运行 `patronictl list`，确认角色是否已经变化后再决定下一步。切换完成后验证两个源站、公共网址以及一次数据库写入。

## 备份

备份命令可在任意节点执行，只有当前主库会真正运行：

```bash
docker compose --env-file .env -f deploy/compose.cluster.yml \
  exec -T patroni backup-if-primary diff
```

每周完整备份：

```bash
docker compose --env-file .env -f deploy/compose.cluster.yml \
  exec -T patroni backup-if-primary full
```

查看 R2 中的备份：

```bash
docker compose --env-file .env -f deploy/compose.cluster.yml \
  exec -T patroni gosu postgres pgbackrest --stanza=crm-barcode info
```

群晖的 `nas-r2-mirror` 每六小时把 R2 PostgreSQL 备份同步到 `.env` 中的 `NAS_BACKUP_PATH`。检查容器日志确认最近一次同步没有错误。

## 恢复演练

恢复必须在隔离容器和独立 Docker 卷中进行，不得直接覆盖生产 `postgres_data`。恢复完成后分别设置：

```bash
export SOURCE_DATABASE_URL='生产只读连接地址'
export RESTORE_DATABASE_URL='隔离恢复库连接地址'
infra/pgbackrest/verify_restore.sh
```

脚本会比较 `app_accounts`、`runtime_config`、`barcode_records`、`product_rules`、`distributors`、CRM 通道、任务、日志、对象和迁移记录共 13 张表。全部计数一致才算恢复验证通过。

## 故障与回滚

1. 香港 Web 故障：等待 Cloudflare 把 `crm.mlmll.cn` 切到新加坡，直接检查新加坡源站。
2. 香港数据库故障：先用 `patronictl list` 确认新加坡已成为唯一主库，不要手工启动第二个主库。
3. 发布版本故障：把 `.env` 中 `CRM_IMAGE` 和 `CRM_APP_VERSION` 改回上一提交镜像，只重建 `app`。
4. 集群整体不可用：保持 PostgreSQL 卷不变，临时恢复旧群晖 `5002` 单机服务；确认旧入口数据范围后再调整 Cloudflare，不得把旧 JSON 回写到新集群数据库。
5. 数据损坏：停止应用写入，从最近完整备份和差异备份恢复到新卷，执行 13 表校验后再替换生产卷。

任何回滚都不删除 R2 对象、旧 JSON/HTML、PostgreSQL 卷、浏览器 session 卷或证书目录。
