const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const pageHtml = fs.readFileSync(path.join(__dirname, "..", "web", "index.html"), "utf8");
const commonJs = fs.readFileSync(path.join(__dirname, "..", "web", "static", "common.js"), "utf8");
const pageScript = pageHtml.match(/<script>([\s\S]*?)<\/script>/)[1];
const loaders = pageScript.slice(0, pageScript.indexOf("function drawPerformanceChart"));

async function render(reference, quote) {
  const elements = new Map();
  const context = vm.createContext({
    document: {
      getElementById: (id) => {
        if (!elements.has(id)) elements.set(id, {});
        return elements.get(id);
      },
      querySelectorAll: () => [],
    },
    drawPerformanceChart: () => {},
  });
  vm.runInContext(commonJs, context);
  vm.runInContext(loaders, context);
  context.api = async (url) => url.startsWith("/api/dashboard") ? {
    has_errors: false,
    monthly_plan: 100,
    strategy_btc: reference,
    decisions: ["BTC", "CRCL", "QQQM", "VOO"].map(asset => ({
      asset,
      metrics: asset === "BTC" && quote ? { spot_quote: quote } : {},
      title: asset,
      status: "buy",
      action: "正常定投",
      price: 82002.12,
      recommended_amount: 100,
      reason: "DCA Score 33.5，倍率 1x。",
      next_window: "每周一 16:00 北京时间",
    })),
    portfolio: { price_data_ok: true, buckets: [], assets: [] },
  } : { summary: {}, series: [] };
  await context.load();
  return elements.get("decisions").innerHTML;
}

const reference = {
  avg_cost_usd: 75446,
  as_of: "2026-10-19",
  source_url: "https://www.strategy.com/ledger",
  stale: false,
  error: null,
};

test("BTC reference appears between DCA Score and the schedule, only once", async () => {
  const html = await render(reference);
  assert.equal((html.match(/微策略 BTC 持仓均价/g) || []).length, 1);
  const position = html.indexOf("微策略 BTC 持仓均价");
  assert.ok(position > html.indexOf("DCA Score 33.5"));
  assert.ok(position < html.indexOf("每周一 16:00 北京时间"));
  assert.match(html, /<strong>\$75,446\.00<\/strong>/);
  assert.match(html, /披露于 2026-10-19/);
  assert.match(html, /href="https:\/\/www\.strategy\.com\/ledger"/);
  assert.match(html, /Strategy 官方账本/);
});

test("stale reference keeps the amount and explicitly reports the failed update", async () => {
  const html = await render({ ...reference, stale: true, error: { code: "source_unavailable" } });
  assert.match(html, /\$75,446\.00/);
  assert.match(html, /更新失败，显示上次数据/);
});

test("no reference shows unavailable instead of a zero or hard-coded cost", async () => {
  for (const value of [undefined, { avg_cost_usd: null, stale: true }]) {
    const html = await render(value);
    assert.match(html, /微策略 BTC 持仓均价/);
    assert.match(html, /暂无数据/);
    assert.doesNotMatch(html, /\$75,446|持仓均价：\s*<strong>\$0/);
  }
});

test("source values are escaped before being inserted into HTML", async () => {
  const html = await render({ ...reference, as_of: '<img src=x onerror="alert(1)">' });
  assert.doesNotMatch(html, /<img/);
  assert.match(html, /&lt;img/);
});


test("previous cached disclosure preserves its original date meaning", async () => {
  const html = await render({ ...reference, avg_cost_usd: 75444, as_of: "2026-10-11", source_url: "https://www.strategy.com/press/previous", stale: true });
  assert.match(html, /截至 2026-10-11/);
  assert.match(html, /Strategy 官方披露/);
  assert.doesNotMatch(html, /链研社|披露于 2026-10-11/);
});

const spotQuote = { price: 82002.12, symbol: "BTCUSDT", source: "Binance Spot", quote_asset: "USDT", updated_at: "2026-10-26T08:00:00+00:00" };

test("BTC spot price uses USDT without showing source and collection details", async () => {
  const html = await render(reference, spotQuote);
  assert.match(html, /价格：82,002\.12 USDT/);
  assert.doesNotMatch(html, /币安现货 BTC\/USDT|取价|2026.*10.*26.*16:00:00/);
  assert.match(html, /价格：\$82,002\.12/);
});

test("previous cached Yahoo decisions are not relabeled as Binance quotes", async () => {
  const html = await render(reference);
  assert.match(html, /价格：\$82,002\.12/);
  assert.doesNotMatch(html, /币安现货|82,002\.12 USDT/);
});
