import assert from "node:assert/strict";
import test from "node:test";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const {
  createHistoryController,
  formatAmountCents,
} = require("../../static/nomina/banorte_export_history.js");

function setup(responseFactory) {
  const calls = [];
  const view = {
    open(trigger) { calls.push(["open", trigger]); },
    loading() { calls.push(["loading"]); },
    success(header, items) { calls.push(["success", header, items]); },
    empty(header) { calls.push(["empty", header]); },
    error(message) { calls.push(["error", message]); },
    close() { calls.push(["close"]); },
  };
  const requests = [];
  const controller = createHistoryController({
    view,
    async fetch(url, options) {
      requests.push({ url, options });
      return responseFactory(url, options);
    },
  });
  return { controller, calls, requests };
}

const header = {
  export_id: 77,
  filename: "DYNAMIC_HISTORY_77.pag",
  layout_date: "20260820",
  payment_count: 2,
  total_cents: 271925,
};

const items = [
  {
    position: 1,
    historical_name: "Snapshot uno",
    employee_number: "0000000011",
    account_number: "1321431243",
    amount_cents: 270000,
  },
  {
    position: 4,
    historical_name: "Snapshot dos",
    employee_number: "0000000022",
    account_number: "1321431244",
    amount_cents: 1925,
  },
];

test("controller renders loading then every historical item", async () => {
  const ctx = setup(async () => ({
    ok: true,
    async json() { return { ok: true, export: header, items }; },
  }));
  const trigger = { dataset: { exportId: "77" } };

  await ctx.controller.open(trigger);

  assert.deepEqual(ctx.calls.map((call) => call[0]), ["open", "loading", "success"]);
  assert.equal(ctx.calls[2][1], header);
  assert.equal(ctx.calls[2][2], items);
  assert.equal(ctx.requests[0].url, "/nomina/exportaciones/banorte/historial/77/movimientos");
  assert.equal(ctx.requests[0].options.credentials, "same-origin");
  assert.equal(ctx.requests[0].options.cache, "no-store");
});

test("controller exposes an empty state", async () => {
  const ctx = setup(async () => ({
    ok: true,
    async json() { return { ok: true, export: { ...header, payment_count: 0 }, items: [] }; },
  }));
  await ctx.controller.open({ dataset: { exportId: "77" } });
  assert.deepEqual(ctx.calls.map((call) => call[0]), ["open", "loading", "empty"]);
});

test("HTTP and network failures expose the error state", async () => {
  const http = setup(async () => ({
    ok: false,
    async json() { return { ok: false, code: "export_not_found" }; },
  }));
  await http.controller.open({ dataset: { exportId: "999" } });
  assert.deepEqual(http.calls.map((call) => call[0]), ["open", "loading", "error"]);

  const network = setup(async () => { throw new Error("offline"); });
  await network.controller.open({ dataset: { exportId: "77" } });
  assert.deepEqual(network.calls.map((call) => call[0]), ["open", "loading", "error"]);
});

test("controller does not truncate a large export", async () => {
  const large = Array.from({ length: 250 }, (_value, index) => ({
    ...items[0],
    position: index + 1,
  }));
  const ctx = setup(async () => ({
    ok: true,
    async json() { return { ok: true, export: { ...header, payment_count: 250 }, items: large }; },
  }));
  await ctx.controller.open({ dataset: { exportId: "77" } });
  assert.equal(ctx.calls[2][2].length, 250);
});

test("amount formatter derives display from integer cents", () => {
  const display = formatAmountCents(1925).replace(/\s/g, "");
  assert.match(display, /19[.,]25/);
});

test("close delegates focus restoration to the shared view", () => {
  const ctx = setup(async () => ({ ok: true, async json() { return {}; } }));
  ctx.controller.close();
  assert.deepEqual(ctx.calls, [["close"]]);
});
