import assert from "node:assert/strict";
import test from "node:test";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const grid = require("../../static/nomina/banorte_payment_grid.js");

const {
  parsePasteMatrix,
  trimCell,
  isMonetaryAmount,
  classifyCell,
  evaluateRow,
  resolveKeyboardMove,
  EDITABLE_COLUMNS,
  applySingleColumnPasteToModel,
  isSingleColumnPaste,
  extractPasteMatrix,
  installGridPasteListener,
} = grid;

function makeRow(partial) {
  const row = {
    client_row_key: "k-" + Math.random(),
    position: 1,
    name_raw: partial.name_raw || "",
    amount_raw: partial.amount_raw || "",
    catalog_person_id: null,
    account_display: partial.account_display || "",
    state: "OK",
    observation_codes: partial.observation_codes || [],
  };
  evaluateRow(row);
  return row;
}

function createRowFactory() {
  let position = 1;
  return function createRow(partial) {
    const row = makeRow(partial || {});
    row.position = position++;
    return row;
  };
}

function pad2(n) {
  return String(n).padStart(2, "0");
}

function buildNames(count, prefix) {
  prefix = prefix || "Nombre";
  const lines = [];
  for (let i = 1; i <= count; i += 1) lines.push(prefix + " " + pad2(i));
  return lines;
}

function buildAmounts(start, count) {
  const lines = [];
  for (let i = 0; i < count; i += 1) lines.push(String(start + i));
  return lines;
}

function createMockPasteRoot() {
  const listeners = [];
  return {
    addEventListener(type, fn, capture) {
      listeners.push({ type: type, fn: fn, capture: !!capture });
    },
    removeEventListener(type, fn, capture) {
      for (let i = listeners.length - 1; i >= 0; i -= 1) {
        const item = listeners[i];
        if (item.type === type && item.fn === fn && item.capture === !!capture) {
          listeners.splice(i, 1);
        }
      }
    },
    pasteListenerCount() {
      return listeners.filter(function (item) { return item.type === "paste"; }).length;
    },
    dispatchPaste(text, target) {
      const event = {
        clipboardData: { getData: function () { return text; } },
        target: target || this,
        preventDefault: function () {},
      };
      listeners.forEach(function (item) {
        if (item.type === "paste") item.fn(event);
      });
    },
  };
}

function rowsWithDefaultBlank() {
  return [makeRow({})];
}

function pasteNamesThenAmounts(rows, nameCount, amountStart, amountExplicit) {
  const createRow = createRowFactory();
  applySingleColumnPasteToModel(
    rows,
    buildNames(nameCount),
    { rowIndex: 0, column: "name", explicit: true },
    createRow
  );
  applySingleColumnPasteToModel(
    rows,
    buildAmounts(amountStart, nameCount),
    { rowIndex: 0, column: "amount", explicit: amountExplicit !== false },
    createRow
  );
  return rows;
}

function countPopulated(rows, field) {
  return rows.filter(function (row) { return trimCell(row[field]); }).length;
}

function rowAt(text, index) {
  const rows = parsePasteMatrix(text);
  assert.ok(rows.length > index, "expected at least " + (index + 1) + " rows");
  return rows[index];
}

test("paste Nombre<TAB>2300", () => {
  const row = rowAt("Juan Perez\t2300", 0);
  assert.equal(row.name_raw, "Juan Perez");
  assert.equal(row.amount_raw, "2300");
});

test("paste multiple rows", () => {
  const rows = parsePasteMatrix("Juan Perez\t2300\nPedro Lopez\t2500");
  assert.equal(rows.length, 2);
  assert.equal(rows[0].name_raw, "Juan Perez");
  assert.equal(rows[0].amount_raw, "2300");
  assert.equal(rows[1].name_raw, "Pedro Lopez");
  assert.equal(rows[1].amount_raw, "2500");
});

test("paste inverted columns", () => {
  const row = rowAt("2300\tJuan Perez", 0);
  assert.equal(row.name_raw, "Juan Perez");
  assert.equal(row.amount_raw, "2300");
});

test("paste accepts $2,300.00", () => {
  const row = rowAt("Pedro Lopez\t$2,300.00", 0);
  assert.equal(row.name_raw, "Pedro Lopez");
  assert.equal(row.amount_raw, "$2,300.00");
  assert.equal(isMonetaryAmount("$2,300.00"), true);
});

