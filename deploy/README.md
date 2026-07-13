# 多服务器部署第一阶段

目标：

- 群晖 NAS 做主节点和 PostgreSQL 数据库承载机器。
- 3 台云服务器做 worker 节点，后续用于查询、移库、结单通道扩容。
- Cloudflare 负责统一域名、健康检查、后续负载均衡/Tunnel。
- Cloudflare R2 作为后续 HTML、Excel、备份文件对象存储。

## 当前阶段说明

本阶段先完成“可部署、可健康检查、可识别节点”的基础设施：

- Docker 镜像由 GitHub Actions 构建并推送到 `ghcr.io/lovegongqi/crm_barcode_query:latest`
- 应用新增：
  - `/healthz`：进程存活检查
  - `/readyz`：配置、数据目录、session 目录、通道池可用检查
  - `/api/node/status`：节点身份、存储配置、CRM 通道状态
- NAS compose 包含 PostgreSQL，先准备长期架构。
- 数据迁移到 PostgreSQL/R2 是第二阶段，不会在第一阶段改动现有 JSON 数据。

## 群晖 NAS 部署

1. 在群晖创建目录，例如：

   ```bash
   mkdir -p /volume1/docker/crm-barcode-query
   cd /volume1/docker/crm-barcode-query
   ```

2. 上传或复制：

   ```text
   deploy/compose.nas.yml
   deploy/env.nas.example
   ```

3. 创建 `.env`：

   ```bash
   cp env.nas.example .env
   vi .env
   ```

   至少修改：

   ```text
   CRM_NODE_ID=nas-1
   CRM_NODE_NAME=群晖NAS
   POSTGRES_PASSWORD=一个强密码
   ```

4. 启动：

   ```bash
   docker compose -f compose.nas.yml pull
   docker compose -f compose.nas.yml up -d
   docker compose -f compose.nas.yml logs -f --tail=100
   ```

5. 检查：

   ```bash
   curl http://127.0.0.1:5001/healthz
   curl http://127.0.0.1:5001/readyz
   curl http://127.0.0.1:5001/api/node/status
   ```

## 云服务器 worker 部署

每台云服务器创建一个目录：

```bash
mkdir -p ~/crm-barcode-query
cd ~/crm-barcode-query
```

上传或复制：

```text
deploy/compose.worker.yml
deploy/env.worker.example
```

创建 `.env`：

```bash
cp env.worker.example .env
vi .env
```

每台服务器必须改成不同节点名：

```text
CRM_NODE_ID=worker-1
CRM_NODE_NAME=云服务器1
```

启动：

```bash
docker compose -f compose.worker.yml pull
docker compose -f compose.worker.yml up -d
docker compose -f compose.worker.yml logs -f --tail=100
```

## Cloudflare 配置思路

建议顺序：

1. 先用 Cloudflare Tunnel 暴露群晖 NAS 的 `http://127.0.0.1:5001`
2. 确认域名能访问 NAS 主节点
3. 3 台云服务器也暴露 `/readyz`
4. 再配置 Load Balancer：
   - Health monitor path: `/readyz`
   - 群晖作为优先池
   - 云服务器作为备用/扩展池

## 第二阶段要做

- 把 `barcode_data.json`、条码匹配、目标分销商、账号、配置迁移到 PostgreSQL。
- 把 HTML、Excel、备份文件迁移到 R2。
- 增加集群任务队列，让所有服务器的查询通道动态抢任务。
- 增加集群状态页面，显示每台服务器和每个通道的登录状态。
