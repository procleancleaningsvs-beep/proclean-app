(function (root, factory) {
  "use strict";
  const api = factory(root);
  if (typeof module === "object" && module.exports) module.exports = api;
  root.BanortePaymentGrid = api;
})(typeof window !== "undefined" ? window : globalThis, function (root) {
  "use strict";

  const OBSERVATION_LABELS = {
    LENGTH_MISMATCH: "Listas con distinta longitud.",
    NAME_EMPTY: "Nombre vacío.",
    AMOUNT_EMPTY: "Importe vacío.",
    AMOUNT_INVALID: "Importe inválido.",
    PASTE_AMBIGUOUS: "Estructura de pegado ambigua.",
  };

  const EDITABLE_COLUMNS = ["name", "amount"];

  function trimCell(value) {
    return String(value == null ? "" : value).replace(/\u00a0/g, " ").trim();
  }

  function splitClipboardLines(text) {
    return String(text || "").replace(/\r\n/g, "\n").replace(/\r/g, "\n").split("\n");
  }

  function splitRowCells(line) {
    return String(line || "").split("\t").map(trimCell);
  }

  function rowIsFullyEmpty(cells) {
    return cells.every(function (cell) { return !cell; });
  }

  function normalizeDecimalString(cleaned) {
    if (cleaned.indexOf(",") >= 0 && cleaned.indexOf(".") >= 0) {
      const lastComma = cleaned.lastIndexOf(",");
      const lastDot = cleaned.lastIndexOf(".");
      if (lastComma > lastDot) {
        const intpart = cleaned.slice(0, lastComma).replace(/\./g, "");
        const frac = cleaned.slice(lastComma + 1);
        if (!/^\d+$/.test(frac) || !/^\d+$/.test(intpart)) return null;
        return intpart + "." + frac;
      }
      const intpart = cleaned.slice(0, lastDot).replace(/,/g, "");
      const frac = cleaned.slice(lastDot + 1);
      if (!/^\d+$/.test(frac) || !/^\d+$/.test(intpart)) return null;
      return intpart + "." + frac;
    }
    if (cleaned.indexOf(",") >= 0) {
      const parts = cleaned.split(",");
      if (parts.length === 2 && /^\d+$/.test(parts[1]) && parts[1].length <= 2) {
        const left = parts[0].replace(/\./g, "");
        if (!/^\d+$/.test(left)) return null;
        return left + "." + parts[1];
      }
      if (parts.length > 1 && parts.slice(1).every(function (p) { return /^\d{3}$/.test(p); }) && /^\d+$/.test(parts[0])) {
        return parts.join("");
      }
      if (parts.length === 2 && /^\d+$/.test(parts[0]) && /^\d{3}$/.test(parts[1])) {
        return parts[0] + parts[1];
      }
      return null;
    }
    if (cleaned.indexOf(".") >= 0) {
      const parts = cleaned.split(".");
      if (parts.length === 2 && /^\d+$/.test(parts[1]) && parts[1].length <= 2) {
        if (parts[0] !== "" && !/^\d+$/.test(parts[0])) return null;
        return parts[0] === "" ? "0." + parts[1] : cleaned;
      }
      if (parts.length > 2 && parts.slice(1).every(function (p) { return /^\d{3}$/.test(p); }) && /^\d+$/.test(parts[0])) {
        return parts.join("");
      }
      if (parts.length === 2 && /^\d+$/.test(parts[0]) && /^\d+$/.test(parts[1]) && parts[1].length > 2) {
        if (parts[0] === "" || /^\d+$/.test(parts[0])) return parts[0] === "" ? "0." + parts[1] : cleaned;
      }
      return null;
    }
    return /^\d+$/.test(cleaned) ? cleaned : null;
  }

  function parseMoney(raw) {
    if (raw == null) return { ok: false };
    const text = trimCell(raw);
    if (!text) return { ok: false };
    const lowered = text.toLowerCase();
    if (["=", "nan", "inf", "#ref", "#value"].some(function (tok) { return lowered.indexOf(tok) >= 0; })) {
      return { ok: false };
    }
    let cleaned = text;
    ["$", "€", "£", "¥", "MXN", "USD", "EUR"].forEach(function (sym) {
      cleaned = cleaned.split(sym).join("");
    });
    cleaned = cleaned.replace(/\u00a0/g, " ").trim().replace(/\s+/g, "");
    if (!cleaned || ["-", "+", ".", ",", "-.", ",."].indexOf(cleaned) >= 0) return { ok: false };

    let negative = false;
    if (cleaned.charAt(0) === "(" && cleaned.charAt(cleaned.length - 1) === ")") {
      negative = true;
      cleaned = cleaned.slice(1, -1);
    }
    if (cleaned.charAt(0) === "+") cleaned = cleaned.slice(1);
    if (cleaned.charAt(0) === "-") {
      negative = true;
      cleaned = cleaned.slice(1);
    }
    if (!cleaned) return { ok: false };
    if (!/^[0-9.,]+$/.test(cleaned)) return { ok: false };

    const normalized = normalizeDecimalString(cleaned);
    if (normalized == null) return { ok: false };
    if (negative) return { ok: false };
    if (/^0+(?:\.0+)?$/.test(normalized)) return { ok: false };
    return { ok: true };
  }

  function isMonetaryAmount(raw) {
    return parseMoney(raw).ok;
  }

  function classifyCell(value) {
    const trimmed = trimCell(value);
    if (!trimmed) return "EMPTY";
    if (isMonetaryAmount(trimmed)) return "AMOUNT";
    return "NAME";
  }

  function rowFromSingleCell(value) {
    const kind = classifyCell(value);
    if (kind === "AMOUNT") return { name_raw: "", amount_raw: value };
    return { name_raw: value, amount_raw: "" };
  }

  function rowFromTwoCells(left, right) {
    const a = trimCell(left);
    const b = trimCell(right);
    const leftKind = classifyCell(a);
    const rightKind = classifyCell(b);
    if (leftKind === "NAME" && rightKind === "AMOUNT") {
      return { name_raw: a, amount_raw: b };
    }
    if (leftKind === "AMOUNT" && rightKind === "NAME") {
      return { name_raw: b, amount_raw: a };
    }
    if (leftKind === "EMPTY" && rightKind === "AMOUNT") {
      return { name_raw: "", amount_raw: b };
    }
    if (leftKind === "AMOUNT" && rightKind === "EMPTY") {
      return { name_raw: "", amount_raw: a };
    }
    if (leftKind === "NAME" && rightKind === "EMPTY") {
      return { name_raw: a, amount_raw: "" };
    }
    if (leftKind === "EMPTY" && rightKind === "NAME") {
      return { name_raw: b, amount_raw: "" };
    }
    if (leftKind === "NAME" && rightKind === "NAME") {
      return { name_raw: a, amount_raw: b, paste_ambiguous: true };
    }
    if (leftKind === "AMOUNT" && rightKind === "AMOUNT") {
      return { name_raw: a, amount_raw: b, paste_ambiguous: true };
    }
    return { name_raw: a, amount_raw: b, paste_ambiguous: true };
  }

  function rowFromMultiCells(cells) {
    const trimmed = cells.map(trimCell);
    if (trimmed.length <= 2) {
      return rowFromTwoCells(trimmed[0] || "", trimmed[1] || "");
    }
    return {
      name_raw: trimmed[0] || "",
      amount_raw: trimmed[1] || "",
      paste_ambiguous: true,
    };
  }

  function finalizeParsedRow(row) {
    const out = {
      name_raw: row.name_raw == null ? "" : String(row.name_raw),
      amount_raw: row.amount_raw == null ? "" : String(row.amount_raw),
      observation_codes: [],
    };
    if (row.paste_ambiguous) out.observation_codes.push("PASTE_AMBIGUOUS");
    return out;
  }

  function extractPasteMatrix(text) {
    const matrix = [];
    splitClipboardLines(text).forEach(function (line) {
      const cells = splitRowCells(line);
      if (rowIsFullyEmpty(cells)) return;
      matrix.push(cells);
    });
    return matrix;
  }

  function isSingleColumnPaste(matrix) {
    return matrix.length > 0 && matrix.every(function (cells) { return cells.length === 1; });
  }

  function singleColumnValues(matrix) {
    return matrix.map(function (cells) { return cells[0]; });
  }

  function inferTargetColumnFromValues(values) {
    if (!values.length) return null;
    let sawName = false;
    let sawAmount = false;
    values.forEach(function (value) {
      const kind = classifyCell(value);
      if (kind === "NAME") sawName = true;
      if (kind === "AMOUNT") sawAmount = true;
    });
    if (sawName && !sawAmount) return "name";
    if (sawAmount && !sawName) return "amount";
    return null;
  }

  function findFirstEmptyNameRow(rows, fromIndex) {
    for (let i = Math.max(0, fromIndex || 0); i < rows.length; i += 1) {
      if (!trimCell(rows[i].name_raw)) return i;
    }
    return -1;
  }

  function findInferredAmountTargetRow(rows, fromIndex) {
    for (let i = Math.max(0, fromIndex || 0); i < rows.length; i += 1) {
      if (trimCell(rows[i].name_raw) && !trimCell(rows[i].amount_raw)) return i;
    }
    for (let i = Math.max(0, fromIndex || 0); i < rows.length; i += 1) {
      if (!trimCell(rows[i].name_raw) && !trimCell(rows[i].amount_raw)) return i;
    }
    return -1;
  }

  function planSingleColumnPaste(rows, values, anchor) {
    anchor = anchor || {};
    const explicit = !!anchor.explicit;
    let targetColumn = anchor.column || "name";
    const startRow = anchor.rowIndex >= 0 ? anchor.rowIndex : 0;
    const ops = [];

    if (!explicit) {
      const inferred = inferTargetColumnFromValues(values);
      if (inferred) targetColumn = inferred;
      else {
        values.forEach(function (value) {
          const parsed = finalizeParsedRow(rowFromSingleCell(value));
          ops.push({ create: true, name_raw: parsed.name_raw, amount_raw: parsed.amount_raw, observation_codes: parsed.observation_codes });
        });
        return ops;
      }
    }

    if (targetColumn === "name") {
      if (explicit) {
        values.forEach(function (value, offset) {
          ops.push({
            rowIndex: startRow + offset,
            field: "name_raw",
            value: value,
            overwrite: true,
            ensureCapacity: true,
          });
        });
        return ops;
      }
      let cursor = findFirstEmptyNameRow(rows, 0);
      values.forEach(function (value) {
        if (cursor < 0) {
          ops.push({ create: true, name_raw: value, amount_raw: "", observation_codes: [] });
          return;
        }
        ops.push({ rowIndex: cursor, field: "name_raw", value: value, overwrite: false });
        cursor = findFirstEmptyNameRow(rows, cursor + 1);
      });
      return ops;
    }

    if (explicit) {
      values.forEach(function (value, offset) {
        ops.push({
          rowIndex: startRow + offset,
          field: "amount_raw",
          value: value,
          overwrite: true,
          ensureCapacity: true,
        });
      });
      return ops;
    }

    let searchFrom = 0;
    values.forEach(function (value) {
      let target = -1;
      for (let i = searchFrom; i < rows.length; i += 1) {
        if (trimCell(rows[i].name_raw) && !trimCell(rows[i].amount_raw)) {
          target = i;
          break;
        }
      }
      if (target < 0) {
        for (let i = searchFrom; i < rows.length; i += 1) {
          if (!trimCell(rows[i].name_raw) && !trimCell(rows[i].amount_raw)) {
            target = i;
            break;
          }
        }
      }
      if (target < 0) {
        ops.push({ create: true, name_raw: "", amount_raw: value, observation_codes: [] });
        return;
      }
      ops.push({ rowIndex: target, field: "amount_raw", value: value, overwrite: false });
      searchFrom = target + 1;
    });
    return ops;
  }

  function applyPastePlan(rows, ops, createRow) {
    ops.forEach(function (op) {
      if (op.create) {
        const row = createRow({
          name_raw: op.name_raw || "",
          amount_raw: op.amount_raw || "",
          observation_codes: op.observation_codes || [],
        });
        rows.push(row);
        return;
      }
      if (op.ensureCapacity) {
        while (rows.length <= op.rowIndex) rows.push(createRow({}));
      }
      const row = rows[op.rowIndex];
      if (!row) return;
      if (op.field === "name_raw") {
        if (op.overwrite || !trimCell(row.name_raw)) row.name_raw = op.value;
      } else if (op.field === "amount_raw") {
        if (op.overwrite || !trimCell(row.amount_raw)) row.amount_raw = op.value;
      }
      row.catalog_person_id = null;
      row.beneficiary_id = null;
      row.account_display = "";
      if (Array.isArray(op.observation_codes) && op.observation_codes.length) {
        row.observation_codes = op.observation_codes.slice();
      }
      evaluateRow(row);
    });
  }

  function applySingleColumnPasteToModel(rows, values, anchor, createRow) {
    const ops = planSingleColumnPaste(rows, values, anchor);
    applyPastePlan(rows, ops, createRow);
    return ops;
  }

  function parsePasteMatrix(text) {
    return extractPasteMatrix(text).map(function (cells) {
      let row;
      if (cells.length === 1) row = rowFromSingleCell(cells[0]);
      else if (cells.length === 2) row = rowFromTwoCells(cells[0], cells[1]);
      else row = rowFromMultiCells(cells);
      return finalizeParsedRow(row);
    });
  }

  function evaluateRow(row) {
    const codes = Array.isArray(row.observation_codes)
      ? row.observation_codes.slice()
      : [];
    let state = "OK";
    if (!trimCell(row.name_raw)) {
      if (codes.indexOf("NAME_EMPTY") < 0) codes.push("NAME_EMPTY");
      state = "NEEDS_REVIEW";
    }
    if (!trimCell(row.amount_raw)) {
      if (codes.indexOf("AMOUNT_EMPTY") < 0) codes.push("AMOUNT_EMPTY");
      state = "NEEDS_REVIEW";
    } else if (!parseMoney(row.amount_raw).ok) {
      if (codes.indexOf("AMOUNT_INVALID") < 0) codes.push("AMOUNT_INVALID");
      state = "NEEDS_REVIEW";
    }
    if (codes.indexOf("PASTE_AMBIGUOUS") >= 0) state = "NEEDS_REVIEW";
    row.state = state;
    row.observation_codes = codes;
  }

  function observationText(codes) {
    return (codes || []).map(function (c) { return OBSERVATION_LABELS[c] || c; }).join(" ");
  }

  function findFirstEmptyRowIndex(rows) {
    for (let i = 0; i < rows.length; i += 1) {
      if (!trimCell(rows[i].name_raw) && !trimCell(rows[i].amount_raw)) return i;
    }
    return rows.length;
  }

  function resolveKeyboardMove(key, column, rowIndex, rowCount, caretStart, caretEnd, textLength) {
    const atStart = caretStart === 0 && caretEnd === 0;
    const atEnd = caretStart === textLength && caretEnd === textLength;
    if (key === "ArrowUp") return { rowIndex: rowIndex - 1, column: column };
    if (key === "ArrowDown" || key === "Enter") return { rowIndex: rowIndex + 1, column: column };
    if (key === "ShiftEnter") return { rowIndex: rowIndex - 1, column: column };
    if (key === "Tab") {
      const colIdx = EDITABLE_COLUMNS.indexOf(column);
      const nextIdx = colIdx + 1;
      if (nextIdx < EDITABLE_COLUMNS.length) return { rowIndex: rowIndex, column: EDITABLE_COLUMNS[nextIdx] };
      if (rowIndex + 1 < rowCount) return { rowIndex: rowIndex + 1, column: EDITABLE_COLUMNS[0] };
      return null;
    }
    if (key === "ShiftTab") {
      const colIdx = EDITABLE_COLUMNS.indexOf(column);
      const nextIdx = colIdx - 1;
      if (nextIdx >= 0) return { rowIndex: rowIndex, column: EDITABLE_COLUMNS[nextIdx] };
      if (rowIndex > 0) return { rowIndex: rowIndex - 1, column: EDITABLE_COLUMNS[EDITABLE_COLUMNS.length - 1] };
      return null;
    }
    if (key === "ArrowLeft" && atStart) {
      const colIdx = EDITABLE_COLUMNS.indexOf(column);
      if (colIdx > 0) return { rowIndex: rowIndex, column: EDITABLE_COLUMNS[colIdx - 1] };
      if (rowIndex > 0) return { rowIndex: rowIndex - 1, column: EDITABLE_COLUMNS[EDITABLE_COLUMNS.length - 1] };
      return null;
    }
    if (key === "ArrowRight" && atEnd) {
      const colIdx = EDITABLE_COLUMNS.indexOf(column);
      if (colIdx + 1 < EDITABLE_COLUMNS.length) return { rowIndex: rowIndex, column: EDITABLE_COLUMNS[colIdx + 1] };
      if (rowIndex + 1 < rowCount) return { rowIndex: rowIndex + 1, column: EDITABLE_COLUMNS[0] };
      return null;
    }
    return null;
  }

  function installGridPasteListener(pasteRoot, handler) {
    if (!pasteRoot || typeof handler !== "function") {
      return { installed: false, dispatch: function () {}, teardown: function () {} };
    }
    if (pasteRoot.__banorteGridPasteBinding) {
      return pasteRoot.__banorteGridPasteBinding;
    }
    function onPaste(e) {
      handler(e);
    }
    pasteRoot.addEventListener("paste", onPaste, true);
    const binding = {
      installed: true,
      dispatch: onPaste,
      teardown: function () {
        pasteRoot.removeEventListener("paste", onPaste, true);
        delete pasteRoot.__banorteGridPasteBinding;
      },
    };
    pasteRoot.__banorteGridPasteBinding = binding;
    return binding;
  }

  function createGridController(options) {
    options = options || {};
    const gridBody = options.gridBody;
    const pasteRoot = options.pasteRoot;
    if (!gridBody) return null;

    const rows = [];
    let nextPosition = 1;

    function esc(s) {
      return String(s || "")
        .replace(/&/g, "&amp;")
        .replace(/"/g, "&quot;")
        .replace(/</g, "&lt;");
    }

    function newKey() {
      return "row-" + Math.random().toString(36).slice(2) + Date.now().toString(36);
    }

    function createRowData(partial) {
      partial = partial || {};
      const row = {
        client_row_key: partial.client_row_key || newKey(),
        position: partial.position || nextPosition++,
        name_raw: partial.name_raw || "",
        catalog_person_id: partial.catalog_person_id || null,
        beneficiary_id: partial.beneficiary_id || null,
        amount_raw: partial.amount_raw || "",
        account_display: partial.account_display || "",
        state: partial.state || "OK",
        observation_codes: partial.observation_codes || [],
      };
      evaluateRow(row);
      return row;
    }

    function ensureRowCapacity(count) {
      while (rows.length < count) rows.push(createRowData({}));
    }

    function mergeParsedRow(existing, parsed, overwrite) {
      if (overwrite || parsed.name_raw !== "" || !trimCell(existing.name_raw)) {
        existing.name_raw = parsed.name_raw;
      }
      if (overwrite || parsed.amount_raw !== "" || !trimCell(existing.amount_raw)) {
        existing.amount_raw = parsed.amount_raw;
      }
      existing.catalog_person_id = null;
      existing.beneficiary_id = null;
      existing.account_display = "";
      existing.observation_codes = parsed.observation_codes.slice();
      evaluateRow(existing);
    }

    function applyPaste(text, anchor) {
      anchor = anchor || {};
      const matrix = extractPasteMatrix(text);
      if (!matrix.length) return;

      if (isSingleColumnPaste(matrix)) {
        const values = singleColumnValues(matrix);
        applySingleColumnPasteToModel(rows, values, anchor, createRowData);
        rows.forEach(function (row, index) { row.position = index + 1; });
        nextPosition = rows.length + 1;
        render();
        const focusRow = Math.min(
          (anchor.rowIndex >= 0 ? anchor.rowIndex : 0) + values.length - 1,
          rows.length - 1
        );
        focusEditableCell(focusRow, anchor.column || inferTargetColumnFromValues(values) || "name");
        return;
      }

      const parsed = matrix.map(function (cells) {
        let row;
        if (cells.length === 1) row = rowFromSingleCell(cells[0]);
        else if (cells.length === 2) row = rowFromTwoCells(cells[0], cells[1]);
        else row = rowFromMultiCells(cells);
        return finalizeParsedRow(row);
      });
      let startRow = anchor.rowIndex >= 0 ? anchor.rowIndex : findFirstEmptyRowIndex(rows);
      ensureRowCapacity(startRow + parsed.length);
      parsed.forEach(function (parsedRow, offset) {
        mergeParsedRow(rows[startRow + offset], parsedRow, !!anchor.explicit);
      });
      rows.forEach(function (row, index) { row.position = index + 1; });
      nextPosition = rows.length + 1;
      render();
      const focusRow = Math.min(startRow + parsed.length - 1, rows.length - 1);
      focusEditableCell(focusRow, anchor.column || "name");
    }

    function resolvePasteAnchor(target) {
      if (target && target.matches && target.matches(".banorte-grid-name, .banorte-grid-amount")) {
        const tr = target.closest("tr");
        const rowIndex = tr ? Array.from(gridBody.children).indexOf(tr) : findFirstEmptyRowIndex(rows);
        return {
          rowIndex: rowIndex < 0 ? findFirstEmptyRowIndex(rows) : rowIndex,
          column: target.classList.contains("banorte-grid-amount") ? "amount" : "name",
          explicit: true,
        };
      }
      const active = gridBody.querySelector(".banorte-grid-name:focus, .banorte-grid-amount:focus");
      if (active) return resolvePasteAnchor(active);
      return { rowIndex: findFirstEmptyRowIndex(rows), column: "name", explicit: false };
    }

    function focusEditableCell(rowIndex, column) {
      const tr = gridBody.children[rowIndex];
      if (!tr) return;
      const selector = column === "amount" ? ".banorte-grid-amount" : ".banorte-grid-name";
      const input = tr.querySelector(selector);
      if (input) input.focus();
    }

    function handlePasteEvent(e) {
      const text = (e.clipboardData || root.clipboardData || { getData: function () { return ""; } }).getData("text");
      if (!String(text).length) return;
      e.preventDefault();
      applyPaste(text, resolvePasteAnchor(e.target));
    }

    const pasteBinding = installGridPasteListener(pasteRoot, handlePasteEvent);

    function handleInputKeydown(e) {
      const input = e.target;
      if (!input.matches(".banorte-grid-name, .banorte-grid-amount")) return;
      if (e.ctrlKey || e.metaKey || e.altKey) return;
      const column = input.classList.contains("banorte-grid-amount") ? "amount" : "name";
      const tr = input.closest("tr");
      const rowIndex = tr ? Array.from(gridBody.children).indexOf(tr) : -1;
      if (rowIndex < 0) return;
      let key = e.key;
      if (key === "Enter" && e.shiftKey) key = "ShiftEnter";
      if (key === "Tab" && e.shiftKey) key = "ShiftTab";
      const move = resolveKeyboardMove(
        key,
        column,
        rowIndex,
        rows.length,
        input.selectionStart == null ? 0 : input.selectionStart,
        input.selectionEnd == null ? 0 : input.selectionEnd,
        String(input.value || "").length
      );
      if (!move) return;
      if (move.rowIndex < 0 || move.rowIndex >= rows.length) return;
      e.preventDefault();
      focusEditableCell(move.rowIndex, move.column);
    }

    function addRow(partial) {
      rows.push(createRowData(partial));
      render();
      return rows[rows.length - 1];
    }

    function addRowsBatch(partials) {
      partials.forEach(function (partial) { rows.push(createRowData(partial)); });
      render();
    }

    function removeRow(key) {
      const idx = rows.findIndex(function (r) { return r.client_row_key === key; });
      if (idx >= 0) rows.splice(idx, 1);
      rows.forEach(function (r, i) { r.position = i + 1; });
      nextPosition = rows.length + 1;
      render();
    }

    function render() {
      gridBody.innerHTML = "";
      rows.forEach(function (row) {
        const tr = document.createElement("tr");
        tr.dataset.rowKey = row.client_row_key;
        tr.innerHTML =
          "<td>" + row.position + "</td>" +
          '<td><input type="text" class="banorte-grid-name" value="' + esc(row.name_raw) + '" autocomplete="off"></td>' +
          '<td><span class="banorte-grid-account banorte-mono" tabindex="-1">' + esc(row.account_display || "—") + "</span></td>" +
          '<td><input type="text" class="banorte-grid-amount" value="' + esc(row.amount_raw) + '" inputmode="decimal" autocomplete="off"></td>' +
          '<td class="banorte-grid-state">' + esc(observationText(row.observation_codes) || (row.state === "OK" ? "Listo" : "Requiere corrección")) + "</td>" +
          '<td><button type="button" class="btn btn-secondary btn-sm banorte-grid-remove" tabindex="0">Quitar</button></td>';
        const nameInput = tr.querySelector(".banorte-grid-name");
        const amountInput = tr.querySelector(".banorte-grid-amount");
        nameInput.addEventListener("input", function () {
          row.name_raw = nameInput.value;
          row.catalog_person_id = null;
          row.beneficiary_id = null;
          row.account_display = "";
          evaluateRow(row);
          tr.querySelector(".banorte-grid-state").textContent =
            observationText(row.observation_codes) || (row.state === "OK" ? "Listo" : "Requiere corrección");
        });
        amountInput.addEventListener("input", function () {
          row.amount_raw = amountInput.value;
          evaluateRow(row);
          tr.querySelector(".banorte-grid-state").textContent =
            observationText(row.observation_codes) || (row.state === "OK" ? "Listo" : "Requiere corrección");
        });
        nameInput.addEventListener("keydown", handleInputKeydown);
        amountInput.addEventListener("keydown", handleInputKeydown);
        tr.querySelector(".banorte-grid-remove").addEventListener("click", function () {
          removeRow(row.client_row_key);
        });
        gridBody.appendChild(tr);
      });
    }

    return {
      rows: rows,
      addRow: addRow,
      addRowsBatch: addRowsBatch,
      removeRow: removeRow,
      applyPaste: applyPaste,
      render: render,
      focusEditableCell: focusEditableCell,
      handlePasteEvent: handlePasteEvent,
      pasteBinding: pasteBinding,
      getRowsPayload: function () {
        return rows.map(function (r) {
          return {
            client_row_key: r.client_row_key,
            position: r.position,
            name_raw: r.name_raw,
            amount_raw: r.amount_raw,
            catalog_person_id: r.catalog_person_id,
            beneficiary_id: r.beneficiary_id,
            state: r.state,
            observation_codes: r.observation_codes,
          };
        });
      },
      clear: function () {
        rows.length = 0;
        nextPosition = 1;
        render();
      },
    };
  }

  function mount() {
    const pageRoot = root.document && root.document.getElementById("banorte-root");
    const gridBody = root.document && root.document.getElementById("banorte-payment-grid-body");
    const pasteRoot = root.document && root.document.getElementById("banorte-payment-grid-paste");
    if (!pageRoot || !gridBody || !pasteRoot) return null;
    if (pasteRoot.dataset.banortePaymentGridMounted === "1") {
      return root.__banortePaymentGridController || null;
    }
    pasteRoot.dataset.banortePaymentGridMounted = "1";
    const controller = createGridController({
      gridBody: gridBody,
      pasteRoot: pasteRoot,
    });
    if (!controller) return null;

    root.document.addEventListener("banorte:catalog-person-selected", function (e) {
      const detail = e.detail || {};
      const accountLabel = detail.account_masked
        ? detail.account_masked
        : (detail.catalog_person_id ? "Catálogo" : "—");
      controller.addRow({
        name_raw: detail.display_name || "",
        amount_raw: "",
        catalog_person_id: detail.catalog_person_id || null,
        beneficiary_id: detail.beneficiary_id || null,
        account_display: accountLabel,
      });
    });

    const addBtn = root.document.getElementById("banorte-payment-grid-add");
    if (addBtn) addBtn.addEventListener("click", function () { controller.addRow({}); });

    controller.addRow({});
    root.__banortePaymentGridController = controller;
    root.banortePaymentGrid = {
      getRowsPayload: controller.getRowsPayload.bind(controller),
      clear: controller.clear.bind(controller),
    };
    return controller;
  }

  if (root.document) {
    if (root.document.readyState === "loading") {
      root.document.addEventListener("DOMContentLoaded", mount);
    } else {
      mount();
    }
  }

  return {
    trimCell: trimCell,
    splitClipboardLines: splitClipboardLines,
    splitRowCells: splitRowCells,
    rowIsFullyEmpty: rowIsFullyEmpty,
    parseMoney: parseMoney,
    isMonetaryAmount: isMonetaryAmount,
    classifyCell: classifyCell,
    extractPasteMatrix: extractPasteMatrix,
    isSingleColumnPaste: isSingleColumnPaste,
    inferTargetColumnFromValues: inferTargetColumnFromValues,
    planSingleColumnPaste: planSingleColumnPaste,
    applySingleColumnPasteToModel: applySingleColumnPasteToModel,
    parsePasteMatrix: parsePasteMatrix,
    evaluateRow: evaluateRow,
    resolveKeyboardMove: resolveKeyboardMove,
    EDITABLE_COLUMNS: EDITABLE_COLUMNS,
    installGridPasteListener: installGridPasteListener,
    createGridController: createGridController,
    mount: mount,
  };
});
