(function () {
  "use strict";

  const table = document.getElementById("gis-ws-table");
  if (!table) return;

  const palette = JSON.parse(document.getElementById("gis-ws-palette")?.textContent || "{}");
  const search = document.getElementById("gis-ws-search");
  const showRemoved = document.getElementById("gis-ws-show-removed");
  const modal = document.getElementById("gis-ws-modal");

  function paintAttendance(root) {
    root.querySelectorAll(".gis-ws-att-cell").forEach(function (cell) {
      const key = String(cell.dataset.code || "").toUpperCase();
      const style = palette[key] || palette._neutral || { bg: "#f3f4f6", bold: false };
      cell.style.background = style.bg;
      cell.style.fontWeight = style.bold ? "700" : "600";
    });
  }
  paintAttendance(table);

  function recordRows() {
    return Array.from(table.querySelectorAll("tbody tr[data-record-row]"));
  }

  function selectedRows() {
    return recordRows().filter(function (row) {
      const checkbox = row.querySelector("[data-row-select]");
      return !row.hidden && checkbox?.checked;
    });
  }

  function syncSelection() {
    const selected = selectedRows();
    document.querySelectorAll("[data-selected-count]").forEach(function (node) {
      node.textContent = String(selected.length);
    });
    document.querySelectorAll("[data-panel-select]").forEach(function (checkbox) {
      const row = table.querySelector('tr[data-record-row][data-result-id="' + CSS.escape(checkbox.dataset.panelSelect) + '"]');
      checkbox.checked = Boolean(row?.querySelector("[data-row-select]")?.checked);
      checkbox.disabled = !row || row.hidden;
    });
    const visibleChecks = recordRows().filter(function (row) { return !row.hidden && row.querySelector("[data-row-select]"); });
    const selectAll = document.getElementById("gis-ws-select-all");
    if (selectAll) {
      selectAll.checked = visibleChecks.length > 0 && visibleChecks.every(function (row) { return row.querySelector("[data-row-select]").checked; });
      selectAll.indeterminate = selected.length > 0 && !selectAll.checked;
    }
  }

  const excel = window.ProCleanExcelTable?.create(table, {
    searchInput: search,
    showArchived: function () { return Boolean(showRemoved?.checked); },
    onChange: function (state) {
      const counter = document.getElementById("gis-ws-visible-count");
      if (counter) counter.textContent = String(state.visible);
      syncSelection();
    }
  });

  showRemoved?.addEventListener("change", function () { excel?.apply(); });
  document.getElementById("gis-ws-clear-filters")?.addEventListener("click", function () {
    excel?.filters.clear();
    if (search) search.value = "";
    excel?.apply();
  });

  table.addEventListener("click", function (event) {
    const toggle = event.target.closest("[data-toggle-detail]");
    if (toggle) {
      const row = toggle.closest("tr[data-record-row]");
      row.dataset.expanded = row.dataset.expanded === "1" ? "0" : "1";
      toggle.setAttribute("aria-expanded", row.dataset.expanded === "1" ? "true" : "false");
      excel?.apply();
      return;
    }
    const open = event.target.closest("[data-open-modal]");
    if (open && modal) {
      const template = document.getElementById(open.dataset.openModal);
      modal.querySelector("[data-modal-body]").innerHTML = template?.innerHTML || "";
      paintAttendance(modal);
      modal.hidden = false;
      modal.setAttribute("aria-hidden", "false");
    }
  });

  table.addEventListener("change", function (event) {
    if (event.target.matches("[data-row-select]")) syncSelection();
    if (event.target.id === "gis-ws-select-all") {
      recordRows().forEach(function (row) {
        const checkbox = row.querySelector("[data-row-select]");
        if (checkbox && !row.hidden) checkbox.checked = event.target.checked;
      });
      syncSelection();
    }
  });

  document.querySelectorAll("[data-panel-select]").forEach(function (checkbox) {
    checkbox.addEventListener("change", function () {
      const row = table.querySelector('tr[data-record-row][data-result-id="' + CSS.escape(checkbox.dataset.panelSelect) + '"]');
      const rowCheckbox = row?.querySelector("[data-row-select]");
      if (rowCheckbox) rowCheckbox.checked = checkbox.checked;
      syncSelection();
    });
  });

  function syncEventSelection() {
    const count = document.querySelectorAll("[data-event-select]:checked").length;
    document.querySelectorAll("[data-event-selected-count]").forEach(function (node) {
      node.textContent = String(count);
    });
  }
  document.querySelectorAll("[data-event-select]").forEach(function (checkbox) {
    checkbox.addEventListener("change", syncEventSelection);
  });

  document.getElementById("gis-ws-apply-patron")?.addEventListener("click", function () {
    const rp = document.getElementById("gis-ws-batch-rp")?.value.trim() || "";
    const rfc = document.getElementById("gis-ws-batch-rfc")?.value.trim() || "";
    selectedRows().forEach(function (row) {
      const id = row.dataset.resultId;
      const rpInput = document.querySelector('input[name="rp_' + CSS.escape(id) + '"]');
      const rfcInput = document.querySelector('input[name="rfc_patron_' + CSS.escape(id) + '"]');
      if (rpInput && rp) rpInput.value = rp;
      if (rfcInput && rfc) rfcInput.value = rfc;
    });
  });

  function closeModal() {
    if (!modal) return;
    modal.hidden = true;
    modal.setAttribute("aria-hidden", "true");
    modal.querySelector("[data-modal-body]").innerHTML = "";
  }
  modal?.querySelectorAll("[data-close-modal]").forEach(function (button) { button.addEventListener("click", closeModal); });
  document.addEventListener("keydown", function (event) { if (event.key === "Escape") closeModal(); });

  excel?.apply();
  syncSelection();
  syncEventSelection();
})();
