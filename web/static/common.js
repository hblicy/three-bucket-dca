const $ = (id) => document.getElementById(id);
const fmt = (n, d = 2) => n === null || n === undefined || Number.isNaN(Number(n)) ? "-" : Number(n).toLocaleString("en-US", { minimumFractionDigits: d, maximumFractionDigits: d });
const money = (n) => n === null || n === undefined || Number.isNaN(Number(n)) ? "-" : `$${fmt(n)}`;
const pct = (n) => n === null || n === undefined || Number.isNaN(Number(n)) ? "-" : `${Number(n) >= 0 ? "+" : ""}${fmt(n)}%`;
const text = (v) => String(v ?? "").replace(/[&<>"']/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]));

async function api(path, options = {}) {
  const opts = { ...options };
  const method = (opts.method || "GET").toUpperCase();
  opts.headers = { ...(opts.headers || {}) };
  const readToken = sessionStorage.getItem("dca_read_token");
  if (readToken && method === "GET" && path.startsWith("/api/")) {
    opts.headers["X-DCA-Read-Token"] = readToken;
  }
  let res = await fetch(path, opts);
  if (res.status === 403 && method === "GET" && path.startsWith("/api/")) {
    const token = prompt("请输入读操作 token");
    if (token) {
      sessionStorage.setItem("dca_read_token", token);
      opts.headers["X-DCA-Read-Token"] = token;
      res = await fetch(path, opts);
    }
  }
  if (!res.ok) {
    const raw = await res.text();
    try {
      const data = JSON.parse(raw);
      const detail = Array.isArray(data.detail)
        ? data.detail.map(item => item.msg || JSON.stringify(item)).join("；")
        : data.detail;
      throw new Error(detail || raw);
    } catch (err) {
      if (err instanceof SyntaxError) throw new Error(raw);
      throw err;
    }
  }
  return res.json();
}

function tokenHeaders() {
  const token = prompt("请输入写操作 token");
  if (!token) throw new Error("未输入 token");
  return { "Content-Type": "application/json", "X-DCA-Token": token };
}
