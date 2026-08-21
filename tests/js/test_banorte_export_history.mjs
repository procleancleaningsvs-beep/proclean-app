import assert from "node:assert/strict";
import test from "node:test";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const {
  createHistoryController,
  filterAndSortMovements,
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

const searchableItems = [
  {
    position: 7,
    historical_name: "Álvaro Núñez",
    employee_number: "0000000010",
    account_number: "1321431243",
    amount_cents: 230000,
  },
  {
    position: 2,
    historical_name: "beatriz López",
    employee_number: "0000000002",
    account_number: "1321431244",
    amount_cents: 1925,
  },
  {
    position: 9,
    historical_name: "Carlos Pérez",
    employee_number: "0000000100",
    account_number: "1321431245",
    amount_cents: 230000,
  },
];

function positions(rows) {
  return rows.map((item) => item.position);
}

test("live search matches historical names case- and accent-insensitively", () => {
  assert.deepEqual(positions(filterAndSortMovements(searchableItems, "ALVARO", "position")), [7]);
  assert.deepEqual(positions(filterAndSortMovements(searchableItems, "nunez", "position")), [7]);
});

test("live search matches complete and partial employee numbers without stripping zeros", () => {
  assert.deepEqual(
    positions(filterAndSortMovements(searchableItems, "0000000010", "position")),
    [7],
  );
  assert.deepEqual(positions(filterAndSortMovements(searchableItems, "0010", "position")), [7, 9]);
});

test("amount search treats common currency spellings as the same integer cents", () => {
  for (const query of ["2300", "2300.00", "$2,300.00"]) {
    assert.deepEqual(positions(filterAndSortMovements(searchableItems, query, "position")), [7, 9]);
  }
});

test("name sorting supports A-Z and Z-A with Spanish base sensitivity", () => {
  assert.deepEqual(positions(filterAndSortMovements(searchableItems, "", "name_asc")), [7, 2, 9]);
  assert.deepEqual(positions(filterAndSortMovements(searchableItems, "", "name_desc")), [9, 2, 7]);
});

test("employee sorting compares numeric identifiers without changing their representation", () => {
  const asc = filterAndSortMovements(searchableItems, "", "employee_asc");
  const desc = filterAndSortMovements(searchableItems, "", "employee_desc");
  assert.deepEqual(positions(asc), [2, 7, 9]);
  assert.deepEqual(positions(desc), [9, 7, 2]);
  assert.deepEqual(asc.map((item) => item.employee_number), ["0000000002", "0000000010", "0000000100"]);
});

test("amount sorting uses amount_cents in both directions", () => {
  assert.deepEqual(positions(filterAndSortMovements(searchableItems, "", "amount_asc")), [2, 7, 9]);
  assert.deepEqual(positions(filterAndSortMovements(searchableItems, "", "amount_desc")), [7, 9, 2]);
});

test("original order restores position and never renumbers historical rows", () => {
  const rows = filterAndSortMovements(searchableItems, "", "position");
  assert.deepEqual(positions(rows), [2, 7, 9]);
  assert.deepEqual(positions(searchableItems), [7, 2, 9]);
});

test("search is applied before sort and clearing it restores every item", () => {
  assert.deepEqual(
    positions(filterAndSortMovements(searchableItems, "2300", "name_desc")),
    [9, 7],
  );
  assert.deepEqual(filterAndSortMovements(searchableItems, "", "position").length, 3);
});
