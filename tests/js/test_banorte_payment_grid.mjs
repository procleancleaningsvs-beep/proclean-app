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
} = grid;

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
