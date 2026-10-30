# AGENTS.md

## 技术栈与运行约定

- 后端使用 Python / FastAPI，数据及缓存使用现有 SQLite；前端使用原生 HTML、JavaScript 和 CSS。
- 微策略 BTC 成本由后台更新任务每天检查一次，使用已安装的 Chrome/Chromium 无头读取 Strategy 官方账本的 `__NEXT_DATA__`。Linux 服务运行用户需要能启动该浏览器，保留浏览器默认沙箱。
- 网页请求只读取均价缓存。取最新披露记录的 `average_price`，日期表示披露日；不得用单笔 `purchase_price` 或取整总成本替代累计均价。
- 采集失败保留上次有效数据、披露日期和调用链；生产浏览器安装、服务重启及部署需获得对应操作授权。

- BTC 当前报价与历史日线均使用币安公共接口的 `BTCUSDT`，单位为 USDT；历史日线以 UTC 为界，按每页最多1000根完整分页，连续日期和有效价格校验失败时整体报错，不返回截断历史。DCA 指标与收益曲线共用该序列，分位按币安可用历史计算；记录来源、历史起点及币种，不沿用 Yahoo 全历史口径的表述。组合参考估值继续按 1 USDT ≈ 1 USD。取价失败明确报错，不回退 Yahoo。

- CRCL、QQQM、VOO 的当前报价继续使用币安官方股票组件公开快照（原始代码、USD）；历史指标和收益曲线使用 FMP `historical-price-eod/full` 的 `close`，保留原来的拆股复权、未计分红再投资口径，不替换为其他ETF或代币化证券。历史按原始代码分别下载并缓存15分钟，调用方取得独立副本；配置 `FMP_API_KEY`，缺失或接口失败明确报错，禁止回退 Yahoo。
- QQQM 的 PE 分层和均线降档规则不变。PE 使用 Invesco 官方 QQQ 基金特征接口的 `priceToEarningsRatio`，验证 CUSIP、币种和披露日期，不用 `forwardPriceToEarningsRatio` 替代，也不把官方字段标为 Yahoo `trailingPE`。官方披露及备用缓存均限制35天；过期或缺失时停止正式建议。保留既有交易数据库及有效缓存，不清库，不修改历史交易。
