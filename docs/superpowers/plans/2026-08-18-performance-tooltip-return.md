# 收益曲线当日收益率 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在四个收益曲线页签的悬停提示中显示截至所选日期的累计收益率。

**Architecture:** 保持 `/api/performance` 接口不变，在 `performance-chart.js` 中集中计算 `(value - cost) / cost × 100`，由现有提示框渲染。累计投入为零时返回 `null`，交给现有 `pct()` 显示 `-`；修改静态资源后统一提升缓存版本。

**Tech Stack:** 原生 JavaScript、SVG、CSS、Node.js `node:test`、Playwright

---

### Task 1: 收益率计算

**Files:**
- Modify: `tests/performance_chart.test.js`
- Modify: `web/static/performance-chart.js`

- [ ] **Step 1: Write the failing test**

在现有导入中加入 `returnPct`，并添加：

```javascript
test("returnPct calculates cumulative return for the selected date", () => {
  assert.equal(returnPct(1250, 1000), 25);
  assert.equal(returnPct(900, 1000), -10);
});

test("returnPct returns null when cumulative cost is zero", () => {
  assert.equal(returnPct(0, 0), null);
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `node --test tests/performance_chart.test.js`

Expected: FAIL because `returnPct` is not exported.

- [ ] **Step 3: Write minimal implementation**

在 `web/static/performance-chart.js` 中加入并导出：

```javascript
function returnPct(value, cost) {
  const numericCost = Number(cost);
  if (numericCost === 0) return null;
  return (Number(value) - numericCost) / numericCost * 100;
}

return { nearestPointIndex, tooltipLeft, returnPct };
```

- [ ] **Step 4: Run test to verify it passes**

Run: `node --test tests/performance_chart.test.js`

Expected: all tests PASS.

### Task 2: 悬停提示与缓存一致性

**Files:**
- Modify: `tests/index_asset_cache.test.js`
- Modify: `web/index.html`

- [ ] **Step 1: Write the failing markup test**

在 `tests/index_asset_cache.test.js` 中添加：

```javascript
test("chart tooltip includes return for the selected date", () => {
  assert.match(indexHtml, /当日收益率/);
  assert.match(indexHtml, /PerformanceChart\.returnPct\(point\.value, point\.cost\)/);
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `node --test tests/index_asset_cache.test.js`

Expected: FAIL because the tooltip does not contain return text or calculation.

- [ ] **Step 3: Render the return value**

在 `web/index.html` 的悬停处理函数中计算并显示：

```javascript
const pointReturnPct = PerformanceChart.returnPct(point.value, point.cost);

tooltip.innerHTML = `
  <strong>${text(point.date)}</strong>
  <span><i class="tooltip-dot value"></i>组合市值 ${money(point.value)}</span>
  <span><i class="tooltip-dot cost"></i>累计投入 ${money(point.cost)}</span>
  <span>当日收益率 <b class="${colorClass(pointReturnPct)}">${pct(pointReturnPct)}</b></span>
`;
```

将 `web/index.html` 中 `style.css`、`common.js`、`performance-chart.js` 的统一查询版本从 `20260818-1` 提升为 `20260818-2`。

- [ ] **Step 4: Run all frontend tests**

Run: `node --test tests/*.test.js`

Expected: all tests PASS, including the cache-version consistency test.

### Task 3: 回归验证与发布

**Files:**
- Verify: `web/index.html`
- Verify: `web/static/performance-chart.js`
- Verify: `tests/performance_chart.test.js`
- Verify: `tests/index_asset_cache.test.js`

- [ ] **Step 1: Run the full automated test suite**

Run: `python -m unittest discover -s tests -v`

Expected: all Python tests PASS.

Run: `node --test tests/*.test.js`

Expected: all JavaScript tests PASS.

Run: `git diff --check`

Expected: no whitespace errors.

- [ ] **Step 2: Run rendered interaction QA**

使用项目现有本地 FastAPI 启动方式和仓库外临时 Playwright 脚本，拦截 `/api/dashboard` 与 `/api/performance` 返回固定数据。依次点击 `all`、`BTC_CYCLE`、`CRCL_GROWTH`、`US_INDEX_CORE`，在图表中点悬停并断言提示包含：

```text
2026-08-17
组合市值 $1,250.00
累计投入 $1,100.00
当日收益率 +13.64%
```

同时断言无相关控制台错误，并保存仓库外截图。

- [ ] **Step 3: Commit and push**

```bash
git add docs/superpowers/plans/2026-08-18-performance-tooltip-return.md tests/performance_chart.test.js tests/index_asset_cache.test.js web/index.html web/static/performance-chart.js
git commit -m "增加收益曲线当日收益率"
git push origin main
```