test("paste trims exterior whitespace", () => {
  const row = rowAt("  Juan Perez \t 2300.00 ", 0);
  assert.equal(row.name_raw, "Juan Perez");
  assert.equal(row.amount_raw, "2300.00");
});

test("fully empty row is removed", () => {
  const rows = parsePasteMatrix("Juan Perez\t2300\n   \nPedro Lopez\t2700");
  assert.equal(rows.length, 2);
  assert.equal(rows[1].name_raw, "Pedro Lopez");
});

test("partially empty row is preserved", () => {
  const rows = parsePasteMatrix("Juan Perez\t2300\n\t2500\nPedro Lopez\t2700");
  assert.equal(rows.length, 3);
  assert.equal(rows[1].name_raw, "");
  assert.equal(rows[1].amount_raw, "2500");
  assert.equal(rows[2].name_raw, "Pedro Lopez");
});

test("single column names only", () => {
  const rows = parsePasteMatrix("Juan Perez\nPedro Lopez");
  assert.equal(rows[0].name_raw, "Juan Perez");
  assert.equal(rows[0].amount_raw, "");
  assert.equal(rows[1].name_raw, "Pedro Lopez");
});

test("single column amounts only", () => {
  const rows = parsePasteMatrix("2300\n2500.00");
  assert.equal(rows[0].name_raw, "");
  assert.equal(rows[0].amount_raw, "2300");
  assert.equal(rows[1].amount_raw, "2500.00");
});

test("single column mixed content classifies each row", () => {
  const rows = parsePasteMatrix("Juan Perez\n2300\nPedro Lopez");
  assert.equal(rows[0].name_raw, "Juan Perez");
  assert.equal(rows[1].name_raw, "");
  assert.equal(rows[1].amount_raw, "2300");
  assert.equal(rows[2].name_raw, "Pedro Lopez");
});

test("ambiguous two-name row is not auto-guessed", () => {
  const row = rowAt("Juan Perez\tPedro Lopez", 0);
  assert.equal(row.name_raw, "Juan Perez");
  assert.equal(row.amount_raw, "Pedro Lopez");
  assert.ok(row.observation_codes.includes("PASTE_AMBIGUOUS"));
});

test("paste 100 rows", () => {
  const lines = [];
  for (let i = 1; i <= 100; i += 1) lines.push("Persona " + i + "\t" + i + ".00");
  const rows = parsePasteMatrix(lines.join("\n"));
  assert.equal(rows.length, 100);
  assert.equal(rows[99].name_raw, "Persona 100");
  assert.equal(rows[99].amount_raw, "100.00");
});

test("paste 500 rows", () => {
  const lines = [];
  for (let i = 1; i <= 500; i += 1) lines.push("Persona " + i + "\t" + i + ".00");
  const rows = parsePasteMatrix(lines.join("\n"));
  assert.equal(rows.length, 500);
  assert.equal(rows[499].amount_raw, "500.00");
});

test("ArrowUp moves to previous row same column", () => {
  const move = resolveKeyboardMove("ArrowUp", "name", 2, 5, 0, 0, 4);
  assert.deepEqual(move, { rowIndex: 1, column: "name" });
});

test("ArrowDown moves to next row same column", () => {
  const move = resolveKeyboardMove("ArrowDown", "amount", 1, 5, 0, 0, 6);
  assert.deepEqual(move, { rowIndex: 2, column: "amount" });
});

test("Enter moves down in same column", () => {
  const move = resolveKeyboardMove("Enter", "name", 0, 3, 0, 0, 5);
  assert.deepEqual(move, { rowIndex: 1, column: "name" });
});

test("Shift+Enter moves up in same column", () => {
  const move = resolveKeyboardMove("ShiftEnter", "amount", 2, 3, 0, 0, 4);
  assert.deepEqual(move, { rowIndex: 1, column: "amount" });
});

test("Tab moves to next editable column", () => {
  const move = resolveKeyboardMove("Tab", "name", 0, 3, 0, 0, 4);
  assert.deepEqual(move, { rowIndex: 0, column: "amount" });
});

