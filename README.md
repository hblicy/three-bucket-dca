# three-bucket-dca

页面标题：三仓定投计划  
系统模块名：DCA Tracker

当前保留三个仓位：

- BTC：周期进攻仓
- CRCL：加密金融成长仓
- QQQM + VOO：美股长期底仓

## BTC 行情口径

BTC 决策卡、提醒与当前持仓估值使用币安现货 `BTCUSDT` 最新报价，接口为 `https://data-api.binance.vision/api/v3/ticker/price?symbol=BTCUSDT`，无需 API Key。页面报价标明 USDT；提醒保留币安现货来源和北京时间的采集时间；后台沿用每 5 分钟刷新，页面“刷新”会重新取价，提醒发送前也单独取价。

币安请求失败或响应交易对、价格异常时明确报错，不用 Yahoo 价格替代币安报价。DCA 历史指标和历史收益曲线仍使用 Yahoo BTC-USD 日线，原有分位、倍率和预算规则保持原口径。组合汇总沿用原有金额记账，BTC 的 USDT 报价按 1 USDT ≈ 1 USD 作为参考估值。

## 美股行情口径

CRCL、QQQM 和 VOO 的展示价、提醒价与当前持仓估值使用币安 Stocks 的原始美股代码，单位为 USD。读取[币安官方股票组件](https://www.binance.com/en/stocks-price-widget)同源的公开批量快照 `https://www.binance.com/bapi/equity/v1/public/equity/symbol/get-symbols-by-param?symbols=CRCL,QQQM,VOO`，无需 API Key 或新增依赖。按源数据的市场阶段读取盘中、盘前、盘后和可用夜盘价格，不用 QQQ、SPY 或代币化证券替代原始标的。页面只展示价格，提醒保留来源及北京时间的采集时间。

同轮计算批量获取三个标的，在进程内复用 30 秒。请求超时为 10 秒；缺失或无效的标的明确报错，有效标的仍可使用同批报价。该官网公开快照没有源成交时间字段，记录的是本次取价时间，不能把 `t`（STOCK/ETF 类型）当成时间戳；官网接口结构变化会明确报错，不回退为 Yahoo 实时报价。

CRCL 的 DCA 历史价格指标、QQQM 的 PE/趋势规则和历史收益曲线继续沿用现有 Yahoo 日线口径；本次切换的是当前报价，不重算历史策略。

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

微策略 BTC 持仓均价由上述后台任务每天检查一次，通过已安装的 Chrome/Chromium 无头读取 `https://www.strategy.com/ledger` 的 `__NEXT_DATA__.props.pageProps.bitcoinData`，取最新披露记录的 `average_price`。这是累计均价；`purchase_price` 是单笔买入价，不用于该展示。页面日期为账本披露日。

Linux 运行环境需要在 PATH 中提供 `google-chrome`、`google-chrome-stable`、`chromium` 或 `chromium-browser`；Windows 开发环境也支持标准安装目录中的 Chrome。浏览器以运行服务的普通用户执行，保留默认沙箱；本次不新增 Python 依赖。部署前应以同一用户确认 `google-chrome --version` 或 `chromium --version` 可用。

网页和手动刷新只读取均价缓存，不会启动浏览器；首次缓存由后台更新任务生成。采集失败保留最后有效值和披露日期，记录调用链并提示更新失败；失败尝试也记录每天的检查时间，避免每 5 分钟重复启动浏览器。旧版链研社缓存会保留到首次官方采集成功。浏览器采集上限为 90 秒；官网结构或访问策略改变时会明确报错，不用逐笔流水推算替代官方均价。

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
