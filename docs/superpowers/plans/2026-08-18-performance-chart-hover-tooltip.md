# 收益曲线悬停提示 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为全部四个收益曲线页签增加鼠标悬停提示，显示日期、组合市值和累计投入。

**Architecture:** 保留现有内联 SVG 图表绘制逻辑，新建一个无 DOM 依赖的前端计算模块，负责最近数据点索引和提示框横向定位。`web/index.html` 负责把计算结果映射到 SVG 辅助线、圆点和 HTML 提示框，`web/static/style.css` 负责视觉样式。

**Tech Stack:** 原生 JavaScript、SVG、CSS、Node.js 内置测试运行器、Python unittest、Playwright（仅使用 Codex 已捆绑运行时进行页面验证，不加入项目依赖）

---

## 文件结构

- Create: `web/static/performance-chart.js` — 提供无 DOM 依赖的悬停位置计算函数。
- Create: `tests/performance_chart.test.js` — 使用 Node.js 内置测试运行器验证最近点和提示框边界计算。
- Modify: `web/index.html` — 加载计算模块，渲染提示框与 SVG 悬停图层，绑定指针事件。
- Modify: `web/static/style.css` — 定义提示框、辅助线、数据点和交互捕获区域样式。
- Temporary: `%TEMP%/three-bucket-dca-hover-qa.cjs` — Playwright 红/绿交互验证脚本，不提交仓库。

### Task 1: 悬停位置计算模块

**Files:**
- Create: `tests/performance_chart.test.js`
- Create: `web/static/performance-chart.js`

- [ ] **Step 1: 写最近数据点和边界定位的失败测试**

```javascript
const test = require("node:test");
const assert = require("node:assert/strict");
const {
  nearestPointIndex,
  tooltipLeft,
} = require("../web/static/performance-chart.js");

test("nearestPointIndex selects the closest point", () => {
  assert.equal(nearestPointIndex(100, 100, 500, 5), 0);
  assert.equal(nearestPointIndex(249, 100, 500, 5), 1);
  assert.equal(nearestPointIndex(251, 100, 500, 5), 2);
  assert.equal(nearestPointIndex(500, 100, 500, 5), 4);
});

test("nearestPointIndex clamps positions to first and last point", () => {
  assert.equal(nearestPointIndex(20, 100, 500, 5), 0);
  assert.equal(nearestPointIndex(700, 100, 500, 5), 4);
});

test("tooltipLeft uses the right side when there is room", () => {
  assert.equal(tooltipLeft(200, 960, 180), 212);
});

test("tooltipLeft flips left and stays inside the chart", () => {
  assert.equal(tooltipLeft(900, 960, 180), 708);
  assert.equal(tooltipLeft(20, 160, 180), 8);
});
```

- [ ] **Step 2: 运行测试并确认因模块缺失而失败**

Run:

```powershell
& 'C:\Users\Administrator\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe' --test tests\performance_chart.test.js
```

Expected: FAIL，错误包含 `Cannot find module '../web/static/performance-chart.js'`。

- [ ] **Step 3: 实现最小计算模块**

```javascript
const PerformanceChart = (() => {
  function nearestPointIndex(svgX, plotLeft, plotRight, pointCount) {
    const clampedX = Math.min(plotRight, Math.max(plotLeft, svgX));
    const ratio = (clampedX - plotLeft) / (plotRight - plotLeft);
    return Math.round(ratio * (pointCount - 1));
  }

  function tooltipLeft(pointX, chartWidth, tooltipWidth) {
    const margin = 8;
    const gap = 12;
    const right = pointX + gap;
    const preferred = right + tooltipWidth <= chartWidth - margin
      ? right
      : pointX - tooltipWidth - gap;
    return Math.max(margin, Math.min(preferred, chartWidth - tooltipWidth - margin));
  }

  return { nearestPointIndex, tooltipLeft };
})();

if (typeof module !== "undefined" && module.exports) {
  module.exports = PerformanceChart;
}
```

- [ ] **Step 4: 运行测试并确认通过**

Run:

```powershell
& 'C:\Users\Administrator\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe' --test tests\performance_chart.test.js
```

Expected: 4 tests PASS，0 failures。

- [ ] **Step 5: 提交计算模块和测试**

```powershell
git add web/static/performance-chart.js tests/performance_chart.test.js
git commit -m "添加收益曲线悬停计算"
```

### Task 2: 收益曲线悬停交互

**Files:**
- Modify: `web/index.html:52-54`
- Modify: `web/index.html:62-205`
- Modify: `web/static/style.css:58-76`
- Temporary: `%TEMP%/three-bucket-dca-hover-qa.cjs`

- [ ] **Step 1: 创建真实页面交互测试并确认当前实现失败**

在 `%TEMP%/three-bucket-dca-hover-qa.cjs` 创建临时 Playwright 脚本。脚本在打开页面前拦截 `/api/dashboard` 和 `/api/performance`，为四个 scope 返回相同的三点确定性曲线数据；依次点击四个页签，把鼠标移动到 SVG 中点，并断言 `#performance-tooltip` 可见且包含 `2026-08-17`、`$1,250.00`、`$1,100.00`。

Run:

```powershell
& 'C:\Users\Administrator\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe' $env:TEMP\three-bucket-dca-hover-qa.cjs
```

Expected: FAIL，原因是 `#performance-tooltip` 不存在。

- [ ] **Step 2: 增加提示框节点和计算模块引用**

在 `.chart-wrap` 内、SVG 后增加：

```html
<div class="chart-tooltip" id="performance-tooltip" hidden></div>
```

在 `common.js` 后、内联脚本前增加：

```html
<script src="/static/performance-chart.js"></script>
```

- [ ] **Step 3: 在 SVG 最上层增加悬停图层和透明捕获区域**

在 `drawPerformanceChart()` 生成的 SVG 模板末尾增加：

```html
<g class="chart-hover" hidden>
  <line class="chart-hover-line" y1="${pad.top}" y2="${h - pad.bottom}"></line>
  <circle class="chart-hover-dot value-dot" r="4"></circle>
  <circle class="chart-hover-dot cost-dot" r="4"></circle>
</g>
<rect class="chart-hit-area" x="${pad.left}" y="${pad.top}"
  width="${w - pad.left - pad.right}" height="${h - pad.top - pad.bottom}"></rect>
```

- [ ] **Step 4: 绑定指针移动和离开事件**

在 SVG 模板赋值后查询悬停元素。`pointermove` 将浏览器坐标按 `viewBox` 宽度换算成 SVG 横坐标，调用 `PerformanceChart.nearestPointIndex()`，更新辅助线和两个圆点坐标；提示框内容使用现有 `text()` 和 `money()` 格式化。

```javascript
const hitArea = svg.querySelector(".chart-hit-area");
const hover = svg.querySelector(".chart-hover");
const hoverLine = hover.querySelector(".chart-hover-line");
const valueDot = hover.querySelector(".value-dot");
const costDot = hover.querySelector(".cost-dot");

const hideHover = () => {
  hover.hidden = true;
  tooltip.hidden = true;
};

hitArea.addEventListener("pointermove", (event) => {
  const bounds = svg.getBoundingClientRect();
  const svgX = (event.clientX - bounds.left) * w / bounds.width;
  const idx = PerformanceChart.nearestPointIndex(svgX, pad.left, w - pad.right, points.length);
  const point = points[idx];
  const pointX = x(idx);

  hoverLine.setAttribute("x1", pointX);
  hoverLine.setAttribute("x2", pointX);
  valueDot.setAttribute("cx", pointX);
  valueDot.setAttribute("cy", y(Number(point.value)));
  costDot.setAttribute("cx", pointX);
  costDot.setAttribute("cy", y(Number(point.cost)));
  hover.hidden = false;

  tooltip.innerHTML = `
    <strong>${text(point.date)}</strong>
    <span><i class="tooltip-dot value"></i>组合市值 ${money(point.value)}</span>
    <span><i class="tooltip-dot cost"></i>累计投入 ${money(point.cost)}</span>
  `;
  tooltip.hidden = false;
  const pointCssX = pointX * bounds.width / w;
  tooltip.style.left = `${PerformanceChart.tooltipLeft(pointCssX, bounds.width, tooltip.offsetWidth)}px`;
  tooltip.style.top = "12px";
});

hitArea.addEventListener("pointerleave", hideHover);
```