test("Shift+Tab moves to previous editable column", () => {
  const move = resolveKeyboardMove("ShiftTab", "amount", 0, 3, 0, 0, 4);
  assert.deepEqual(move, { rowIndex: 0, column: "name" });
});

test("ArrowLeft at caret start moves to previous editable cell", () => {
  const move = resolveKeyboardMove("ArrowLeft", "amount", 0, 3, 0, 0, 6);
  assert.deepEqual(move, { rowIndex: 0, column: "name" });
});

test("ArrowRight at caret end moves to next editable cell", () => {
  const move = resolveKeyboardMove("ArrowRight", "name", 0, 3, 4, 4, 4);
  assert.deepEqual(move, { rowIndex: 0, column: "amount" });
});

test("ArrowRight inside text does not move focus", () => {
  const move = resolveKeyboardMove("ArrowRight", "name", 0, 3, 1, 1, 4);
  assert.equal(move, null);
});

test("account column is not part of editable navigation order", () => {
  assert.deepEqual(EDITABLE_COLUMNS, ["name", "amount"]);
});

test("names with letters are never classified as amount", () => {
  assert.equal(classifyCell("Juan Perez"), "NAME");
  assert.equal(classifyCell("Ma. de Jesus Cabral"), "NAME");
  assert.equal(isMonetaryAmount("Ma. de Jesus Cabral"), false);
});

test("evaluateRow marks partial paste row as NEEDS_REVIEW", () => {
  const row = { name_raw: "", amount_raw: "2500", observation_codes: [] };
  evaluateRow(row);
  assert.equal(row.state, "NEEDS_REVIEW");
  assert.ok(row.observation_codes.includes("NAME_EMPTY"));
});

test("normalize CRLF and CR line endings", () => {
  const rows = parsePasteMatrix("Juan Perez\t2300\r\nPedro Lopez\t2500\rMaria\t1800");
  assert.equal(rows.length, 3);
  assert.equal(rows[2].name_raw, "Maria");
});

test("tab-only row is removed", () => {
  const rows = parsePasteMatrix("Juan Perez\t2300\n\t\nPedro Lopez\t2700");
  assert.equal(rows.length, 2);
});

test("whitespace-only cells trim to empty but keep row alignment", () => {
  assert.equal(trimCell("     "), "");
  const row = rowAt("\t2500", 0);
  assert.equal(row.name_raw, "");
  assert.equal(row.amount_raw, "2500");
});

test("mandatory: 16 names then 16 amounts remain 16 rows aligned by index", () => {
  const rows = [];
  const createRow = createRowFactory();
  const names = buildNames(16);
  const amounts = buildAmounts(1001, 16);

  applySingleColumnPasteToModel(rows, names, { rowIndex: 0, column: "name", explicit: true }, createRow);
  assert.equal(rows.length, 16);
  assert.equal(rows.filter(function (r) { return trimCell(r.name_raw); }).length, 16);
  assert.equal(rows.filter(function (r) { return trimCell(r.amount_raw); }).length, 0);

  applySingleColumnPasteToModel(rows, amounts, { rowIndex: 0, column: "amount", explicit: true }, createRow);
  assert.equal(rows.length, 16);
  assert.equal(rows.filter(function (r) { return trimCell(r.name_raw); }).length, 16);
  assert.equal(rows.filter(function (r) { return trimCell(r.amount_raw); }).length, 16);
  for (let i = 0; i < 16; i += 1) {
    assert.equal(rows[i].name_raw, "Nombre " + pad2(i + 1));
    assert.equal(rows[i].amount_raw, String(1001 + i));
  }
});

test("5 names then 5 amounts fill existing rows", () => {
  const rows = [];
  const createRow = createRowFactory();
  applySingleColumnPasteToModel(rows, buildNames(5), { rowIndex: 0, column: "name", explicit: true }, createRow);
  applySingleColumnPasteToModel(rows, buildAmounts(500, 5), { rowIndex: 0, column: "amount", explicit: false }, createRow);
  assert.equal(rows.length, 5);
  for (let i = 0; i < 5; i += 1) {
    assert.equal(rows[i].name_raw, "Nombre " + pad2(i + 1));
    assert.equal(rows[i].amount_raw, String(500 + i));
  }
});

