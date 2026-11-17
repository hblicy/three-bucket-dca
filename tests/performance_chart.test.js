const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const {
  nearestPointIndex,
  returnPct,
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

test("returnPct calculates cumulative return for the selected date", () => {
  assert.equal(returnPct(1250, 1000), 25);
  assert.equal(returnPct(900, 1000), -10);
});

test("returnPct returns null when cumulative cost is zero", () => {
  assert.equal(returnPct(0, 0), null);
});

test("the chart renders the first and last dates once on both short and long series", () => {
  const page = fs.readFileSync(path.join(__dirname, "../web/index.html"), "utf8");
  const renderer = page.slice(
    page.indexOf("function drawPerformanceChart("),
    page.indexOf('$("performance-tabs").addEventListener'),
  );
  const chart = { innerHTML: "", querySelector() { return this; }, addEventListener() {} };
  const context = vm.createContext({ $: (id) => id === "performance-chart" ? chart : {}, money: String });
  vm.runInContext(renderer, context);

  for (const size of [2, 350]) {
    const start = Date.UTC(2025, 11, 3);
    const series = Array.from({ length: size }, (_, i) => ({
      date: new Date(start + i * 86400000).toISOString().slice(0, 10),
      cost: i + 1,
      value: i + 2,
    }));
    context.drawPerformanceChart(series);
    for (const { date } of [series[0], series.at(-1)]) {
      const labels = [...chart.innerHTML.matchAll(/class="axis-label[^"]*">([^<]+)<\/text>/g)];
      assert.equal(labels.filter(([, label]) => label === date.slice(5)).length, 1, date);
    }
  }
});
