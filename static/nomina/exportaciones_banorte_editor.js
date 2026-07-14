(function () {
  const root = document.getElementById("banorte-root");
  if (!root) return;
  let csrf = root.dataset.csrf || "";
  let draft = null;

  function setCsrf(token) { if (token) csrf = token; }
  function money(cents) {
    return (Number(cents || 0) / 100).toFixed(2);
  }
  function esc(s) {
    return String(s || "").replace(/&/g, "&amp;").replace(/"/g, "&quot;").replace(/</g, "&lt;");
  }
  function statusClass(state) {
    if (state === "OK") return "banorte-status--ok";
    if (state === "NEEDS_REVIEW") return "banorte-status--warn";
    if (state === "BLOCKED") return "banorte-status--bad";
    return "banorte-status--muted";
  }
  function statusLabel(state) {
    if (state === "OK") return "Listo";
    if (state === "NEEDS_REVIEW") return "Revisión";
    if (state === "BLOCKED") return "Bloqueo";
    if (state === "EXCLUDED") return "Excluido";
    return state || "—";
  }

  async function api(url, body) {
    const res = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-CSRF-Token": csrf },
      body: JSON.stringify(Object.assign({ csrf_token: csrf }, body || {})),
    });
    const data = await res.json().catch(function () { return {}; });
    setCsrf(data.csrf_token);
    return { res: res, data: data };
  }

  function getConsecutiveValue() {
    const sel = document.getElementById("banorte-consec-select");
    const other = document.getElementById("banorte-consec-other");
    if (sel.value === "other") return (other.value || "").trim();
    return sel.value;
  }

  function applyConsecutivePref(pref) {
    const sel = document.getElementById("banorte-consec-select");
    const other = document.getElementById("banorte-consec-other");
    const val = String(pref || "01");
    if (/^0[1-9]$|^10$/.test(val) && parseInt(val, 10) <= 10) {
      sel.value = val;
      other.hidden = true;
    } else if (/^1[1-9]$|^[2-9][0-9]$/.test(val)) {
      sel.value = "other";
      other.hidden = false;
      other.value = val;
    } else {
      sel.value = "01";
      other.hidden = true;
    }
  }

  document.getElementById("banorte-consec-select").addEventListener("change", function () {
    const other = document.getElementById("banorte-consec-other");
    other.hidden = this.value !== "other";
  });

  function hasRestorableRows(d) {
    return (d.rows || []).some(function (r) { return r.excluded_at; });
  }

  function updateRestoreButton(d) {
    const btn = document.getElementById("banorte-restore-last");
    btn.disabled = !hasRestorableRows(d);
  }

  function renderRecon(rec) {
    const el = document.getElementById("banorte-recon");
    if (!rec) { el.innerHTML = ""; return; }
    el.innerHTML =
      "<div><span>Originales</span><strong>" + rec.original_row_count + "</strong></div>" +
      "<div><span>Incluidas</span><strong>" + rec.included_count + "</strong></div>" +
      "<div><span>Excluidas</span><strong>" + rec.excluded_count + "</strong></div>" +
      "<div><span>Total orig.</span><strong>$" + money(rec.total_original_cents) + "</strong></div>" +
      "<div><span>Total final</span><strong>$" + money(rec.total_final_cents) + "</strong></div>" +
      "<div><span>Pagos</span><strong>" + rec.payment_count + "</strong></div>";
  }

  function collectRows() {
    const rows = [];
    document.querySelectorAll("#banorte-editor tbody tr").forEach(function (tr) {
      rows.push({
        id: Number(tr.dataset.rowId),
        position: Number(tr.querySelector(".c-pos").textContent),
        calculo_row_id: tr.dataset.calculoRowId ? Number(tr.dataset.calculoRowId) : null,
        nombre_recibido: tr.querySelector(".c-name").value,
        beneficiary_id: tr.dataset.beneficiaryId ? Number(tr.dataset.beneficiaryId) : null,
        employee_number_snapshot: tr.querySelector(".c-emp").textContent.trim() || null,
        account_number_snapshot: tr.querySelector(".c-acct").textContent.trim() || null,
        amount_original_cents: Number(tr.dataset.originalCents || 0),
        amount_final_cents: Math.round(Number(tr.querySelector(".c-final").value || 0) * 100),
        included: Number(tr.dataset.included || 0),
        match_kind: tr.dataset.matchKind || "NONE",
        alias_id: tr.dataset.aliasId ? Number(tr.dataset.aliasId) : null,
        row_state: tr.dataset.rowState || "OK",
        warnings: JSON.parse(tr.dataset.warnings || "[]"),
        user_decision: JSON.parse(tr.dataset.userDecision || "{}"),
        nss_snapshot: tr.dataset.nss || null,
        banco_snapshot: tr.dataset.banco || null,
        excluded_at: tr.dataset.excludedAt || null,
        excluded_by: tr.dataset.excludedBy || null,
      });
    });
    return rows;
  }

  function bindAutocomplete(input, tr) {
    let timer = null;
    let listEl = null;
    input.addEventListener("input", function () {
      clearTimeout(timer);
      if (listEl) { listEl.remove(); listEl = null; }
      const q = input.value.trim();
      if (q.length < 3) return;
      timer = setTimeout(async function () {
        const out = await api("/nomina/exportaciones/banorte/beneficiarios/search-name", { q: q, limit: 10 });
        if (!out.data.ok || !out.data.rows || !out.data.rows.length) return;
        listEl = document.createElement("div");
        listEl.className = "banorte-ac-list";
        out.data.rows.forEach(function (b) {
          const btn = document.createElement("button");
          btn.type = "button";
          btn.textContent = b.nombre_original;
          btn.addEventListener("click", function () {
            tr.dataset.beneficiaryId = b.id;
            tr.querySelector(".c-emp").textContent = b.employee_number_effective || "—";
            tr.querySelector(".c-acct").textContent = b.account_number || "—";
            input.value = b.nombre_original;
            listEl.remove();
            listEl = null;
          });
          listEl.appendChild(btn);
        });
        input.parentElement.style.position = "relative";
        input.parentElement.appendChild(listEl);
      }, 250);
    });
  }

  function renderEditor(d) {
    draft = d;
    const panel = document.getElementById("banorte-editor-panel");
    panel.hidden = false;
    applyConsecutivePref(d.consecutive_pref || "01");
    document.getElementById("banorte-origin-box").textContent =
      "Borrador #" + d.id + " · " + d.origin_kind +
      (d.calculo_id ? (" · Cálculo #" + d.calculo_id) : "") +
      " · rev " + d.revision;
    renderRecon(d.reconciliation);
    updateRestoreButton(d);
    const tbody = document.querySelector("#banorte-editor tbody");
    tbody.innerHTML = "";
    (d.rows || []).forEach(function (row) {
      const tr = document.createElement("tr");
      tr.dataset.rowId = row.id;
      tr.dataset.calculoRowId = row.calculo_row_id || "";
      tr.dataset.matchKind = row.match_kind || "NONE";
      tr.dataset.aliasId = row.alias_id || "";
      tr.dataset.rowState = row.row_state || "";
      tr.dataset.included = row.included ? "1" : "0";
      tr.dataset.beneficiaryId = row.beneficiary_id || "";
      tr.dataset.excludedAt = row.excluded_at || "";
      tr.dataset.excludedBy = row.excluded_by || "";
      tr.dataset.warnings = JSON.stringify(row.warnings || JSON.parse(row.warnings_json || "[]"));
      tr.dataset.userDecision = JSON.stringify(row.user_decision || JSON.parse(row.user_decision_json || "{}"));
      tr.dataset.originalCents = row.amount_original_cents;
      tr.dataset.nss = row.nss_snapshot || "";
      tr.dataset.banco = row.banco_snapshot || "";
      tr.innerHTML =
        '<td class="c-pos">' + row.position + "</td>" +
        '<td class="c-worker"><input class="c-name" type="text" value="' + esc(row.nombre_recibido) + '"></td>' +
        '<td class="c-emp banorte-mono">' + esc(row.employee_number_snapshot || "—") + "</td>" +
        '<td class="c-acct banorte-mono">' + esc(row.account_number_snapshot || "—") + "</td>" +
        '<td><input class="c-final" type="number" step="0.01" value="' + money(row.amount_final_cents) + '"></td>' +
        '<td><span class="banorte-status ' + statusClass(row.row_state) + '">' + statusLabel(row.row_state) + "</span></td>" +
        '<td><button type="button" class="banorte-icon-btn c-trash" title="Excluir fila" aria-label="Excluir">' +
        '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14H6L5 6"/><path d="M10 11v6M14 11v6"/><path d="M9 6V4h6v2"/></svg></button></td>';
      tbody.appendChild(tr);
      bindAutocomplete(tr.querySelector(".c-name"), tr);
      tr.querySelector(".c-trash").addEventListener("click", async function () {
        if (!draft) return;
        const out = await api("/nomina/exportaciones/banorte/drafts/" + draft.id + "/exclude-row", {
          expected_revision: draft.revision,
          row_id: Number(tr.dataset.rowId),
        });
        if (out.res.status === 409) { alert("Borrador desactualizado."); return; }
        if (!out.data.ok) { alert(out.data.code || "error"); return; }
        renderEditor(out.data.draft);
      });
    });
  }

  document.querySelectorAll("[data-banorte-tab]").forEach(function (btn) {
    btn.addEventListener("click", function () {
      const tab = btn.getAttribute("data-banorte-tab");
      document.querySelectorAll("[data-banorte-panel]").forEach(function (panel) {
        panel.hidden = panel.getAttribute("data-banorte-panel") !== tab;
      });
    });
  });
  function showHub() {
    document.querySelectorAll("[data-banorte-panel]").forEach(function (panel) {
      panel.hidden = panel.getAttribute("data-banorte-panel") !== "hub";
    });
  }

  document.getElementById("banorte-restore-last").addEventListener("click", async function () {
    if (!draft) return;
    const out = await api("/nomina/exportaciones/banorte/drafts/" + draft.id + "/restore-last", {
      expected_revision: draft.revision,
    });
    if (out.res.status === 409) { alert("Borrador desactualizado."); return; }
    if (!out.data.ok) { alert(out.data.code || "error"); return; }
    renderEditor(out.data.draft);
  });

  document.querySelectorAll("[data-prepare-calculo]").forEach(function (btn) {
    btn.addEventListener("click", async function () {
      const id = btn.getAttribute("data-prepare-calculo");
      btn.disabled = true;
      const out = await api("/nomina/exportaciones/banorte/drafts/from-calculo/" + id, {});
      btn.disabled = false;
      if (!out.data.ok) {
        alert("No se pudo preparar: " + (out.data.code || out.res.status));
        return;
      }
      showHub();
      renderEditor(out.data.draft);
    });
  });

  document.getElementById("banorte-save-draft").addEventListener("click", async function () {
    if (!draft) return;
    const out = await api("/nomina/exportaciones/banorte/drafts/" + draft.id + "/save", {
      expected_revision: draft.revision,
      rows: collectRows(),
      consecutive_pref: getConsecutiveValue(),
    });
    if (out.res.status === 409) {
      alert("Borrador desactualizado. Recargue el editor.");
      return;
    }
    if (!out.data.ok) {
      alert("Error al guardar: " + (out.data.code || ""));
      return;
    }
    renderEditor(out.data.draft);
  });

  document.getElementById("banorte-generate").addEventListener("click", async function () {
    if (!draft) return;
    const out = await api("/nomina/exportaciones/banorte/drafts/" + draft.id + "/generate", {
      expected_revision: draft.revision,
      consecutive: getConsecutiveValue(),
      confirm_manuals: document.getElementById("banorte-confirm-manual").checked,
      confirm_duplicate_consecutive: document.getElementById("banorte-confirm-dup").checked,
    });
    const msg = document.getElementById("banorte-export-msg");
    if (out.res.status === 409) {
      msg.hidden = false;
      msg.textContent = "Revisión obsoleta — recargue.";
      return;
    }
    if (!out.data.ok) {
      msg.hidden = false;
      msg.textContent = "Bloqueado: " + (out.data.code || out.res.status);
      return;
    }
    if (out.data.layout_date_display) {
      document.getElementById("banorte-app-date").textContent = out.data.layout_date_display;
    }
    msg.hidden = false;
    msg.textContent = "Generado " + out.data.filename;
    window.location.href = "/nomina/exportaciones/banorte/historial/" + out.data.export_id + "/download";
  });

  document.getElementById("banorte-abandon-draft").addEventListener("click", async function () {
    if (!draft) return;
    if (!confirm("¿Abandonar este borrador? No se generará archivo.")) return;
    const out = await api("/nomina/exportaciones/banorte/drafts/" + draft.id + "/abandon", {
      expected_revision: draft.revision,
      confirm: true,
    });
    if (out.data.ok) {
      draft = null;
      document.getElementById("banorte-editor-panel").hidden = true;
    } else {
      alert(out.data.code || "No se pudo abandonar");
    }
  });

  document.getElementById("banorte-toggle-origin").addEventListener("click", function () {
    const box = document.getElementById("banorte-origin-box");
    box.hidden = !box.hidden;
  });

  document.getElementById("banorte-alta-form").addEventListener("submit", async function (e) {
    e.preventDefault();
    const fd = new FormData(e.target);
    const out = await api("/nomina/exportaciones/banorte/beneficiarios/create", {
      nombre: fd.get("nombre"),
      account: fd.get("account"),
      confirm_effective_from_account: fd.get("confirm_effective_from_account") === "1",
    });
    if (!out.data.ok) {
      alert("Alta: " + (out.data.code || ""));
      return;
    }
    alert("Beneficiario creado #" + out.data.beneficiary.id);
    showHub();
  });

  document.getElementById("manual-prepare").addEventListener("click", async function () {
    const choice = document.getElementById("manual-choice");
    const out = await api("/nomina/exportaciones/banorte/drafts/manual", {
      names: document.getElementById("manual-names").value,
      amounts: document.getElementById("manual-amounts").value,
    });
    if (out.res.status === 409 && out.data.code === "manual_open_exists") {
      choice.hidden = false;
      choice.innerHTML =
        "Ya existe un borrador manual OPEN #" + out.data.existing_draft_id +
        ". <button type='button' id='manual-continue' class='btn btn-secondary btn-sm'>Continuar</button> " +
        "<button type='button' id='manual-abandon-then' class='btn btn-secondary btn-sm'>Abandonar y reiniciar</button>";
      document.getElementById("manual-continue").onclick = async function () {
        const g = await fetch("/nomina/exportaciones/banorte/drafts/" + out.data.existing_draft_id);
        const data = await g.json();
        setCsrf(data.csrf_token);
        showHub();
        renderEditor(data.draft);
      };
      document.getElementById("manual-abandon-then").onclick = async function () {
        if (!confirm("¿Abandonar el borrador manual existente?")) return;
        const ab = await api("/nomina/exportaciones/banorte/drafts/" + out.data.existing_draft_id + "/abandon", {
          expected_revision: out.data.existing_revision,
          confirm: true,
        });
        if (!ab.data.ok) { alert(ab.data.code || "error"); return; }
        const again = await api("/nomina/exportaciones/banorte/drafts/manual", {
          names: document.getElementById("manual-names").value,
          amounts: document.getElementById("manual-amounts").value,
        });
        if (again.data.ok) { showHub(); renderEditor(again.data.draft); }
        else alert(again.data.code || "error");
      };
      return;
    }
    if (!out.data.ok) {
      alert(out.data.code || "error");
      return;
    }
    showHub();
    renderEditor(out.data.draft);
  });

  function renderBenefRows(listing) {
    const tbody = document.querySelector("#banorte-ben-table tbody");
    if (!tbody) return;
    tbody.innerHTML = "";
    (listing.rows || []).forEach(function (b) {
      const tr = document.createElement("tr");
      let st = '<span class="banorte-status banorte-status--warn">Pendiente</span>';
      if (b.validation_status === "IMPORTADO_EXITOSO" && b.record_status === "ACTIVO") {
        st = '<span class="banorte-status banorte-status--ok">Validado Banorte</span>';
      } else if (b.manual_effective_from_account && b.record_status === "ACTIVO") {
        st = '<span class="banorte-status banorte-status--ok">Usizable (manual)</span>';
      }
      tr.innerHTML =
        "<td>" + b.id + "</td><td>" + esc(b.nombre_original) + "</td>" +
        "<td>" + esc(b.employee_number_effective) + "</td>" +
        '<td class="banorte-mono">' + esc(b.account_number) + "</td><td>" + st + "</td>";
      tbody.appendChild(tr);
    });
    document.getElementById("banorte-ben-meta").textContent =
      "Total " + listing.total + " · página " + listing.page;
  }

  async function loadBenefListing(page, opts) {
    const params = new URLSearchParams({ page: String(page || 1) });
    if (opts.validation_status) params.set("validation_status", opts.validation_status);
    if (opts.record_status) params.set("record_status", opts.record_status);
    const res = await fetch("/nomina/exportaciones/banorte/beneficiarios/list?" + params.toString());
    const data = await res.json();
    setCsrf(data.csrf_token);
    if (data.ok) renderBenefRows(data.listing);
  }

  document.getElementById("banorte-ben-search").addEventListener("click", async function () {
    const q = document.getElementById("banorte-ben-q").value.trim();
    if (q.length >= 3) {
      const out = await api("/nomina/exportaciones/banorte/beneficiarios/search-name", { q: q, limit: 50 });
      if (out.data.ok) renderBenefRows({ rows: out.data.rows, total: out.data.rows.length, page: 1 });
      return;
    }
    loadBenefListing(1, {
      validation_status: document.getElementById("banorte-ben-val").value,
      record_status: document.getElementById("banorte-ben-rec").value,
    });
  });
})();