test("more amounts than rows completes existing then creates only surplus", () => {
  const rows = [makeRow({ name_raw: "A", amount_raw: "" }), makeRow({ name_raw: "B", amount_raw: "" })];
  applySingleColumnPasteToModel(rows, ["10", "20", "30"], { rowIndex: 0, column: "amount", explicit: false }, createRowFactory());
  assert.equal(rows.length, 3);
  assert.equal(rows[0].amount_raw, "10");
  assert.equal(rows[1].amount_raw, "20");
  assert.equal(rows[2].amount_raw, "30");
});

test("fewer amounts than names leaves trailing names without amount", () => {
  const rows = [];
  const createRow = createRowFactory();
  applySingleColumnPasteToModel(rows, buildNames(4), { rowIndex: 0, column: "name", explicit: true }, createRow);
  applySingleColumnPasteToModel(rows, ["10", "20"], { rowIndex: 0, column: "amount", explicit: false }, createRow);
  assert.equal(rows.length, 4);
  assert.equal(rows[0].amount_raw, "10");
  assert.equal(rows[1].amount_raw, "20");
  assert.equal(rows[2].amount_raw, "");
  assert.equal(rows[3].amount_raw, "");
});

test("amount paste from row 5 starts at row 5 when anchored", () => {
  const rows = [];
  const createRow = createRowFactory();
  applySingleColumnPasteToModel(rows, buildNames(10), { rowIndex: 0, column: "name", explicit: true }, createRow);
  applySingleColumnPasteToModel(rows, ["9001", "9002"], { rowIndex: 5, column: "amount", explicit: true }, createRow);
  assert.equal(rows[5].amount_raw, "9001");
  assert.equal(rows[6].amount_raw, "9002");
  assert.equal(rows[0].amount_raw, "");
});

test("name paste targets name column on anchored paste", () => {
  const rows = [makeRow({ name_raw: "", amount_raw: "" })];
  applySingleColumnPasteToModel(rows, ["Alice", "Bob"], { rowIndex: 0, column: "name", explicit: true }, createRowFactory());
  assert.equal(rows[0].name_raw, "Alice");
  assert.equal(rows[1].name_raw, "Bob");
  assert.equal(rows[0].amount_raw, "");
});

test("inferred amount paste skips rows that already have amount", () => {
  const rows = [
    makeRow({ name_raw: "A", amount_raw: "999" }),
    makeRow({ name_raw: "B", amount_raw: "" }),
  ];
  applySingleColumnPasteToModel(rows, ["100", "200"], { rowIndex: 0, column: "amount", explicit: false }, createRowFactory());
  assert.equal(rows[0].amount_raw, "999");
  assert.equal(rows[1].amount_raw, "100");
  assert.equal(rows.length, 3);
  assert.equal(rows[2].amount_raw, "200");
});

test("explicit anchored amount paste replaces existing amount", () => {
  const rows = [makeRow({ name_raw: "A", amount_raw: "999" })];
  applySingleColumnPasteToModel(rows, ["1234"], { rowIndex: 0, column: "amount", explicit: true }, createRowFactory());
  assert.equal(rows[0].amount_raw, "1234");
});

test("single column paste detection", () => {
  const matrix = extractPasteMatrix("A\nB\nC");
  assert.equal(isSingleColumnPaste(matrix), true);
  const matrixTsv = extractPasteMatrix("A\t1\nB\t2");
  assert.equal(isSingleColumnPaste(matrixTsv), false);
});

test("inferred amount paste after names from container anchor", () => {
  const rows = [];
  const createRow = createRowFactory();
  applySingleColumnPasteToModel(rows, buildNames(16), { rowIndex: 0, column: "name", explicit: true }, createRow);
  applySingleColumnPasteToModel(rows, buildAmounts(1001, 16), { rowIndex: 0, column: "name", explicit: false }, createRow);
  assert.equal(rows.length, 16);
  for (let i = 0; i < 16; i += 1) {
    assert.equal(rows[i].amount_raw, String(1001 + i));
  }
});

test("one DOM paste event invokes applyPaste once", () => {
  const pasteRoot = createMockPasteRoot();
  let calls = 0;
  installGridPasteListener(pasteRoot, function () { calls += 1; });
  pasteRoot.dispatchPaste("Nombre 01\nNombre 02");
  assert.equal(calls, 1);
  assert.equal(pasteRoot.pasteListenerCount(), 1);
});

