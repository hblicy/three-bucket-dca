const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { spawnSync } = require("node:child_process");

function read(relativePath) {
  return fs.readFileSync(path.join(__dirname, "..", relativePath), "utf8");
}

const appService = read(path.join("deploy", "three-bucket-dca.service"));
const updateService = read(path.join("deploy", "three-bucket-dca-update.service"));
const readme = read("README.md");
const gitignore = read(".gitignore");
const safeUpdateScript = read(path.join("scripts", "safe_update.sh"));
const root = "/home/ubuntu/three-bucket-dca";
const envFile = `${root}/three-bucket-dca.env`;

test("systemd services use the active ubuntu deployment paths", () => {
  for (const service of [appService, updateService]) {
    assert.match(service, /^User=ubuntu$/m);
    assert.match(service, /^Group=ubuntu$/m);
    assert.match(service, new RegExp(`^EnvironmentFile=${envFile}$`, "m"));
    assert.match(service, new RegExp(`^WorkingDirectory=${root}$`, "m"));
  }
  assert.match(appService, new RegExp(`^ExecStart=${root}/\\.venv/bin/python`, "m"));
});

test("deployment documentation uses the active project and environment paths", () => {
  assert.match(readme, new RegExp(root, "g"));
  assert.match(readme, new RegExp(envFile, "g"));
});

test("the active VPS environment file is ignored by git", () => {
  assert.match(gitignore, /^three-bucket-dca\.env$/m);
});

test("safe update stops every writer before backing up the database", () => {
  const stopIndex = safeUpdateScript.indexOf("systemctl stop");
  const backupIndex = safeUpdateScript.indexOf("scripts/backup_for_update.py");
  assert.notEqual(stopIndex, -1);
  assert.notEqual(backupIndex, -1);
  assert.ok(stopIndex < backupIndex);
});

test("safe update script fails closed before pulling code", () => {
  const scriptPath = path.join(__dirname, "..", "scripts", "safe_update.sh");
  assert.equal(fs.existsSync(scriptPath), true, "scripts/safe_update.sh must exist");
  const script = safeUpdateScript;
  assert.match(script, /^set -euo pipefail$/m);
  assert.match(script, /systemctl show --property=ActiveState --value/);
  assert.match(script, /scripts\/backup_for_update\.py/);
  assert.match(script, /\[\[ -f "\$backup_path" \]\]/);

  const stopIndex = script.indexOf("systemctl stop");
  const verifyIndex = script.indexOf('verify_inactive "$unit"', stopIndex);
  const backupIndex = script.indexOf("scripts/backup_for_update.py");
  const pullIndex = script.indexOf("git pull --ff-only");
  assert.ok(stopIndex !== -1 && stopIndex < verifyIndex);
  assert.ok(verifyIndex < backupIndex);
  assert.ok(backupIndex < pullIndex);
  assert.doesNotMatch(script, /migrate_legacy|migrate_all/);
});

test("safe update does not pull when a prerequisite fails", {
  skip: process.platform === "win32" ? "validated through WSL on Windows" : false,
}, () => {
  const result = spawnSync("bash", [
    path.join(__dirname, "safe_update_execution.test.sh"),
    path.join(__dirname, "..", "scripts", "safe_update.sh"),
  ], { encoding: "utf8" });
  assert.equal(result.status, 0, result.stderr || result.stdout);
});

test("unfinalized BTC halving state is checked by a dedicated one-minute timer", () => {
  const servicePath = path.join("deploy", "three-bucket-dca-halving.service");
  const timerPath = path.join("deploy", "three-bucket-dca-halving.timer");
  assert.equal(fs.existsSync(path.join(__dirname, "..", servicePath)), true);
  assert.equal(fs.existsSync(path.join(__dirname, "..", timerPath)), true);

  const service = read(servicePath);
  const timer = read(timerPath);
  assert.match(service, /^User=ubuntu$/m);
  assert.match(service, new RegExp(`^WorkingDirectory=${root}$`, "m"));
  assert.match(service, /scripts\/update_btc_halving\.py$/m);
  assert.match(timer, /^OnUnitInactiveSec=1min$/m);
  assert.doesNotMatch(timer, /^OnUnitActiveSec=/m);
  assert.match(timer, /^Unit=three-bucket-dca-halving\.service$/m);

  assert.match(safeUpdateScript, /three-bucket-dca-halving\.timer/);
  assert.match(safeUpdateScript, /three-bucket-dca-halving\.service/);
  assert.match(safeUpdateScript, /cp deploy\/three-bucket-dca-halving\.service/);
  assert.match(safeUpdateScript, /cp deploy\/three-bucket-dca-halving\.timer/);
  assert.match(safeUpdateScript, /systemctl enable three-bucket-dca-halving\.timer/);
  assert.match(safeUpdateScript, /systemctl start[\s\S]*three-bucket-dca-halving\.timer/);
});
