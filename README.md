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

## Windows exe 打包

### GitHub Actions 自动打包

推送到 `main` 后，GitHub Actions 会自动在 Windows 环境构建 exe。也可以在 GitHub 仓库页面手动运行：

```text
Actions -> Build Windows exe -> Run workflow
```

构建完成后，在这次 workflow 的 `Artifacts` 下载：

```text
CRM条码查询-Windows
```

下载后解压 `CRM条码查询-Windows.zip`，双击里面的 `CRM条码查询.exe` 即可启动。

### 本地 Windows 打包

建议在 Windows 电脑上打包。打包结果是目录版 exe，里面会带上 Playwright Chromium 浏览器；复制时要复制整个目录。

准备：

- 安装 Python 3.11，并勾选添加到 PATH
- 用 PowerShell 进入项目目录

执行：

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\build_windows.ps1
```

完成后会生成：

```text
dist\CRM条码查询\CRM条码查询.exe
```

使用时复制整个目录：

```text
dist\CRM条码查询\
```

到 Windows 电脑后双击 `CRM条码查询.exe`。程序会启动本地服务并自动打开：

```text
http://127.0.0.1:5001/product-library
```

注意：运行时不要关闭 exe 弹出的黑色窗口，关闭窗口后本地服务也会停止。

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

`docker-compose.yml` 已经把这些目录挂载到宿主机：

```text
./barcode     -> /app/barcode
./results     -> /app/results
./session     -> /app/session
```

所以容器重建后：

- 浏览器登录会话不会丢
- 查询结果不会丢
- 导出文件不会丢

## 安全建议

- 不要把自己的 `config.json` 上传到公开仓库。
- 云服务器建议只给可信 IP 开放 `5001` 端口。
- 如果要公网长期使用，建议前面加 Nginx + HTTPS + 访问密码。