test("nested paste target still executes pipeline once", () => {
  const pasteRoot = createMockPasteRoot();
  let calls = 0;
  installGridPasteListener(pasteRoot, function () { calls += 1; });
  const nestedInput = {
    matches: function () { return true; },
    classList: { contains: function () { return false; } },
    closest: function () { return null; },
  };
  pasteRoot.dispatchPaste("1001\n1002", nestedInput);
  assert.equal(calls, 1);
});

test("repeated paste listener install is idempotent", () => {
  const pasteRoot = createMockPasteRoot();
  let calls = 0;
  const handler = function () { calls += 1; };
  const first = installGridPasteListener(pasteRoot, handler);
  const second = installGridPasteListener(pasteRoot, handler);
  assert.equal(first, second);
  assert.equal(pasteRoot.pasteListenerCount(), 1);
  pasteRoot.dispatchPaste("Nombre 01");
  assert.equal(calls, 1);
});

test("default blank row plus 9 names yields exactly 9 rows", () => {
  const rows = rowsWithDefaultBlank();
  applySingleColumnPasteToModel(
    rows,
    buildNames(9),
    { rowIndex: 0, column: "name", explicit: true },
    createRowFactory()
  );
  assert.equal(rows.length, 9);
  assert.equal(countPopulated(rows, "name_raw"), 9);
  assert.equal(countPopulated(rows, "amount_raw"), 0);
});

test("contract: 9 names then 9 amounts remain 9 rows aligned", () => {
  const rows = rowsWithDefaultBlank();
  pasteNamesThenAmounts(rows, 9, 1001, false);
  assert.equal(rows.length, 9);
  assert.equal(countPopulated(rows, "name_raw"), 9);
  assert.equal(countPopulated(rows, "amount_raw"), 9);
  for (let i = 0; i < 9; i += 1) {
    assert.equal(rows[i].name_raw, "Nombre " + pad2(i + 1));
    assert.equal(rows[i].amount_raw, String(1001 + i));
  }
});

test("contract: default blank plus 16 names then 16 amounts remain 16 rows", () => {
  const rows = rowsWithDefaultBlank();
  pasteNamesThenAmounts(rows, 16, 1001, true);
  assert.equal(rows.length, 16);
  assert.equal(countPopulated(rows, "name_raw"), 16);
  assert.equal(countPopulated(rows, "amount_raw"), 16);
});

test("existing 9 name rows plus amount paste creates no extra rows", () => {
  const rows = rowsWithDefaultBlank();
  applySingleColumnPasteToModel(
    rows,
    buildNames(9),
    { rowIndex: 0, column: "name", explicit: true },
    createRowFactory()
  );
  assert.equal(rows.length, 9);
  applySingleColumnPasteToModel(
    rows,
    buildAmounts(1001, 9),
    { rowIndex: 0, column: "amount", explicit: false },
    createRowFactory()
  );
  assert.equal(rows.length, 9);
  assert.equal(countPopulated(rows, "name_raw"), 9);
  assert.equal(countPopulated(rows, "amount_raw"), 9);
});

test("triple inferred name paste would duplicate rows without single-event guard", () => {
  const rows = rowsWithDefaultBlank();
  const createRow = createRowFactory();
  const names = buildNames(9);
  for (let i = 0; i < 3; i += 1) {
    applySingleColumnPasteToModel(
      rows,
      names,
      { rowIndex: 0, column: "name", explicit: false },
      createRow
    );
  }
  assert.equal(rows.length, 27);
});

test("no amount-only duplicate rows after names then amounts", () => {
  const rows = rowsWithDefaultBlank();
  pasteNamesThenAmounts(rows, 9, 1001, false);
  const amountOnly = rows.filter(function (row) {
    return trimCell(row.amount_raw) && !trimCell(row.name_raw);
  });
  assert.equal(amountOnly.length, 0);
});

test("no name-only duplicate rows after names then amounts", () => {
  const rows = rowsWithDefaultBlank();
  pasteNamesThenAmounts(rows, 9, 1001, false);
  const nameOnly = rows.filter(function (row) {
    return trimCell(row.name_raw) && !trimCell(row.amount_raw);
  });
  assert.equal(nameOnly.length, 0);
});
