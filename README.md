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
- `deploy/three-bucket-dca.env.example`：Linux systemd 部署模板。首次部署时可复制为 `/home/ubuntu/three-bucket-dca/three-bucket-dca.env`，systemd 服务会读取它。

实际生效文件：

- 本地：当前项目目录下的 `.env`
- VPS systemd：`/home/ubuntu/three-bucket-dca/three-bucket-dca.env`

关键配置：

```env
DCA_WRITE_TOKEN=change-me
DCA_READ_TOKEN=
WEWORK_BOT_WEBHOOK=https://example.com/CHANGE_ME
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
/home/ubuntu/three-bucket-dca
```

推荐 systemd 环境文件：

```text
/home/ubuntu/three-bucket-dca/three-bucket-dca.env
```

### 1. 上传项目

将本项目上传到：

```bash
/home/ubuntu/three-bucket-dca
```

### 2. 安装依赖

```bash
cd /home/ubuntu/three-bucket-dca
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 3. 配置环境变量

```bash
cp deploy/three-bucket-dca.env.example three-bucket-dca.env
nano three-bucket-dca.env
```

按实际情况修改：

```env
DCA_WRITE_TOKEN=换成强随机token
DCA_READ_TOKEN=
WEWORK_BOT_WEBHOOK=你的企业微信webhook

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

确认 `/home/ubuntu/three-bucket-dca/three-bucket-dca.env` 里的 `LEGACY_*_DB` 路径正确后执行：

```bash
cd /home/ubuntu/three-bucket-dca
set -a
source three-bucket-dca.env
set +a
source .venv/bin/activate
python scripts/migrate_legacy.py
python scripts/update.py
```

### 5. 安装 systemd 服务

systemd 文件在 `deploy/` 目录：

- `three-bucket-dca.service`
- `three-bucket-dca-update.service`
- `three-bucket-dca-update.timer`
- `three-bucket-dca-halving.service`
- `three-bucket-dca-halving.timer`

服务使用现有的 `ubuntu` 用户。确保项目目录归该用户所有：

```bash
sudo chown -R ubuntu:ubuntu /home/ubuntu/three-bucket-dca
```

安装并启动：

```bash
sudo cp deploy/three-bucket-dca.service /etc/systemd/system/
sudo cp deploy/three-bucket-dca-update.service /etc/systemd/system/
sudo cp deploy/three-bucket-dca-update.timer /etc/systemd/system/
sudo cp deploy/three-bucket-dca-halving.service /etc/systemd/system/
sudo cp deploy/three-bucket-dca-halving.timer /etc/systemd/system/

sudo systemctl daemon-reload
sudo systemctl enable three-bucket-dca.service
sudo systemctl enable three-bucket-dca-update.timer
sudo systemctl enable three-bucket-dca-halving.timer

sudo systemctl start three-bucket-dca.service
sudo systemctl start three-bucket-dca-update.timer
sudo systemctl start three-bucket-dca-halving.timer
```

### 6. 检查状态

```bash
sudo systemctl status three-bucket-dca.service
sudo systemctl status three-bucket-dca-update.timer
sudo systemctl status three-bucket-dca-halving.timer
curl http://127.0.0.1:8020/api/health
```

查看定时更新日志：

```bash
cd /home/ubuntu/three-bucket-dca
tail -f logs/update.log
```

## VPS 安全更新

项目数据库位于被 Git 忽略的 `data/`，环境文件 `three-bucket-dca.env` 也已被忽略；正常的 `git pull --ff-only` 不会覆盖它们。使用安全更新脚本执行停服、状态确认和数据库备份，全部成功后才会拉取代码：

```bash
cd /home/ubuntu/three-bucket-dca
bash scripts/safe_update.sh
```

脚本启用 `set -euo pipefail`。环境文件和 Python 虚拟环境会在停服前检查；任一服务未能停止或 SQLite 备份未实际生成时，更新都会立即中止，不会继续执行 `git pull`。停服后的拉取、依赖安装或 systemd 更新失败时，脚本会尝试恢复更新前正在运行的服务和定时器。更新时不要重新复制环境模板，不要删除 `data/` 或 `backups/`，也不要执行会清理被忽略文件的 `git clean -fdx`。保持 `DCA_UPDATE_MIGRATE_LEGACY=false`；安全更新脚本不会运行迁移，只有需要补导旧库时才手工运行迁移脚本，脚本会先备份现有数据库，且不会覆盖已经导入并编辑过的交易。

## 自动更新和提醒

完整数据更新定时器每 5 分钟运行一次：

```bash
python scripts/update.py
```

默认行为：

- 刷新 dashboard 缓存。
- 刷新收益曲线缓存。
- 在定投日发送企业微信提醒。
- 不重新迁移旧数据库。

BTC 减半状态由独立轻量定时器每 1 分钟检查一次。距离目标区块较远时仍复用 6 小时缓存；接近目标区块或不足 6 个确认时按 1 分钟缓存复核，达到 6 个确认后固定最终结果。该任务只更新减半状态缓存，不重复下载完整行情或刷新收益曲线。

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
