(function () {
  const root = document.getElementById("banorte-root");
  const gridBody = document.getElementById("banorte-payment-grid-body");
  if (!root || !gridBody) return;

  const rows = [];
  let nextPosition = 1;

  function esc(s) {
    return String(s || "")
      .replace(/&/g, "&amp;")
      .replace(/"/g, "&quot;")
      .replace(/</g, "&lt;");
  }

  function observationText(codes) {
    const labels = {
      LENGTH_MISMATCH: "Listas con distinta longitud.",
      NAME_EMPTY: "Nombre vacío.",
      AMOUNT_EMPTY: "Importe vacío.",
      AMOUNT_INVALID: "Importe inválido.",
    };
    return (codes || []).map(function (c) { return labels[c] || c; }).join(" ");
  }

  function newKey() {
    return "row-" + Math.random().toString(36).slice(2) + Date.now().toString(36);
  }

  function evaluateRow(row) {
    const codes = [];
    let state = "OK";
    if (!String(row.name_raw || "").trim()) {
      codes.push("NAME_EMPTY");
      state = "NEEDS_REVIEW";
    }
    if (!String(row.amount_raw || "").trim()) {
      codes.push("AMOUNT_EMPTY");
      state = "NEEDS_REVIEW";
    } else if (!/^\s*-?\d+(?:[.,]\d{1,2})?\s*$/.test(String(row.amount_raw))) {
      codes.push("AMOUNT_INVALID");
      state = "NEEDS_REVIEW";
    }
    row.state = state;
    row.observation_codes = codes;
  }

  function addRow(partial) {
    partial = partial || {};
    const row = {
      client_row_key: partial.client_row_key || newKey(),
      position: partial.position || nextPosition++,
      name_raw: partial.name_raw || "",
      catalog_person_id: partial.catalog_person_id || null,
      amount_raw: partial.amount_raw || "",
      account_display: partial.account_display || "",
      state: partial.state || "OK",
      observation_codes: partial.observation_codes || [],
    };
    evaluateRow(row);
    rows.push(row);
    render();
    return row;
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
        '<td><span class="banorte-grid-account banorte-mono">' + esc(row.account_display || "—") + "</span></td>" +
        '<td><input type="text" class="banorte-grid-amount" value="' + esc(row.amount_raw) + '" inputmode="decimal" autocomplete="off"></td>' +
        '<td class="banorte-grid-state">' + esc(observationText(row.observation_codes) || (row.state === "OK" ? "Listo" : "Requiere corrección")) + "</td>" +
        '<td><button type="button" class="btn btn-secondary btn-sm banorte-grid-remove">Quitar</button></td>';
      const nameInput = tr.querySelector(".banorte-grid-name");
      const amountInput = tr.querySelector(".banorte-grid-amount");
      nameInput.addEventListener("input", function () {
        row.name_raw = nameInput.value;
        row.catalog_person_id = null;
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
      tr.querySelector(".banorte-grid-remove").addEventListener("click", function () {
        removeRow(row.client_row_key);
      });
      gridBody.appendChild(tr);
    });
  }

  function parsePaste(text) {
    const lines = String(text || "").replace(/\r\n/g, "\n").replace(/\r/g, "\n").split("\n");
    const tsvLike = lines.filter(function (l) { return String(l).trim(); }).every(function (l) {
      return l.indexOf("\t") >= 0 && l.split("\t").length === 2;
    });
    lines.forEach(function (line) {
      if (!String(line).trim()) {
        addRow({ name_raw: "", amount_raw: "", state: "NEEDS_REVIEW", observation_codes: ["NAME_EMPTY", "AMOUNT_EMPTY"] });
        return;
      }
      if (tsvLike) {
        const parts = line.split("\t");
        addRow({ name_raw: parts[0], amount_raw: parts[1] || "" });
      } else {
        addRow({ name_raw: line, amount_raw: "" });
      }
    });
  }

  const pasteArea = document.getElementById("banorte-payment-grid-paste");
  if (pasteArea) {
    pasteArea.addEventListener("paste", function (e) {
      e.preventDefault();
      const text = (e.clipboardData || window.clipboardData).getData("text");
      parsePaste(text);
    });
  }

  document.addEventListener("banorte:catalog-person-selected", function (e) {
    const detail = e.detail || {};
    addRow({
      name_raw: "",
      amount_raw: "",
      catalog_person_id: detail.catalog_person_id,
      account_display: "Catálogo",
    });
  });

  document.getElementById("banorte-payment-grid-add")?.addEventListener("click", function () {
    addRow({});
  });

  window.banortePaymentGrid = {
    getRowsPayload: function () {
      return rows.map(function (r) {
        return {
          client_row_key: r.client_row_key,
          position: r.position,
          name_raw: r.name_raw,
          amount_raw: r.amount_raw,
          catalog_person_id: r.catalog_person_id,
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

  addRow({});
})();
