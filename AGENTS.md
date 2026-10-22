# AGENTS.md

## 技术栈与运行约定

- 后端使用 Python / FastAPI，数据及缓存使用现有 SQLite；前端使用原生 HTML、JavaScript 和 CSS。
- 微策略 BTC 成本由后台更新任务每天检查一次，使用已安装的 Chrome/Chromium 无头读取 Strategy 官方账本的 `__NEXT_DATA__`。Linux 服务运行用户需要能启动该浏览器，保留浏览器默认沙箱。
- 网页请求只读取均价缓存。取最新披露记录的 `average_price`，日期表示披露日；不得用单笔 `purchase_price` 或取整总成本替代累计均价。
- 采集失败保留上次有效数据、披露日期和调用链；生产浏览器安装、服务重启及部署需获得对应操作授权。
