# CRM 条码查询工具

这是一个 Flask + Playwright 的 CRM 条码查询工具，包含：

- 在线登录 CRM
- 单个/批量条码查询
- 后台 Playwright 查询
- 查询结果 HTML 管理
- 结果归档、备注、导出
- Docker 云服务器部署

## 项目文件

核心文件：

```text
app.py
templates/index.html
templates/crm.html
requirements.txt
Dockerfile
docker-compose.yml
config.example.json
config.docker.example.json
accounts.example.json
```

不会上传到 GitHub 的本地文件：

```text
accounts.json      # CRM 账号密码
config.json        # 实际 CRM 地址和本地路径配置
barcode/           # 查询结果 HTML
results/           # 导出结果
session/           # 浏览器登录会话
venv/
*.log
*.pid
```

## 本地运行

安装依赖：

```bash
pip install -r requirements.txt
playwright install chromium
```

创建本地配置：

```bash
cp config.example.json config.json
cp accounts.example.json accounts.json
```

编辑 `config.json` 和 `accounts.json`，然后启动：

```bash
python app.py
```

访问：

```text
http://127.0.0.1:5001/
http://127.0.0.1:5001/crm
```

## Docker 部署

### 1. 云服务器安装 Docker

如果服务器还没有 Docker，先安装 Docker 和 Compose。Ubuntu/Debian 常用方式：

```bash
curl -fsSL https://get.docker.com | sh
sudo systemctl enable docker
sudo systemctl start docker
```

确认安装成功：

```bash
docker --version
docker compose version
```

### 2. 拉取项目

```bash
git clone https://github.com/lovegongqi/CRM_barcode_query.git
cd CRM_barcode_query
```

### 3. 创建运行配置

```bash
cp config.docker.example.json config.json
cp accounts.example.json accounts.json
mkdir -p barcode results session
```

编辑 `config.json`：

```json
{
    "website": {
        "url": "你的 CRM 地址",
        "report_url": ""
    },
    "browser": {
        "headless": true,
        "viewport": {
            "width": 1920,
            "height": 1080
        }
    },
    "session": {
        "state_path": "/app/session",
        "save_on_exit": true
    }
}
```

编辑 `accounts.json`：

```json
{
    "你的CRM账号": {
        "password": "你的CRM密码"
    }
}
```

注意：`config.json` 和 `accounts.json` 不会被 Git 上传，但在服务器上必须存在。

### 4. 启动容器

```bash
docker compose up -d --build
```

查看运行状态：

```bash
docker compose ps
docker compose logs -f
```

访问：

```text
http://服务器IP:5001/
http://服务器IP:5001/crm
```

如果云服务器有防火墙或安全组，需要放行 TCP `5001` 端口。

### 5. 停止、重启、更新

停止：

```bash
docker compose down
```

重启：

```bash
docker compose restart
```

更新到 GitHub 最新代码：

```bash
git pull
docker compose up -d --build
```

## 多架构说明

`Dockerfile` 使用官方 `python:3.11-slim`，不锁定 CPU 架构。

所以你在服务器上直接执行：

```bash
docker compose up -d --build
```

Docker 会按当前服务器架构自动构建：

- 普通 x86 云服务器：`linux/amd64`
- ARM 云服务器：`linux/arm64`

如果你想提前构建一个同时支持 x86 和 ARM 的镜像，可以用 `buildx`：

```bash
docker buildx create --use --name multiarch-builder
docker buildx build \
  --platform linux/amd64,linux/arm64 \
  -t 你的DockerHub用户名/crm-barcode-query:latest \
  --push .
```

然后把 `docker-compose.yml` 里的镜像改成：

```yaml
image: 你的DockerHub用户名/crm-barcode-query:latest
```

服务器上运行：

```bash
docker compose pull
docker compose up -d
```

## 数据持久化

`docker-compose.yml` 已经把这些目录挂载到宿主机：

```text
./config.json  -> /app/config.json
./accounts.json -> /app/accounts.json
./barcode     -> /app/barcode
./results     -> /app/results
./session     -> /app/session
```

所以容器重建后：

- CRM 账号配置不会丢
- 浏览器登录会话不会丢
- 查询结果不会丢
- 导出文件不会丢

## 安全建议

- 不要把 `accounts.json` 和 `config.json` 上传到公开仓库。
- 云服务器建议只给可信 IP 开放 `5001` 端口。
- 如果要公网长期使用，建议前面加 Nginx + HTTPS + 访问密码。
