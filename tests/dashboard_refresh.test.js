const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const indexHtml = fs.readFileSync(
  path.join(__dirname, "..", "web", "index.html"),
  "utf8",
);
const pageScript = indexHtml.match(/<script>([\s\S]*?)<\/script>/)[1];
const loaders = pageScript.slice(0, pageScript.indexOf("function drawPerformanceChart"));
const refreshAction = indexHtml.match(/<button onclick="([^"]+)">刷新<\/button>/)[1];

function createPage() {
  const requests = [];
  const elements = new Map();
  const context = vm.createContext({
    $: (id) => {
      if (!elements.has(id)) elements.set(id, {});
      return elements.get(id);
    },
    document: { querySelectorAll: () => [] },
    money: String,
    pct: String,
    text: String,
    drawPerformanceChart: () => {},
    api: async (url) => {
      requests.push(url);
      if (url.startsWith("/api/dashboard")) {
        return {
          has_errors: false,
          decisions: [],
          portfolio: { price_data_ok: true, buckets: [], assets: [] },
        };
      }
      const refreshed = new URL(url, "http://localhost").searchParams.get("refresh") === "true";
      return {
        data_ok: refreshed,
        errors: refreshed ? [] : [{ asset: "CRCL", message: "Invalid latest price for CRCL" }],
        summary: {},
        series: [],
      };
    },
  });
  vm.runInContext(loaders, context);
  return { context, requests, elements };
}

test("refresh button rebuilds both dashboard and the selected performance curve", async () => {
  const page = createPage();
  await page.context.loadPerformance("CRCL_GROWTH");
  assert.equal(page.elements.get("performance-warning").hidden, false);
  page.requests.length = 0;

  await vm.runInContext(refreshAction, page.context);

  assert.deepEqual(page.requests, [
    "/api/dashboard?refresh=true",
    "/api/performance?scope=CRCL_GROWTH&refresh=true",
  ]);
  assert.equal(page.elements.get("performance-warning").hidden, true);
  assert.equal(page.elements.get("performance-warning").textContent, "");
});

test("initial load and switching performance tabs keep using the cache", async () => {
  const page = createPage();
  await page.context.load();
  await page.context.loadPerformance("US_INDEX_CORE");

  assert.deepEqual(page.requests, [
    "/api/dashboard",
    "/api/performance?scope=all",
    "/api/performance?scope=US_INDEX_CORE",
  ]);
});