在 `drawPerformanceChart()` 开头先隐藏提示框，确保切换页签或空数据时不残留旧状态：

```javascript
const tooltip = $("performance-tooltip");
tooltip.hidden = true;
```

- [ ] **Step 5: 增加悬停视觉样式**

```css
.chart-tooltip {
  position: absolute;
  z-index: 2;
  min-width: 180px;
  padding: 10px 12px;
  border: 1px solid rgba(88,166,255,.45);
  border-radius: 8px;
  background: rgba(10,15,22,.94);
  box-shadow: 0 8px 24px rgba(0,0,0,.35);
  pointer-events: none;
}
.chart-tooltip[hidden], .chart-hover[hidden] { display: none; }
.chart-tooltip strong { display: block; margin-bottom: 8px; }
.chart-tooltip span { display: block; color: #c9d8e8; font-size: 12px; line-height: 1.8; }
.tooltip-dot { display: inline-block; width: 7px; height: 7px; margin-right: 7px; border-radius: 50%; }
.tooltip-dot.value { background: var(--green); }
.tooltip-dot.cost { background: var(--red); }
.chart-hover-line { stroke: rgba(183,216,255,.55); stroke-width: 1; stroke-dasharray: 3 4; }
.chart-hover-dot.value-dot { fill: var(--green); stroke: #0d141d; stroke-width: 2; }
.chart-hover-dot.cost-dot { fill: var(--red); stroke: #0d141d; stroke-width: 2; }
.chart-hit-area { fill: transparent; pointer-events: all; cursor: crosshair; }
```

- [ ] **Step 6: 运行真实页面交互测试并确认四个页签全部通过**

Run:

```powershell
& 'C:\Users\Administrator\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe' $env:TEMP\three-bucket-dca-hover-qa.cjs
```

Expected: PASS；四个 scope 均显示正确日期、组合市值和累计投入，鼠标离开后提示框隐藏。

- [ ] **Step 7: 运行前端计算测试和现有 Python 测试**

```powershell
& 'C:\Users\Administrator\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe' --test tests\performance_chart.test.js
python -m unittest discover -s tests -v
```

Expected: Node 测试 4/4 PASS；Python 测试 6/6 PASS。

- [ ] **Step 8: 提交悬停交互**

```powershell
git add web/index.html web/static/style.css
git commit -m "增加收益曲线悬停提示"
```

### Task 3: 最终页面验证

**Files:**
- Verify: `web/index.html`
- Verify: `web/static/style.css`
- Verify: `web/static/performance-chart.js`

- [ ] **Step 1: 启动本地应用并核对页面身份**

Run:

```powershell
python app.py
```

Expected: `http://127.0.0.1:8020/` 返回页面标题“三仓定投计划”，收益曲线不是空白页，也没有框架错误覆盖层。

- [ ] **Step 2: 使用 Playwright 检查悬停交互和控制台**

使用同一个临时 Playwright 脚本核对：

- 页面标题正确；
- 四个页签均可切换；
- 图表中点悬停后提示框可见；
- 日期、组合市值、累计投入内容正确；
- 辅助线、绿色圆点、红色圆点可见；
- 鼠标移出图表后提示框隐藏；
- 控制台无相关 error 或 warning。

- [ ] **Step 3: 保存桌面截图到临时目录**

保存悬停状态截图到 `%TEMP%/three-bucket-dca-hover.png`，不写入仓库。截图必须显示提示框、竖向辅助线和两个数据点。

- [ ] **Step 4: 最终检查 Git 状态和提交记录**

```powershell
git status -sb
git log --oneline -4
```

Expected: 工作树干净；最新提交包含“添加收益曲线悬停计算”和“增加收益曲线悬停提示”。
