# Three-Bucket Rule Corrections Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修正 BTC、CRCL、提醒和纳指 PE 缓存的规则语义，并让页面与实际算法一致。

**Architecture:** 规则仍集中在 `dca_tracker/rules.py`，行情与 PE 缓存校验留在 `dca_tracker/prices.py`，提醒日判断留在 `dca_tracker/notify.py`。CRCL 买入分只由三个价格指标组成并归一化，USDC 24 小时异常作为独立硬暂停；不增加资金约束、配置项或无关展示指标。

**Tech Stack:** Python 3、unittest、pandas/numpy、FastAPI 静态页面、Node.js test runner。

---

### Task 1: BTC 唯一权重与完整数据要求

**Files:**
- Create: `tests/test_btc_strategy.py`
- Modify: `dca_tracker/prices.py`
- Modify: `dca_tracker/rules.py`

- [x] **Step 1: Write failing tests**

覆盖 `btc_dca_score()` 固定使用 Proxy/AHR999/Puell = 40%/25%/35%，任一输入缺失即返回 NaN；覆盖 P10/P25/P50/P75 边界映射为 4x/2x/1x/0.5x/0x；覆盖最新行指标缺失时返回 `data_ok=False`，不得回退到旧行。

- [x] **Step 2: Run tests and verify RED**

Run: `python -m unittest tests.test_btc_strategy -v`

Expected: 缺失指标测试失败，因为当前实现会对剩余指标重新归一化或回退旧行。

- [x] **Step 3: Implement minimal BTC changes**

让 `btc_dca_score()` 在三个分项未全部有效时返回 NaN；让 `btc_decision()` 只读取最新行并验证分数、价格及四个分位阈值均为有限值，否则生成错误决策。

- [x] **Step 4: Run BTC tests and verify GREEN**

Run: `python -m unittest tests.test_btc_strategy -v`

Expected: 全部通过。

### Task 2: CRCL 价格分与 USDC 硬暂停

**Files:**
- Create: `tests/test_crcl_strategy.py`
- Modify: `dca_tracker/rules.py`
- Modify: `dca_tracker/config.py`

- [x] **Step 1: Write failing tests**

使用真实 DataFrame 构造便宜、中性、昂贵和缺失价格场景；断言原始技术分为 `percentile*0.40 + MA60映射(0..25) + 回撤映射(20..0)`，最终按 `raw/85*100` 归一化并使用 25/45/65 阈值。断言 USDC 24 小时跌幅超过 5% 时强制 `pause/$0`，风险接口失败或价格数据不足时不生成正式建议。

- [x] **Step 2: Run tests and verify RED**

Run: `python -m unittest tests.test_crcl_strategy -v`

Expected: 归一化、硬暂停和失败处理测试失败，因为当前基本面分参与买入分且风险只把基本面分归零。

- [x] **Step 3: Implement minimal CRCL changes**

用 `_crcl_usdc_risk()` 只读取 DefiLlama 中 USDC 当前值与前一日值并返回跌幅；移除 FRED、规模增长、市占和宏观评分。`crcl_decision()` 验证至少 90 个有效收盘价，计算三个价格指标并归一化；USDC 跌幅超过 5% 时覆盖为硬暂停。

- [x] **Step 4: Run CRCL tests and verify GREEN**

Run: `python -m unittest tests.test_crcl_strategy -v`

Expected: 全部通过。

### Task 3: 暂停信号照常提醒

**Files:**
- Create: `tests/test_reminders.py`
- Modify: `dca_tracker/notify.py`

- [x] **Step 1: Write failing tests**

构造 BTC 与 CRCL 的 `pause/$0/data_ok=True` 决策，断言各自在周一、周二 16 点到期；构造错误决策和美股非目标资产，断言不得提醒。

- [x] **Step 2: Run tests and verify RED**

Run: `python -m unittest tests.test_reminders -v`

Expected: 两个暂停信号测试失败，因为当前代码先按金额零直接跳过。

- [x] **Step 3: Implement minimal reminder change**

先拒绝 `data_ok=False`；BTC/CRCL 依据各自计划日判断，不再因建议金额为零跳过；美股继续要求金额大于零且处于信号日。

- [x] **Step 4: Run reminder tests and verify GREEN**

Run: `python -m unittest tests.test_reminders -v`

Expected: 全部通过。

### Task 4: 纳指 PE 缓存有效期

**Files:**
- Modify: `tests/test_us_index_strategy.py`
- Modify: `dca_tracker/prices.py`

- [x] **Step 1: Write failing test**

写入 36 天前的 `nasdaq_pe.json`，让实时行情只返回 forwardPE，断言 `nasdaq_pe()` 抛出 `RuntimeError`；同时把现有有效缓存测试改为实时生成当前 UTC 时间。

- [x] **Step 2: Run test and verify RED**

Run: `python -m unittest tests.test_us_index_strategy -v`

Expected: 过期缓存测试失败，因为当前读取缓存不校验时间。

- [x] **Step 3: Implement minimal cache age check**

在 `prices.py` 使用固定的 35 天最大有效期；无法解析、未来时间或年龄超过 35 天的缓存均拒绝。

- [x] **Step 4: Run US index tests and verify GREEN**

Run: `python -m unittest tests.test_us_index_strategy -v`

Expected: 全部通过。

### Task 5: 页面、配置和文档与算法一致

**Files:**
- Create: `tests/rules_page.test.js`
- Modify: `web/rules.html`
- Modify: `web/settings.html`
- Modify: `app.py`
- Modify: `.env.example`
- Modify: `deploy/three-bucket-dca.env.example`
- Modify: `README.md`

- [x] **Step 1: Write failing static-page tests**

断言规则页包含 CRCL `0-85` 到 `0-100` 归一化和 USDC 24 小时跌幅超过 5% 硬暂停；断言不再包含 CRCL 基本面评分、FRED、现金/短债池和相关渲染脚本。

- [x] **Step 2: Run test and verify RED**

Run: `node --test tests\\rules_page.test.js`

Expected: 失败，因为当前页面仍展示基本面评分和现金/短债池。

- [x] **Step 3: Update UI and documentation**

规则页只展示三个 CRCL 价格输入和 USDC 硬暂停，并写明 BTC/CRCL 4 年、QQQM+VOO 10 年以上的周期；删除无用的基本面脚本及 FRED 设置卡。配置模板和 README 删除 FRED/CRCL 基本面缓存项；美股文案只描述当期投入倍率，不声明现金池；`/api/rules` 明示 BTC 40%/25%/35% 的唯一权重。

- [x] **Step 4: Run static tests and verify GREEN**

Run: `node --test tests\\rules_page.test.js`

Expected: 全部通过。

### Task 6: Full verification

**Files:**
- Verify all modified files

- [x] **Step 1: Run all Python tests**

Run: `python -m unittest discover -s tests -v`

Expected: 0 failures。

- [x] **Step 2: Run all JavaScript tests**

Run: `node --test tests\\*.test.js`

Expected: 0 failures。

- [x] **Step 3: Compile and inspect diff**

Run: `python -m py_compile app.py dca_tracker\\config.py dca_tracker\\prices.py dca_tracker\\rules.py dca_tracker\\notify.py`

Run: `git diff --check`

Expected: 两条命令均退出 0；最后逐项核对已确认方案，且没有资金约束、新配置或额外指标。
