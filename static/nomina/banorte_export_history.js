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

  function createDomView(document) {
    const modal = document.getElementById("banorte-movements-modal");
    const closeButton = document.getElementById("banorte-movements-close");
    const title = document.getElementById("banorte-movements-title");
    const filename = document.getElementById("banorte-movements-filename");
    const date = document.getElementById("banorte-movements-date");
    const count = document.getElementById("banorte-movements-count");
    const total = document.getElementById("banorte-movements-total");
    const state = document.getElementById("banorte-movements-state");
    const tableWrap = document.getElementById("banorte-movements-table-wrap");
    const tbody = document.getElementById("banorte-movements-body");
    let lastTrigger = null;

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

    function close() {
      if (!modal || modal.hidden) return;
      modal.hidden = true;
      if (lastTrigger && typeof lastTrigger.focus === "function") lastTrigger.focus();
      lastTrigger = null;
    }

    if (closeButton) closeButton.addEventListener("click", close);
    if (modal) {
      modal.addEventListener("click", function (event) {
        if (event.target === modal) close();
      });
    }
    document.addEventListener("keydown", function (event) {
      if (event.key === "Escape" && modal && !modal.hidden) close();
    });

    return {
      open(trigger) {
        lastTrigger = trigger;
        modal.hidden = false;
        if (closeButton) closeButton.focus();
      },
      loading() {
        tbody.replaceChildren();
        showState("Cargando movimientos históricos…");
      },
      success(header, items) {
        setHeader(header);
        const fragment = document.createDocumentFragment();
        items.forEach(function (item) {
          const row = document.createElement("tr");
          appendCell(row, item.position, "banorte-mono");
          appendCell(row, item.historical_name);
          appendCell(row, item.employee_number, "banorte-mono");
          appendCell(row, item.account_number, "banorte-mono");
          appendCell(row, formatAmountCents(item.amount_cents), "banorte-movements-amount");
          fragment.appendChild(row);
        });
        tbody.replaceChildren(fragment);
        state.hidden = true;
        tableWrap.hidden = false;
      },
      empty(header) {
        setHeader(header);
        tbody.replaceChildren();
        showState("Este export no tiene movimientos históricos persistidos.");
      },
      error(message) {
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
      view.loading();
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
    formatAmountCents: formatAmountCents,
    bindTriggers: bindTriggers,
  };
});
