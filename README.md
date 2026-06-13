# 怡口CRM条码查询工具

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
```

不会上传到 GitHub 的本地文件：

```text
config.json        # 可选：覆盖默认 CRM 地址和本地路径配置
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

如果需要覆盖默认配置，可以创建本地配置：

```bash
cp config.example.json config.json
```

默认 CRM 入口已经写在示例配置里。账号和密码直接在网页登录弹窗里填写，不需要先写入 `accounts.json`。

启动：

```bash
python app.py
```

访问：

```text
http://127.0.0.1:5001/
http://127.0.0.1:5001/crm
```

## Docker 部署

Docker 版会在容器内用 Xvfb 启动一个虚拟显示器，让 Playwright 以普通 Chromium 形态运行。这样云服务器不需要真实桌面，也能兼容 CRM 的老式 Crystal Reports 页面。

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

### 3. 启动容器

```bash
mkdir -p barcode results session
docker compose up -d --build
```

项目已经内置 CRM 入口，部署后打开网页，点击“登录 CRM”，在弹窗里现填账号、密码、验证码即可。`accounts.json` 不是必需文件。

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

### 4. 可选：覆盖配置

正常使用不需要创建配置文件。若以后 CRM 入口或运行路径变化，再执行：

```bash
cp config.docker.example.json config.json
```

然后编辑 `docker-compose.yml`，取消这一行的注释：

```yaml
# - ./config.json:/app/config.json:ro
```

最后重建容器：

```bash
docker compose up -d --build
```

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

`docker-compose.yml` 已经把数据挂载到 Docker 命名卷：

```text
crm_barcode_query_app_data         -> /app/data
crm_barcode_query_browser_session  -> /app/session
```

所以正常执行 `git pull origin main` 和 `docker compose up -d --build` 后：

- 产品库/条码匹配数据不会丢
- 移库目标分销商历史不会丢
- 查询结果和导出文件不会丢
- 浏览器登录会话不会丢

主要文件位置：

```text
/app/data/config/product_library.json       # 条码匹配/产品库
/app/data/config/distributor_history.json   # 移库目标分销商历史
/app/data/config/barcode_data.json          # 条码备注、归档、本地移库同步状态
/app/data/config/accounts.json              # 工具账号和权限
/app/data/config/runtime_config.json        # 通道数量、公司名称、冻结仓配置
/app/data/config/crm_credentials.json       # 记住的 CRM 账号密码
/app/data/config/crm_slot_state.json        # 查询/移库通道登录状态缓存
/app/data/barcode/*.html                    # CRM 条码查询结果页面
/app/data/barcode/archived/*.html           # 已归档查询结果页面
```

不要执行 `docker compose down -v` 或删除这两个 Docker 卷，否则会清空持久化数据。

## 安全建议

- 不要把自己的 `config.json` 上传到公开仓库。
- 云服务器建议只给可信 IP 开放 `5001` 端口。
- 如果要公网长期使用，建议前面加 Nginx + HTTPS + 访问密码。
