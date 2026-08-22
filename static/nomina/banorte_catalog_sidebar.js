(function () {
  const root = document.getElementById("banorte-root");
  if (!root) return;
  const sidebar = document.getElementById("banorte-catalog-sidebar");
  if (!sidebar) return;

  let csrf = root.dataset.csrf || "";
  let debounceTimer = null;
  let searchSeq = 0;
  let abortController = null;
  const SEARCH_URL = "/nomina/exportaciones/banorte/catalogo/sidebar/search";

  const input = document.getElementById("banorte-catalog-sidebar-q");
  const list = document.getElementById("banorte-catalog-sidebar-list");
  const statusEl = document.getElementById("banorte-catalog-sidebar-status");
  const loadMoreBtn = document.getElementById("banorte-catalog-sidebar-more");

  function setCsrf(token) {
    if (token) csrf = token;
  }

  function esc(s) {
    return String(s || "")
      .replace(/&/g, "&amp;")
      .replace(/"/g, "&quot;")
      .replace(/</g, "&lt;");
  }

  async function search(options) {
    options = options || {};
    const seq = ++searchSeq;
    if (abortController) abortController.abort();
    abortController = new AbortController();
    const body = {
      csrf_token: csrf,
      q: input ? input.value : "",
      sort: "employee_asc",
      limit: 25,
    };
    if (options.cursor) body.cursor = options.cursor;
    try {
      const res = await fetch(SEARCH_URL, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-CSRF-Token": csrf,
        },
        body: JSON.stringify(body),
        signal: abortController.signal,
      });
      const data = await res.json().catch(function () { return {}; });
      if (seq !== searchSeq) return null;
      setCsrf(data.csrf_token);
      return data;
    } catch (err) {
      if (err && err.name === "AbortError") return null;
      throw err;
    }
  }

  function renderItem(item) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "banorte-catalog-sidebar-item";
    btn.disabled = !item.payment_enabled;
    btn.dataset.catalogPersonId = String(item.catalog_person_id || "");
    btn.dataset.beneficiaryId = String(item.beneficiary_id || "");
    btn.dataset.authorityKind = String(item.authority_kind || "CATALOG");
    const statusClass = item.payment_enabled ? "banorte-status--ok" : "banorte-status--muted";
    const statusText = item.payment_enabled ? "Habilitado" : "Bloqueado";
    btn.innerHTML =
      '<span class="banorte-catalog-sidebar-item__name">' + esc(item.display_name) + "</span>" +
      '<span class="banorte-catalog-sidebar-item__meta">' +
      esc(item.employee_number || "—") + " · " + esc(item.account_masked || "—") +
      "</span>" +
      '<span class="banorte-status ' + statusClass + '">' + esc(statusText) + "</span>" +
      (item.block_reason ? '<span class="banorte-catalog-sidebar-item__reason">' + esc(item.block_reason) + "</span>" : "");
    if (item.payment_enabled) {
      btn.addEventListener("click", function () {
        document.dispatchEvent(new CustomEvent("banorte:catalog-person-selected", {
          detail: {
            catalog_person_id: item.catalog_person_id ? Number(item.catalog_person_id) : null,
            beneficiary_id: item.beneficiary_id ? Number(item.beneficiary_id) : null,
            authority_kind: item.authority_kind || "CATALOG",
            display_name: item.display_name || "",
            employee_number: item.employee_number || "",
            account_masked: item.account_masked || "",
          },
        }));
      });
    }
    return btn;
  }

  function renderResults(data, append) {
    if (!list || !statusEl) return;
    if (!append) list.innerHTML = "";
    if (!data.catalog_active) {
      statusEl.textContent = data.message || "Catálogo oficial todavía no activo";
      statusEl.hidden = false;
      if (loadMoreBtn) loadMoreBtn.hidden = true;
      return;
    }
    statusEl.hidden = true;
    (data.items || []).forEach(function (item) {
      list.appendChild(renderItem(item));
    });
    if (loadMoreBtn) {
      loadMoreBtn.hidden = !data.next_cursor;
      loadMoreBtn.dataset.cursor = data.next_cursor || "";
    }
  }

  async function runSearch(append) {
    append = !!append;
    try {
      const data = await search({
        cursor: append && loadMoreBtn ? loadMoreBtn.dataset.cursor : null,
      });
      if (!data || !data.ok) {
        if (statusEl) {
          statusEl.textContent = "No se pudo consultar el catálogo.";
          statusEl.hidden = false;
        }
        return;
      }
      renderResults(data, append);
    } catch (_err) {
      if (statusEl) {
        statusEl.textContent = "Error al consultar catálogo.";
        statusEl.hidden = false;
      }
    }
  }

  function scheduleSearch() {
    if (debounceTimer) clearTimeout(debounceTimer);
    debounceTimer = setTimeout(function () {
      debounceTimer = null;
      runSearch(false);
    }, 300);
  }

  if (input) {
    input.addEventListener("input", scheduleSearch);
  }
  if (loadMoreBtn) {
    loadMoreBtn.addEventListener("click", function () {
      runSearch(true);
    });
  }

  runSearch(false);
})();
