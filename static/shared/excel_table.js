(function (global) {
  "use strict";

  function normalize(value) {
    return String(value == null ? "" : value).replace(/\s+/g, " ").trim().toLocaleUpperCase("es-MX");
  }

  function recordRows(table) {
    return Array.from(table.querySelectorAll("tbody tr[data-record-row]"));
  }

  function cellValue(row, index) {
    const cell = row.cells[index];
    return normalize(cell?.dataset.filterValue != null ? cell.dataset.filterValue : cell?.innerText);
  }

  function uniqueValues(table, index) {
    const labels = new Map();
    recordRows(table).forEach(function (row) {
      const raw = row.cells[index]?.dataset.filterValue != null
        ? row.cells[index].dataset.filterValue
        : row.cells[index]?.innerText;
      const key = normalize(raw);
      if (!labels.has(key)) labels.set(key, String(raw == null || raw === "" ? "(Vacío)" : raw).trim());
    });
    return Array.from(labels, function (entry) { return { key: entry[0], label: entry[1] }; })
      .sort(function (a, b) { return a.label.localeCompare(b.label, "es", { numeric: true }); });
  }

  function createPanel() {
    const panel = document.createElement("div");
    panel.className = "pc-excel-filter";
    panel.hidden = true;
    panel.innerHTML = [
      '<strong data-filter-title>Filtrar</strong>',
      '<div class="pc-excel-filter__sort">',
      '<button type="button" data-sort="asc">Ordenar A→Z</button>',
      '<button type="button" data-sort="desc">Ordenar Z→A</button>',
      '</div>',
      '<input type="search" data-value-search placeholder="Buscar valor…" autocomplete="off">',
      '<label><input type="checkbox" data-select-all checked> Seleccionar todo</label>',
      '<div class="pc-excel-filter__values" data-values></div>',
      '<div class="pc-excel-filter__actions">',
      '<button type="button" data-clear>Limpiar</button>',
      '<button type="button" data-apply>Aplicar</button>',
      '</div>'
    ].join("");
    document.body.appendChild(panel);
    return panel;
  }

  function create(table, options) {
    options = options || {};
    const rows = recordRows(table);
    const originalOrder = new Map(rows.map(function (row, index) { return [row, index]; }));
    const filters = new Map();
    const panel = createPanel();
    const searchInput = options.searchInput || null;
    let activeIndex = -1;
    let activeButton = null;
    let sort = null;

    function detailRow(row) {
      const id = row.dataset.detailId;
      return id ? table.querySelector('tbody tr[data-detail-for="' + CSS.escape(id) + '"]') : null;
    }

    function passes(row) {
      const globalQuery = normalize(searchInput?.value);
      if (globalQuery && !normalize(row.dataset.search || row.innerText).includes(globalQuery)) return false;
      for (const entry of filters.entries()) {
        if (!entry[1].has(cellValue(row, entry[0]))) return false;
      }
      return true;
    }

    function apply() {
      let visible = 0;
      rows.forEach(function (row) {
        const showArchived = typeof options.showArchived === "function" && options.showArchived();
        const show = passes(row) && (showArchived || row.dataset.archived !== "1");
        row.hidden = !show;
        if (show) visible += 1;
        const detail = detailRow(row);
        if (detail) detail.hidden = !show || row.dataset.expanded !== "1";
        if (!show) {
          const checkbox = row.querySelector("[data-row-select]");
          if (checkbox) checkbox.checked = false;
        }
      });
      table.querySelectorAll("thead [data-excel-filter]").forEach(function (button) {
        const index = Number(button.dataset.columnIndex);
        button.classList.toggle("is-active", filters.has(index) || sort?.index === index);
      });
      table.dispatchEvent(new CustomEvent("proclean:tablechange", { detail: { visible: visible } }));
      if (typeof options.onChange === "function") options.onChange({ visible: visible, rows: rows });
    }

    function reorder(index, direction) {
      sort = { index: index, direction: direction };
      const body = table.tBodies[0];
      rows.sort(function (a, b) {
        const av = cellValue(a, index);
        const bv = cellValue(b, index);
        const compared = av.localeCompare(bv, "es", { numeric: true, sensitivity: "base" });
        return compared === 0 ? originalOrder.get(a) - originalOrder.get(b) : compared * (direction === "asc" ? 1 : -1);
      });
      rows.forEach(function (row) {
        body.appendChild(row);
        const detail = detailRow(row);
        if (detail) body.appendChild(detail);
      });
      apply();
    }

    function renderValues(query) {
      const host = panel.querySelector("[data-values]");
      const current = filters.get(activeIndex);
      host.innerHTML = "";
      uniqueValues(table, activeIndex).forEach(function (entry) {
        if (query && !normalize(entry.label).includes(normalize(query))) return;
        const label = document.createElement("label");
        const checkbox = document.createElement("input");
        checkbox.type = "checkbox";
        checkbox.value = entry.key;
        checkbox.checked = !current || current.has(entry.key);
        label.appendChild(checkbox);
        label.appendChild(document.createTextNode(" " + entry.label));
        host.appendChild(label);
      });
    }

    function open(button) {
      activeButton = button;
      activeIndex = Number(button.dataset.columnIndex);
      panel.querySelector("[data-filter-title]").textContent = button.dataset.filterLabel || "Filtrar";
      panel.querySelector("[data-value-search]").value = "";
      panel.querySelector("[data-select-all]").checked = !filters.has(activeIndex);
      renderValues("");
      const rect = button.getBoundingClientRect();
      panel.style.left = Math.max(8, Math.min(rect.left, window.innerWidth - 310)) + "px";
      panel.style.top = Math.min(rect.bottom + 6, window.innerHeight - 390) + "px";
      panel.hidden = false;
    }

    table.querySelectorAll("thead th").forEach(function (header, index) {
      if (!header.hasAttribute("data-excel-filter")) return;
      const button = document.createElement("button");
      button.type = "button";
      button.className = "pc-excel-filter-btn";
      button.dataset.excelFilter = "";
      button.dataset.columnIndex = String(index);
      button.dataset.filterLabel = header.dataset.filterLabel || header.innerText.trim();
      button.setAttribute("aria-label", "Filtrar " + button.dataset.filterLabel);
      button.textContent = "▾";
      header.appendChild(button);
      button.addEventListener("click", function (event) { event.stopPropagation(); open(button); });
    });

    panel.querySelector("[data-value-search]").addEventListener("input", function (event) {
      renderValues(event.target.value);
    });
    panel.querySelector("[data-select-all]").addEventListener("change", function (event) {
      panel.querySelectorAll('[data-values] input[type="checkbox"]').forEach(function (checkbox) {
        checkbox.checked = event.target.checked;
      });
    });
    panel.querySelector("[data-apply]").addEventListener("click", function () {
      const selected = new Set(Array.from(panel.querySelectorAll('[data-values] input:checked')).map(function (item) {
        return item.value;
      }));
      const all = uniqueValues(table, activeIndex);
      if (selected.size === all.length) filters.delete(activeIndex); else filters.set(activeIndex, selected);
      panel.hidden = true;
      apply();
    });
    panel.querySelector("[data-clear]").addEventListener("click", function () {
      filters.delete(activeIndex);
      panel.hidden = true;
      apply();
    });
    panel.querySelectorAll("[data-sort]").forEach(function (button) {
      button.addEventListener("click", function () { reorder(activeIndex, button.dataset.sort); panel.hidden = true; });
    });
    document.addEventListener("click", function (event) {
      if (!panel.hidden && event.target !== activeButton && !panel.contains(event.target)) panel.hidden = true;
    });
    searchInput?.addEventListener("input", apply);

    return { apply: apply, filters: filters, rows: rows, uniqueValues: function (index) { return uniqueValues(table, index); } };
  }

  global.ProCleanExcelTable = { create: create, normalize: normalize, uniqueValues: uniqueValues };
})(window);
