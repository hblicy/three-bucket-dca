# 历史审计记录（已归档）

> 本文记录旧版实现，包含的 CRCL 基本面评分等内容已被当前规则替代，不作为现行买入算法依据。现行规则以代码、自动化测试和规则页面为准。

全量审计完成。以下是当时版本（三仓）的完整报告：

✅ 新版本亮点（较上轮审计新增）
功能	文件
SQLite API 缓存层（dashboard + performance）	cache.py
收益曲线模块 + /api/performance	performance.py
总览页收益 SVG 图表（4 个 tab）	index.html
交易页：多维筛选 + 分页 + 搜索	transactions.html
text() 统一 XSS 防护函数	common.js
Token 写入 sessionStorage（刷新不丢）	common.js
FRED + DefiLlama 基本面评分（CRCL）	rules.py
refresh_all() 写后刷新缓存	app.py
🔴 CRITICAL（1 个）
1. performance.py 的序列过滤逻辑错误——0 仓位被丢弃
文件： performance.py:63


series = [point for point in series if point["cost"] != 0 or point["value"] not in (0, None)]
point["value"] not in (0, None) — 当 value == 0 时条件为 False，整行被丢弃。这意味着：

清仓后第一天（shares=0, value=0）会被过滤掉
最后一天的 latest 取的是过滤后的 series[-1]，如果恰好刚清仓，会拿到前一天数据
"成本 $0" 的行（如纯卖单日）也会被过滤，但当前逻辑本意是保留
不过实际影响有限——清仓场景罕见，且过滤后 series[-1] 仍有合理的前序值。但逻辑意图与实现不符。

修复：


# 保留：cost=0 且 value>0（纯卖单后第一天）以及 cost>0 的行
series = [point for point in series
          if point["cost"] != 0
          or (point["value"] is not None and point["value"] != 0)]
🟡 MEDIUM（2 个）
2. _price_maps 失败时静默返回空图
文件： performance.py:116–118


except Exception as exc:
    errors.append({"asset": asset, "message": str(exc)})
    out[asset] = {}    # ← 空 dict，后续 ffill 后全是 None
yfinance 限流或网络抖动时，该资产价格全为 None，图表在失败时点之后全段 NaN，无任何警告标识传入 chart。errors 字段确实填了，但 chart 直接忽略它。

3. refresh_all() 中途失败留下不一致缓存
文件： cache.py:64–75


def refresh_all() -> dict:
    dashboard = build_dashboard()
    set_cache(DASHBOARD_KEY, dashboard)        # ← 写 dashboard 缓存
    ...
    for scope in Performance_SCOPES:
        data = build_performance(scope)        # ← 如果这里抛异常
        set_cache(performance_key(scope), data)
如果 build_performance("CRCL_GROWTH") 抛异常，后面的 US_INDEX_CORE 永远不会被刷新。dashboard 和部分 performance key 是新值，部分是旧值，下次页面加载时混在一起。至少一个 try/except 包裹循环即可。

🔵 LOW（4 个，可接受）
#	描述	位置
4	latest_price() 仍无独立超时（依赖 history() 的 timeout=20），网络卡顿时 yfinance 内部重试可能导致 5 分钟级别延迟，但 dashboard 有缓存保护	prices.py
5	FRED_API_KEY 在 FRED URL 里明文拼接，不过这是 FRED 官方标准用法，API key 本身也支持只读端	rules.py:88–89
6	定时任务无外部健康检查（无 healthchecks.io / Prometheus pushgateway），VPS 宕机不告警	部署层面
7	config.py ROOT.parent 相对路径假设未用环境变量覆盖时可能失效（已提供 LEGACY_*_DB 覆盖，够用）	config.py:41–44
已确认仍然正确的项目
write_lock() 重入逻辑：return 在 finally 后但 finally 自身包含 return，Python 行为正确，不会落入 OS 锁
backup_db()：shutil.copy2 在 with write_lock() 内部，锁覆盖完整
INSERT OR IGNORE + write_lock() 的提醒抢占机制
所有 SQL 参数化，XSS text() 全局覆盖
4 个 systemd 文件全已改名 three-bucket-dca-*
README / 页面 / 规则说明无 STRC / 四仓残留
/api/transactions LIKE 查询参数化安全
总结
新版本质量良好。新增功能（缓存层 + 收益曲线 + 增强交易页）逻辑清晰、实现稳健。唯一需要修的是 MEDIUM #1（序列过滤 bug），MEDIUM #2/3 优先级更低。修完之后项目生产就绪。
