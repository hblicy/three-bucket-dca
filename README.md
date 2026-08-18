# three-bucket-dca

页面标题：三仓定投计划  
系统模块名：DCA Tracker

当前保留三个仓位：

- BTC：周期进攻仓
- CRCL：加密金融成长仓
- QQQM + VOO：美股长期底仓

## 配置文件

本地运行和 VPS systemd 部署使用的配置模板不同：

- `.env.example`：本地开发模板。复制成项目根目录 `.env` 后，`python app.py` 会读取它。
- `deploy/three-bucket-dca.env.example`：Linux systemd 部署模板。复制到 `/etc/three-bucket-dca.env` 后，systemd 服务会读取它。

实际生效文件：

- 本地：`/opt/three-bucket-dca/.env` 或当前项目目录下的 `.env`
- VPS systemd：`/etc/three-bucket-dca.env`

关键配置：

```env
DCA_WRITE_TOKEN=change-me
DCA_READ_TOKEN=
WEWORK_BOT_WEBHOOK=https://example.com/CHANGE_ME
FRED_API_KEY=
CRCL_FUNDAMENTALS_CACHE_TTL=3600
DCA_DB_PATH=data/dca_tracker.db
DCA_HOST=127.0.0.1
DCA_PORT=8020
BTC_BASE_AMOUNT=100
CRCL_BASE_AMOUNT=100
US_INDEX_MONTHLY_AMOUNT=500
DCA_UPDATE_MIGRATE_LEGACY=false
```

说明：

- `DCA_WRITE_TOKEN`：新增、编辑、删除交易时需要输入。部署前必须改成强随机值。
- `DCA_READ_TOKEN`：可选。为空时读接口开放；配置后前端会提示输入读 token。
- `WEWORK_BOT_WEBHOOK`：企业微信机器人 webhook。
- `FRED_API_KEY`：CRCL 基本面里的储备收益率、Fed 利率数据。
- `DCA_UPDATE_MIGRATE_LEGACY`：默认 `false`。定时更新不重复迁移旧库，避免覆盖三仓里已编辑的记录。

## 本地运行

```bash
cd three-bucket-dca
copy .env.example .env
pip install -r requirements.txt
python scripts/migrate_legacy.py
python app.py
```

访问：

```text
http://127.0.0.1:8020
```

## VPS Linux 部署

推荐部署目录：

```text
/opt/three-bucket-dca
```

推荐 systemd 环境文件：

```text
/etc/three-bucket-dca.env
```

### 1. 上传项目

将本项目上传到：

```bash
/opt/three-bucket-dca
```

### 2. 安装依赖

```bash
cd /opt/three-bucket-dca
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 3. 配置环境变量

```bash
sudo cp deploy/three-bucket-dca.env.example /etc/three-bucket-dca.env
sudo nano /etc/three-bucket-dca.env
```

按实际情况修改：

```env
DCA_WRITE_TOKEN=换成强随机token
DCA_READ_TOKEN=
WEWORK_BOT_WEBHOOK=你的企业微信webhook
FRED_API_KEY=你的FRED_API_KEY

DCA_DB_PATH=data/dca_tracker.db
DCA_HOST=127.0.0.1
DCA_PORT=8020

BTC_BASE_AMOUNT=100
CRCL_BASE_AMOUNT=100
US_INDEX_MONTHLY_AMOUNT=500
DCA_UPDATE_MIGRATE_LEGACY=false

LEGACY_BTC_DB=/opt/BTC_DDCA_old999/data/investments.db
LEGACY_CRCL_DB=/opt/CRCL_DCA/crcl_investments.db
LEGACY_US_INDEX_DB=/opt/US_INDEX_DCA/index_dca.db
```

如果不用 Nginx、想直接公网访问，可将 `DCA_HOST` 改为 `0.0.0.0`。更推荐保留 `127.0.0.1`，用 Nginx 反向代理。

### 4. 手动迁移旧数据库

确认 `/etc/three-bucket-dca.env` 里的 `LEGACY_*_DB` 路径正确后执行：

```bash
cd /opt/three-bucket-dca
source .venv/bin/activate
python scripts/migrate_legacy.py
python scripts/update.py
```

### 5. 安装 systemd 服务

systemd 文件在 `deploy/` 目录：

- `three-bucket-dca.service`
- `three-bucket-dca-update.service`
- `three-bucket-dca-update.timer`

默认服务用户是 `indexpulse`。如果 VPS 没有这个用户，可以创建：

```bash
sudo useradd -r -m -s /usr/sbin/nologin indexpulse
sudo chown -R indexpulse:indexpulse /opt/three-bucket-dca
```

安装并启动：

```bash
sudo cp deploy/three-bucket-dca.service /etc/systemd/system/
sudo cp deploy/three-bucket-dca-update.service /etc/systemd/system/
sudo cp deploy/three-bucket-dca-update.timer /etc/systemd/system/

sudo systemctl daemon-reload
sudo systemctl enable three-bucket-dca.service
sudo systemctl enable three-bucket-dca-update.timer

sudo systemctl start three-bucket-dca.service
sudo systemctl start three-bucket-dca-update.timer
```

### 6. 检查状态

```bash
sudo systemctl status three-bucket-dca.service
sudo systemctl status three-bucket-dca-update.timer
curl http://127.0.0.1:8020/api/health
```

查看定时更新日志：

```bash
cd /opt/three-bucket-dca
tail -f logs/update.log
```

## 自动更新和提醒

定时器每 5 分钟运行一次：

```bash
python scripts/update.py
```

默认行为：

- 刷新 dashboard 缓存。
- 刷新收益曲线缓存。
- 在定投日发送企业微信提醒。
- 不重新迁移旧数据库。

如果确实需要定时更新时顺带迁移旧库，可设置：

```env
DCA_UPDATE_MIGRATE_LEGACY=true
```

更推荐手动执行：

```bash
python scripts/migrate_legacy.py
```

## Nginx 反向代理示例

```nginx
server {
    listen 80;
    server_name your-domain.com;

    location / {
        proxy_pass http://127.0.0.1:8020;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```

然后：

```bash
sudo nginx -t
sudo systemctl reload nginx
```
