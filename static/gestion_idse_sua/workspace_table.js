(function () {
  "use strict";

  const table = document.getElementById("gis-ws-table");
  if (!table) return;

  const palette = JSON.parse(document.getElementById("gis-ws-palette")?.textContent || "{}");
  const bulkBar = document.getElementById("gis-ws-bulkbar");
  const drawer = document.getElementById("gis-ws-drawer");
  const filters = {};

  function attStyle(code) {
    const key = String(code || "").toUpperCase();
    const st = palette[key] || palette._neutral || { bg: "#f3f4f6", bold: false };
    return `background:${st.bg};font-weight:${st.bold ? 700 : 600}`;
  }

  table.querySelectorAll(".gis-ws-att-cell").forEach((cell) => {
    const code = cell.dataset.code || "";
    if (code) cell.setAttribute("style", attStyle(code));
  });

  function selectedRows() {
    return [...table.querySelectorAll("tbody tr")].filter((row) => {
      const cb = row.querySelector('input[type="checkbox"][data-row-select]');
      return cb && cb.checked && !row.hidden;
    });
  }

  function updateBulkBar() {
    const count = selectedRows().length;
    if (!bulkBar) return;
    bulkBar.classList.toggle("is-visible", count > 0);
    const label = bulkBar.querySelector("[data-selected-count]");
    if (label) label.textContent = String(count);
  }

  table.addEventListener("change", (ev) => {
    if (ev.target.matches("[data-row-select], #gis-ws-select-all")) {
      if (ev.target.id === "gis-ws-select-all") {
        table.querySelectorAll("[data-row-select]").forEach((cb) => {
          const row = cb.closest("tr");
          if (row && !row.hidden) cb.checked = ev.target.checked;
        });
      }
      updateBulkBar();
    }
  });

  function applyFilters() {
    const q = (document.getElementById("gis-ws-search")?.value || "").toLowerCase();
    table.querySelectorAll("tbody tr").forEach((row) => {
      const text = (row.dataset.search || "").toLowerCase();
      let ok = !q || text.includes(q);
      Object.entries(filters).forEach(([key, val]) => {
        if (val && row.dataset[key] !== val) ok = false;
      });
      row.hidden = !ok;
    });
    updateBulkBar();
  }

  document.getElementById("gis-ws-search")?.addEventListener("input", applyFilters);
  document.querySelectorAll("[data-filter-col]").forEach((sel) => {
    sel.addEventListener("change", (ev) => {
      filters[ev.target.dataset.filterCol] = ev.target.value;
      applyFilters();
    });
  });
  document.getElementById("gis-ws-clear-filters")?.addEventListener("click", () => {
    Object.keys(filters).forEach((k) => delete filters[k]);
    document.querySelectorAll("[data-filter-col]").forEach((sel) => { sel.value = ""; });
    document.getElementById("gis-ws-search").value = "";
    applyFilters();
  });

  document.querySelectorAll("[data-open-drawer]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const row = btn.closest("tr");
      if (!row || !drawer) return;
      drawer.querySelector("[data-drawer-title]").textContent = row.dataset.drawerTitle || "Detalle";
      drawer.querySelector("[data-drawer-body]").innerHTML = row.querySelector(".gis-ws-drawer-template")?.innerHTML || "";
      drawer.classList.add("is-open");
    });
  });

  drawer?.querySelector("[data-close-drawer]")?.addEventListener("click", () => drawer.classList.remove("is-open"));
  drawer?.querySelector(".gis-ws-drawer__backdrop")?.addEventListener("click", () => drawer.classList.remove("is-open"));

  document.getElementById("gis-ws-apply-patron")?.addEventListener("click", () => {
    const rp = document.getElementById("gis-ws-batch-rp")?.value || "";
    const rfc = document.getElementById("gis-ws-batch-rfc")?.value || "";
    selectedRows().forEach((row) => {
      const rid = row.dataset.resultId;
      if (!rid) return;
      const rpInput = document.querySelector(`input[name="rp_${rid}"]`);
      const rfcInput = document.querySelector(`input[name="rfc_patron_${rid}"]`);
      if (rpInput && rp) rpInput.value = rp;
      if (rfcInput && rfc) rfcInput.value = rfc;
    });
  });

  updateBulkBar();
})();
