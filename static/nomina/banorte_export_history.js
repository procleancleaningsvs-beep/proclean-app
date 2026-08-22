(function (root, factory) {
  "use strict";
  const api = factory(root);
  if (typeof module === "object" && module.exports) module.exports = api;
  root.BanorteExportHistory = api;
})(typeof window !== "undefined" ? window : globalThis, function (root) {
  "use strict";

  function formatAmountCents(amountCents) {
    const cents = Number(amountCents);
    if (!Number.isSafeInteger(cents)) return "—";
    return new Intl.NumberFormat("es-MX", {
      style: "currency",
      currency: "MXN",
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    }).format(cents / 100);
  }

  function formatLayoutDate(value) {
    const raw = String(value || "");
    if (/^\d{8}$/.test(raw)) {
      return raw.slice(6, 8) + "/" + raw.slice(4, 6) + "/" + raw.slice(0, 4);
    }
    return raw || "—";
  }

  function movementsUrl(exportId) {
    return "/nomina/exportaciones/banorte/historial/" +
      encodeURIComponent(String(exportId)) + "/movimientos";
  }

  function movementsExcelUrl(exportId) {
    return "/nomina/exportaciones/banorte/historial/" +
      encodeURIComponent(String(exportId)) + "/movimientos.xlsx";
  }

  function normalizeSearchValue(value) {
    return String(value == null ? "" : value)
      .normalize("NFD")
      .replace(/[\u0300-\u036f]/g, "")
      .toLocaleLowerCase("es-MX");
  }

  function parseAmountQueryCents(value) {
    const compact = String(value == null ? "" : value)
      .trim()
      .replace(/\s/g, "")
      .replace(/^\$/, "");
    if (!compact) return null;
    if (!/^(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d{1,2})?$/.test(compact)) return null;
    const parts = compact.replace(/,/g, "").split(".");
    const whole = Number(parts[0]);
    const fractional = (parts[1] || "").padEnd(2, "0");
    if (!Number.isSafeInteger(whole)) return null;
    const cents = whole * 100 + Number(fractional || "0");
    return Number.isSafeInteger(cents) ? cents : null;
  }

  function compareEmployeeNumbers(left, right) {
    const a = String(left == null ? "" : left);
    const b = String(right == null ? "" : right);
    const aNumeric = /^\d+$/.test(a);
    const bNumeric = /^\d+$/.test(b);
    if (aNumeric && bNumeric) {
      const aKey = a.replace(/^0+(?=\d)/, "");
      const bKey = b.replace(/^0+(?=\d)/, "");
      if (aKey.length !== bKey.length) return aKey.length < bKey.length ? -1 : 1;
      if (aKey !== bKey) return aKey < bKey ? -1 : 1;
      return a.localeCompare(b, "es", { sensitivity: "variant" });
    }
    if (aNumeric !== bNumeric) return aNumeric ? -1 : 1;
    return a.localeCompare(b, "es", { numeric: true, sensitivity: "base" });
  }

  function filterAndSortMovements(items, query, sort) {
    const rawQuery = String(query == null ? "" : query).trim();
    const normalizedQuery = normalizeSearchValue(rawQuery);
    const amountQueryCents = parseAmountQueryCents(rawQuery);
    const rows = (Array.isArray(items) ? items : []).filter(function (item) {
      if (!rawQuery) return true;
      const nameMatches = normalizeSearchValue(item.historical_name).includes(normalizedQuery);
      const employeeMatches = String(item.employee_number == null ? "" : item.employee_number)
        .includes(rawQuery);
      const amountMatches = amountQueryCents !== null && item.amount_cents === amountQueryCents;
      return nameMatches || employeeMatches || amountMatches;
    });
    const direction = /_desc$/.test(sort) ? -1 : 1;
    rows.sort(function (a, b) {
      let comparison = 0;
      if (sort === "name_asc" || sort === "name_desc") {
        comparison = String(a.historical_name || "").localeCompare(
          String(b.historical_name || ""),
          "es",
          { sensitivity: "base" },
        );
      } else if (sort === "employee_asc" || sort === "employee_desc") {
        comparison = compareEmployeeNumbers(a.employee_number, b.employee_number);
      } else if (sort === "amount_asc" || sort === "amount_desc") {
        comparison = a.amount_cents < b.amount_cents ? -1 : (a.amount_cents > b.amount_cents ? 1 : 0);
      }
      if (comparison) return comparison * direction;
      return Number(a.position) - Number(b.position);
    });
    return rows;
  }

  function createDomView(document) {
    const modal = document.getElementById("banorte-movements-modal");
    const closeButton = document.getElementById("banorte-movements-close");
    const exportButton = document.getElementById("banorte-movements-export");
    const title = document.getElementById("banorte-movements-title");
    const filename = document.getElementById("banorte-movements-filename");
    const date = document.getElementById("banorte-movements-date");
    const count = document.getElementById("banorte-movements-count");
    const total = document.getElementById("banorte-movements-total");
    const state = document.getElementById("banorte-movements-state");
    const controls = document.getElementById("banorte-movements-controls");
    const search = document.getElementById("banorte-movements-search");
    const sort = document.getElementById("banorte-movements-sort");
    const tableWrap = document.getElementById("banorte-movements-table-wrap");
    const tbody = document.getElementById("banorte-movements-body");
    let lastTrigger = null;
    let currentItems = [];
    let currentExportId = null;

    function setHeader(header) {
      title.textContent = "Movimientos de " + String(header.filename || "exportación Banorte");
      filename.textContent = String(header.filename || "—");
      date.textContent = formatLayoutDate(header.layout_date);
      count.textContent = String(Number(header.payment_count) || 0);
      total.textContent = formatAmountCents(header.total_cents);
    }

    function showState(message) {
      state.textContent = message;
      state.hidden = false;
      tableWrap.hidden = true;
    }

    function appendCell(row, value, className) {
      const cell = document.createElement("td");
      cell.textContent = String(value == null ? "—" : value);
      if (className) cell.className = className;
      row.appendChild(cell);
    }

    function renderCurrentItems() {
      const rows = filterAndSortMovements(
        currentItems,
        search ? search.value : "",
        sort ? sort.value : "position",
      );
      const fragment = document.createDocumentFragment();
      rows.forEach(function (item) {
        const row = document.createElement("tr");
        appendCell(row, item.position, "banorte-mono");
        appendCell(row, item.historical_name);
        appendCell(row, item.employee_number, "banorte-mono");
        appendCell(row, item.account_number, "banorte-mono");
        appendCell(row, formatAmountCents(item.amount_cents), "banorte-movements-amount");
        fragment.appendChild(row);
      });
      tbody.replaceChildren(fragment);
      if (rows.length === 0) {
        showState("No hay movimientos que coincidan con la búsqueda.");
      } else {
        state.hidden = true;
        tableWrap.hidden = false;
      }
    }

    function close() {
      if (!modal || modal.hidden) return;
      modal.hidden = true;
      if (lastTrigger && typeof lastTrigger.focus === "function") lastTrigger.focus();
      lastTrigger = null;
    }

    if (closeButton) closeButton.addEventListener("click", close);
    if (exportButton) {
      exportButton.addEventListener("click", function () {
        if (!/^\d+$/.test(String(currentExportId || ""))) return;
        root.location.assign(movementsExcelUrl(currentExportId));
      });
    }
    if (modal) {
      modal.addEventListener("click", function (event) {
        if (event.target === modal) close();
      });
    }
    document.addEventListener("keydown", function (event) {
      if (event.key === "Escape" && modal && !modal.hidden) close();
    });
    if (search) search.addEventListener("input", renderCurrentItems);
    if (sort) sort.addEventListener("change", renderCurrentItems);

    return {
      open(trigger) {
        lastTrigger = trigger;
        modal.hidden = false;
        if (closeButton) closeButton.focus();
      },
      loading(exportId) {
        currentExportId = exportId;
        currentItems = [];
        if (exportButton) exportButton.hidden = true;
        if (search) search.value = "";
        if (sort) sort.value = "position";
        if (controls) controls.hidden = true;
        tbody.replaceChildren();
        showState("Cargando movimientos históricos…");
      },
      success(header, items) {
        setHeader(header);
        currentItems = items.slice();
        if (exportButton) exportButton.hidden = items.length === 0;
        if (controls) controls.hidden = false;
        renderCurrentItems();
      },
      empty(header) {
        setHeader(header);
        currentItems = [];
        if (controls) controls.hidden = true;
        tbody.replaceChildren();
        showState("Este export no tiene movimientos históricos persistidos.");
      },
      error(message) {
        currentItems = [];
        if (controls) controls.hidden = true;
        tbody.replaceChildren();
        showState(message || "No fue posible consultar los movimientos históricos.");
      },
      close: close,
    };
  }

  function createHistoryController(environment) {
    const env = environment;
    const view = env.view;

    async function open(trigger) {
      view.open(trigger);
      view.loading(exportId);
      const exportId = trigger && trigger.dataset && trigger.dataset.exportId;
      if (!/^\d+$/.test(String(exportId || ""))) {
        view.error("El identificador del export no es válido.");
        return;
      }
      try {
        const response = await env.fetch(movementsUrl(exportId), {
          credentials: "same-origin",
          cache: "no-store",
          headers: { Accept: "application/json" },
        });
        const payload = await response.json().catch(function () { return {}; });
        if (!response.ok || !payload.ok || !payload.export || !Array.isArray(payload.items)) {
          view.error("No fue posible consultar los movimientos históricos.");
          return;
        }
        if (payload.items.length === 0) {
          view.empty(payload.export);
          return;
        }
        view.success(payload.export, payload.items);
      } catch (_error) {
        view.error("No fue posible consultar los movimientos históricos.");
      }
    }

    function close() {
      view.close();
    }

    function bindTriggers(container) {
      const host = container || root.document;
      if (!host || typeof host.querySelectorAll !== "function") return;
      host.querySelectorAll("[data-banorte-export-movements]").forEach(function (trigger) {
        if (trigger.dataset.banorteMovementsBound === "1") return;
        trigger.dataset.banorteMovementsBound = "1";
        trigger.addEventListener("click", function () { open(trigger); });
      });
    }

    return { open: open, close: close, bindTriggers: bindTriggers };
  }

  let defaultController = null;
  function getDefaultController() {
    if (!defaultController) {
      defaultController = createHistoryController({
        fetch: root.fetch.bind(root),
        view: createDomView(root.document),
      });
    }
    return defaultController;
  }

  function bindTriggers(container) {
    return getDefaultController().bindTriggers(container);
  }

  if (root.document) {
    if (root.document.readyState === "loading") {
      root.document.addEventListener("DOMContentLoaded", function () {
        bindTriggers(root.document);
      });
    } else {
      bindTriggers(root.document);
    }
  }

  return {
    createHistoryController: createHistoryController,
    filterAndSortMovements: filterAndSortMovements,
    formatAmountCents: formatAmountCents,
    bindTriggers: bindTriggers,
  };
});
