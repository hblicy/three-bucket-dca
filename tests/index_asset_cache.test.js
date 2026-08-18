const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const indexHtml = fs.readFileSync(
  path.join(__dirname, "..", "web", "index.html"),
  "utf8",
);

test("chart hit area stays transparent when stylesheet cache is stale", () => {
  assert.match(
    indexHtml,
    /<rect class="chart-hit-area"[\s\S]*?fill="transparent"[\s\S]*?><\/rect>/,
  );
});

test("performance page assets use the same cache version", () => {
  const styleVersion = indexHtml.match(/href="\/static\/style\.css\?v=([^"]+)"/)?.[1];
  const commonVersion = indexHtml.match(/src="\/static\/common\.js\?v=([^"]+)"/)?.[1];
  const chartVersion = indexHtml.match(/src="\/static\/performance-chart\.js\?v=([^"]+)"/)?.[1];

  assert.ok(styleVersion, "style.css should have a cache version");
  assert.equal(commonVersion, styleVersion);
  assert.equal(chartVersion, styleVersion);
});

test("chart tooltip labels cumulative return as return", () => {
  assert.match(indexHtml, /<span>收益率 <b/);
  assert.doesNotMatch(indexHtml, /当日收益率/);
  assert.match(indexHtml, /PerformanceChart\.returnPct\(point\.value, point\.cost\)/);
});
