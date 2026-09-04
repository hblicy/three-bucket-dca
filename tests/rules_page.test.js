const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

function read(relativePath) {
  return fs.readFileSync(path.join(__dirname, "..", relativePath), "utf8");
}

const rulesPage = read(path.join("web", "rules.html"));
const appSource = read("app.py");
const legacyPlan = read(path.join("方案", "方案.md"));
const stylesheet = read(path.join("web", "static", "style.css"));

test("rules keep the confirmed investment horizons and BTC weights", () => {
  assert.match(rulesPage, /BTC 和 CRCL 的执行周期为 4 年/);
  assert.match(rulesPage, /QQQM \+ VOO 的持有周期为 10 年以上/);
  assert.match(appSource, /40% \/ 25% \/ 35%/);
});

test("CRCL rules describe only the price score and USDC hard pause", () => {
  assert.match(rulesPage, /0[–-]85/);
  assert.match(rulesPage, /0[–-]100/);
  assert.match(rulesPage, /USDC 24 小时/);
  assert.match(rulesPage, /超过 5%[\s\S]*暂停/);
  assert.doesNotMatch(rulesPage, /基本面预估|基本面评分口径|renderFundamentals|fund-score/);
});

test("US index rules do not claim an unimplemented cash pool", () => {
  assert.doesNotMatch(rulesPage, /现金\/短债池/);
});

test("rule ranges state their open and closed boundaries", () => {
  assert.match(rulesPage, /&gt; P10 且 ≤ P25/);
  assert.match(rulesPage, /&gt; P25 且 ≤ P50/);
  assert.match(rulesPage, /&gt; P50 且 ≤ P75/);
  assert.match(rulesPage, /&gt; 25 且 ≤ 45/);
  assert.match(rulesPage, /&gt; 45 且 ≤ 65/);
});

test("obsolete four-bucket plan is explicitly archived", () => {
  assert.match(legacyPlan, /^\uFEFF?# 历史方案（已归档）/);
  assert.match(legacyPlan, /不作为现行规则依据/);
});

test("removed CRCL fundamentals do not leave FRED configuration", () => {
  const configText = [
    read(".env.example"),
    read(path.join("deploy", "three-bucket-dca.env.example")),
    read("README.md"),
    read("app.py"),
    read(path.join("web", "settings.html")),
  ].join("\n");
  assert.doesNotMatch(configText, /FRED_API_KEY|CRCL_FUNDAMENTALS_CACHE_TTL|fred_configured/);
});

test("CRCL drawdown label matches the implemented calculation", () => {
  assert.match(rulesPage, /距 90 日高点回撤/);
  assert.doesNotMatch(rulesPage, /90 日最大回撤/);
});

test("rules page reports dashboard loading failures", () => {
  assert.doesNotMatch(rulesPage, /\.catch\(\(\) => \{\}\)/);
  assert.match(rulesPage, /console\.error/);
});

test("removed CRCL fundamentals leave no dead stylesheet selectors", () => {
  assert.doesNotMatch(stylesheet, /fundamentals-live|fund-head|fund-grid|fund-item/);
});
