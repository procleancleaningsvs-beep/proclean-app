(function () {
  const root = document.getElementById("banorte-root");
  if (!root) return;
  let csrf = root.dataset.csrf || "";
  let draft = null;
  const editorPanel = document.getElementById("banorte-editor-panel");

  /** Serialized mutation queue per draft (coalesce amount; beneficiary immediate). */
  const mutationQueue = {
    active: false,
    pendingByRow: Object.create(null),
    amountTimers: Object.create(null),
  };

  function setCsrf(token) { if (token) csrf = token; }
  function money(cents) {
    return (Number(cents || 0) / 100).toFixed(2);
  }
  function esc(s) {
    return String(s || "").replace(/&/g, "&amp;").replace(/"/g, "&quot;").replace(/</g, "&lt;");
  }
  function statusClass(state) {
    if (state === "OK") return "banorte-status--ok";
    if (state === "NEEDS_REVIEW" || state === "BLOCKED") return "banorte-status--warn";
    if (state === "EXCLUDED") return "banorte-status--muted";
    return "banorte-status--muted";
  }
  function statusLabel(state) {
    if (state === "OK") return "Listo";
    if (state === "NEEDS_REVIEW" || state === "BLOCKED") return "Requiere corrección";
    if (state === "EXCLUDED") return "No incluido";
    return state || "—";
  }
  function warningLabel(warnings) {
    const w = warnings || [];
    if (w.indexOf("amount_zero") >= 0) return "Monto en cero";
    if (w.indexOf("amount_invalid") >= 0) return "Monto inválido";
    if (w.indexOf("banco_no_banorte") >= 0 || w.indexOf("banco_vacio") >= 0) return "Banco no Banorte";
    if (w.length) return w[0];
    return "Excluido";
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

  function setBusy(busy) {
    if (!editorPanel) return;
    editorPanel.classList.toggle("banorte-editor-busy", !!busy);
  }

  function clearMutationQueue() {
    Object.keys(mutationQueue.amountTimers).forEach(function (k) {
      clearTimeout(mutationQueue.amountTimers[k]);
    });
    mutationQueue.amountTimers = Object.create(null);
    mutationQueue.pendingByRow = Object.create(null);
    mutationQueue.active = false;
    setBusy(false);
  }

  function enqueueApply(rowId, payload, opts) {
    opts = opts || {};
    const key = String(rowId);
    const prev = mutationQueue.pendingByRow[key] || {};
    const next = Object.assign({}, prev, payload, { rowId: Number(rowId) });
    if (opts.beneficiary) next._priority = "beneficiary";
    mutationQueue.pendingByRow[key] = next;
    if (opts.immediate) {
      if (mutationQueue.amountTimers[key]) {
        clearTimeout(mutationQueue.amountTimers[key]);
        delete mutationQueue.amountTimers[key];
      }
      drainMutationQueue();
      return;
    }
    if (opts.debounceMs) {
      if (mutationQueue.amountTimers[key]) clearTimeout(mutationQueue.amountTimers[key]);
      mutationQueue.amountTimers[key] = setTimeout(function () {
        delete mutationQueue.amountTimers[key];
        drainMutationQueue();
      }, opts.debounceMs);
      return;
    }
    drainMutationQueue();
  }

  async function drainMutationQueue() {
    if (mutationQueue.active || !draft) return;
    const keys = Object.keys(mutationQueue.pendingByRow);
    if (!keys.length) {
      setBusy(false);
      return;
    }
    keys.sort(function (a, b) {
      const pa = mutationQueue.pendingByRow[a]._priority === "beneficiary" ? 0 : 1;
      const pb = mutationQueue.pendingByRow[b]._priority === "beneficiary" ? 0 : 1;
      return pa - pb;
    });
    const key = keys[0];
    const job = mutationQueue.pendingByRow[key];
    delete mutationQueue.pendingByRow[key];
    mutationQueue.active = true;
    setBusy(true);
    try {
      const body = {
        expected_revision: draft.revision,
        amount_final: job.amount_final,
      };
      if (job.beneficiary_id != null) body.beneficiary_id = job.beneficiary_id;
      if (job.nombre_recibido != null) body.nombre_recibido = job.nombre_recibido;
      const out = await api(
        "/nomina/exportaciones/banorte/drafts/" + draft.id + "/rows/" + job.rowId + "/apply",
        body
      );
      if (out.res.status === 409 || out.data.code === "draft_stale") {
        clearMutationQueue();
        await reloadDraft("Borrador desactualizado. Se recargó el editor.");
        return;
      }
      if (!out.data.ok) {
        alert(out.data.code || "No se pudo aplicar el cambio");
        return;
      }
      renderEditor(out.data.draft);
    } finally {
      mutationQueue.active = false;
      setBusy(Object.keys(mutationQueue.pendingByRow).length > 0);
      if (Object.keys(mutationQueue.pendingByRow).length) {
        drainMutationQueue();
      }
    }
  }

  async function reloadDraft(message) {
    if (!draft) return;
    const res = await fetch("/nomina/exportaciones/banorte/drafts/" + draft.id);
    const data = await res.json().catch(function () { return {}; });
    setCsrf(data.csrf_token);
    if (data.ok && data.draft) {
      renderEditor(data.draft);
      if (message) {
        const msg = document.getElementById("banorte-export-msg");
        msg.hidden = false;
        msg.textContent = message;
      }
    }
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

  function splitRows(d) {
    const main = [];
    const excluded = [];
    (d.rows || []).forEach(function (row) {
      if (row.row_state === "EXCLUDED") excluded.push(row);
      else main.push(row);
    });
    return { main: main, excluded: excluded };
  }

  function collectRows() {
    const rows = [];
    document.querySelectorAll("#banorte-editor tbody tr, #banorte-excluded tbody tr").forEach(function (tr) {
      rows.push({
        id: Number(tr.dataset.rowId),
        position: Number(tr.dataset.position || tr.querySelector(".c-pos").textContent),
        calculo_row_id: tr.dataset.calculoRowId ? Number(tr.dataset.calculoRowId) : null,
        nombre_recibido: (tr.querySelector(".c-name") && tr.querySelector(".c-name").value) || tr.dataset.nombre || "",
        beneficiary_id: tr.dataset.beneficiaryId ? Number(tr.dataset.beneficiaryId) : null,
        employee_number_snapshot: (tr.querySelector(".c-emp") && tr.querySelector(".c-emp").textContent.trim()) || null,
        account_number_snapshot: (tr.querySelector(".c-acct") && tr.querySelector(".c-acct").textContent.trim()) || null,
        amount_original_cents: Number(tr.dataset.originalCents || 0),
        amount_final_cents: Math.round(Number((tr.querySelector(".c-final") && tr.querySelector(".c-final").value) || tr.dataset.finalMoney || 0) * 100),
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
      if (q.length < 2) return;
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
            input.value = b.nombre_original;
            if (listEl) { listEl.remove(); listEl = null; }
            enqueueApply(tr.dataset.rowId, {
              beneficiary_id: b.id,
              nombre_recibido: b.nombre_original,
              amount_final: tr.querySelector(".c-final").value,
            }, { immediate: true, beneficiary: true });
          });
          listEl.appendChild(btn);
        });
        input.parentElement.style.position = "relative";
        input.parentElement.appendChild(listEl);
      }, 250);
    });
  }

  function bindAmount(input, tr) {
    function flush() {
      enqueueApply(tr.dataset.rowId, {
        beneficiary_id: tr.dataset.beneficiaryId ? Number(tr.dataset.beneficiaryId) : null,
        nombre_recibido: tr.querySelector(".c-name").value,
        amount_final: input.value,
      }, { immediate: true });
    }
    input.addEventListener("input", function () {
      enqueueApply(tr.dataset.rowId, {
        beneficiary_id: tr.dataset.beneficiaryId ? Number(tr.dataset.beneficiaryId) : null,
        nombre_recibido: tr.querySelector(".c-name").value,
        amount_final: input.value,
      }, { debounceMs: 350 });
    });
    input.addEventListener("blur", flush);
    input.addEventListener("keydown", function (e) {
      if (e.key === "Enter") {
        e.preventDefault();
        flush();
      }
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
    const parts = splitRows(d);
    const tbody = document.querySelector("#banorte-editor tbody");
    tbody.innerHTML = "";
    parts.main.forEach(function (row) {
      const tr = document.createElement("tr");
      tr.dataset.rowId = row.id;
      tr.dataset.position = row.position;
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
      bindAmount(tr.querySelector(".c-final"), tr);
      tr.querySelector(".c-trash").addEventListener("click", async function () {
        if (!draft || mutationQueue.active) return;
        if (!confirm("¿Excluir esta fila del archivo .pag?")) return;
        setBusy(true);
        try {
          const out = await api("/nomina/exportaciones/banorte/drafts/" + draft.id + "/exclude-row", {
            expected_revision: draft.revision,
            row_id: Number(tr.dataset.rowId),
            confirm: true,
          });
          if (out.res.status === 409) {
            clearMutationQueue();
            await reloadDraft("Borrador desactualizado. Se recargó el editor.");
            return;
          }
          if (!out.data.ok) { alert(out.data.code || "error"); return; }
          renderEditor(out.data.draft);
        } finally {
          setBusy(false);
        }
      });
    });

    const wrap = document.getElementById("banorte-excluded-wrap");
    const summary = document.getElementById("banorte-excluded-summary");
    const xtbody = document.querySelector("#banorte-excluded tbody");
    xtbody.innerHTML = "";
    if (!parts.excluded.length) {
      wrap.hidden = true;
    } else {
      wrap.hidden = false;
      summary.textContent = "No incluidos (" + parts.excluded.length + ")";
      parts.excluded.forEach(function (row) {
        const warnings = row.warnings || JSON.parse(row.warnings_json || "[]");
        const tr = document.createElement("tr");
        tr.dataset.rowId = row.id;
        tr.dataset.position = row.position;
        tr.dataset.rowState = row.row_state || "EXCLUDED";
        tr.dataset.included = "0";
        tr.dataset.beneficiaryId = row.beneficiary_id || "";
        tr.dataset.excludedAt = row.excluded_at || "";
        tr.dataset.excludedBy = row.excluded_by || "";
        tr.dataset.warnings = JSON.stringify(warnings);
        tr.dataset.userDecision = JSON.stringify(row.user_decision || {});
        tr.dataset.originalCents = row.amount_original_cents;
        tr.dataset.nombre = row.nombre_recibido || "";
        tr.dataset.finalMoney = money(row.amount_final_cents);
        tr.dataset.nss = row.nss_snapshot || "";
        tr.dataset.banco = row.banco_snapshot || "";
        tr.innerHTML =
          '<td class="c-pos">' + row.position + "</td>" +
          "<td>" + esc(row.nombre_recibido) + "</td>" +
          '<td class="c-emp banorte-mono">' + esc(row.employee_number_snapshot || "—") + "</td>" +
          '<td class="c-acct banorte-mono">' + esc(row.account_number_snapshot || "—") + "</td>" +
          "<td>$" + money(row.amount_final_cents) + "</td>" +
          '<td><span class="banorte-status ' + statusClass("EXCLUDED") + '">No incluido</span></td>' +
          "<td>" + esc(warningLabel(warnings)) + "</td>";
        xtbody.appendChild(tr);
      });
    }
    applyViewFilters();
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
    if (!draft || mutationQueue.active) return;
    setBusy(true);
    try {
      const out = await api("/nomina/exportaciones/banorte/drafts/" + draft.id + "/restore-last", {
        expected_revision: draft.revision,
      });
      if (out.res.status === 409) {
        clearMutationQueue();
        await reloadDraft("Borrador desactualizado. Se recargó el editor.");
        return;
      }
      if (!out.data.ok) { alert(out.data.code || "error"); return; }
      renderEditor(out.data.draft);
    } finally {
      setBusy(false);
    }
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
      if ((out.data.omitted && out.data.omitted.length) || (out.data.amount_errors && out.data.amount_errors.length)) {
        const msg = document.getElementById("banorte-export-msg");
        msg.hidden = false;
        const bits = [];
        (out.data.omitted || []).forEach(function (o) {
          bits.push((o.causa || "omitido") + ": " + o.count + " filas");
        });
        (out.data.amount_errors || []).forEach(function (e) {
          bits.push((e.causa || "monto") + " en " + (e.nombre || ("#" + e.calculo_row_id)));
        });
        msg.textContent = "Resumen de origen — " + bits.join("; ");
      }
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
      clearMutationQueue();
      await reloadDraft("Borrador desactualizado. Se recargó el editor.");
      return;
    }
    if (!out.data.ok) {
      alert("Error al guardar: " + (out.data.code || ""));
      return;
    }
    renderEditor(out.data.draft);
  });

  function showModal(text) {
    return new Promise(function (resolve) {
      const modal = document.getElementById("banorte-modal");
      document.getElementById("banorte-modal-text").textContent = text;
      modal.hidden = false;
      function done(ok) {
        modal.hidden = true;
        document.getElementById("banorte-modal-ok").onclick = null;
        document.getElementById("banorte-modal-cancel").onclick = null;
        resolve(ok);
      }
      document.getElementById("banorte-modal-ok").onclick = function () { done(true); };
      document.getElementById("banorte-modal-cancel").onclick = function () { done(false); };
    });
  }

  async function runGenerate(flags) {
    flags = flags || {};
    const out = await api("/nomina/exportaciones/banorte/drafts/" + draft.id + "/generate", {
      expected_revision: draft.revision,
      consecutive: getConsecutiveValue(),
      confirm_manuals: !!flags.confirm_manuals,
      confirm_duplicate_consecutive: !!flags.confirm_duplicate_consecutive,
    });
    const msg = document.getElementById("banorte-export-msg");
    if (out.res.status === 409) {
      clearMutationQueue();
      await reloadDraft("Revisión obsoleta — se recargó el editor.");
      return;
    }
    if (!out.data.ok) {
      if (out.data.code === "manual_beneficiaries_confirmation_required") {
        const ok = await showModal("Hay beneficiarios de alta manual. ¿Confirma incluirlos en el archivo .pag?");
        if (ok) return runGenerate({ confirm_manuals: true, confirm_duplicate_consecutive: flags.confirm_duplicate_consecutive });
        return;
      }
      if (out.data.code === "duplicate_consecutive_confirmation_required") {
        const prior = out.data.prior_export_id ? (" (exportación #" + out.data.prior_export_id + ")") : "";
        const ok = await showModal("El consecutivo ya existe para hoy" + prior + ". ¿Confirma generar de todos modos?");
        if (ok) return runGenerate({ confirm_manuals: flags.confirm_manuals, confirm_duplicate_consecutive: true });
        return;
      }
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
  }

  document.getElementById("banorte-generate").addEventListener("click", async function () {
    if (!draft || mutationQueue.active) return;
    await runGenerate({});
  });

  document.getElementById("banorte-abandon-draft").addEventListener("click", async function () {
    if (!draft) return;
    if (!confirm("¿Abandonar este borrador? No se generará archivo.")) return;
    const out = await api("/nomina/exportaciones/banorte/drafts/" + draft.id + "/abandon", {
      expected_revision: draft.revision,
      confirm: true,
    });
    if (out.data.ok) {
      clearMutationQueue();
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
        st = '<span class="banorte-status banorte-status--ok">Utilizable (manual)</span>';
      } else if (b.record_status === "INACTIVO_MANUAL") {
        st = '<span class="banorte-status banorte-status--muted">Inactivo manual</span>';
      } else if (b.record_status === "INACTIVO_REEMPLAZADO") {
        st = '<span class="banorte-status banorte-status--muted">Reemplazado</span>';
      } else if (b.record_status === "CONFLICTO_CRITICO") {
        st = '<span class="banorte-status banorte-status--bad">Conflicto</span>';
      }
      const note = b.last_event_reason || b.banorte_comment || "";
      tr.innerHTML =
        "<td>" + b.id + "</td><td>" + esc(b.nombre_original) +
        (note ? ('<div class="banorte-hint">' + esc(note) + "</div>") : "") +
        "</td>" +
        "<td>" + esc(b.employee_number_effective) + "</td>" +
        '<td class="banorte-mono">' + esc(b.account_number) + "</td><td>" + st + "</td>";
      tbody.appendChild(tr);
    });
    document.getElementById("banorte-ben-meta").textContent =
      "Total " + listing.total + " · página " + listing.page + " · " + listing.page_size + "/pág";
  }

  let benSearchSeq = 0;
  let benSearchTimer = null;
  async function loadBenefListing(page) {
    const seq = ++benSearchSeq;
    const out = await api("/nomina/exportaciones/banorte/beneficiarios/search", {
      page: page || 1,
      q_name: (document.getElementById("banorte-ben-q").value || "").trim(),
      q_emp: (document.getElementById("banorte-ben-emp") && document.getElementById("banorte-ben-emp").value || "").trim(),
      validation_status: document.getElementById("banorte-ben-val").value,
      record_status: document.getElementById("banorte-ben-rec").value,
    });
    if (seq !== benSearchSeq) return;
    if (out.data.ok) renderBenefRows(out.data.listing);
  }

  function scheduleBenefSearch() {
    clearTimeout(benSearchTimer);
    benSearchTimer = setTimeout(function () { loadBenefListing(1); }, 300);
  }

  ["banorte-ben-q", "banorte-ben-emp", "banorte-ben-val", "banorte-ben-rec"].forEach(function (id) {
    const el = document.getElementById(id);
    if (!el) return;
    el.addEventListener(el.tagName === "SELECT" ? "change" : "input", scheduleBenefSearch);
  });

  function applyViewFilters() {
    const state = (document.getElementById("banorte-view-state") || {}).value || "all";
    const q = ((document.getElementById("banorte-view-q") || {}).value || "").trim().toLowerCase();
    document.querySelectorAll("#banorte-editor tbody tr").forEach(function (tr) {
      const rs = tr.dataset.rowState || "";
      const name = (tr.querySelector(".c-name") && tr.querySelector(".c-name").value || "").toLowerCase();
      const emp = (tr.querySelector(".c-emp") && tr.querySelector(".c-emp").textContent || "").toLowerCase();
      let ok = true;
      if (state === "OK" && rs !== "OK") ok = false;
      if (state === "NEEDS_REVIEW" && rs !== "NEEDS_REVIEW" && rs !== "BLOCKED") ok = false;
      if (q && name.indexOf(q) < 0 && emp.indexOf(q) < 0) ok = false;
      tr.hidden = !ok;
    });
  }
  const viewState = document.getElementById("banorte-view-state");
  const viewQ = document.getElementById("banorte-view-q");
  if (viewState) viewState.addEventListener("change", applyViewFilters);
  if (viewQ) viewQ.addEventListener("input", applyViewFilters);

  async function loadAvailableNumbers() {
    const box = document.getElementById("banorte-available-emps");
    if (!box) return;
    const out = await api("/nomina/exportaciones/banorte/beneficiarios/available-employee-numbers", {
      limit: 15,
    });
    if (!out.data.ok) { box.textContent = "No disponibles"; return; }
    box.textContent = (out.data.numbers || []).join(" · ");
  }
  const altaTabBtn = document.querySelector('[data-banorte-tab="alta-benef"]');
  if (altaTabBtn) altaTabBtn.addEventListener("click", loadAvailableNumbers);

  let excelToken = null;
  async function excelMultipart(url, extra) {
    const fileInput = document.getElementById("banorte-excel-file");
    if (!fileInput.files || !fileInput.files[0]) return { res: { status: 400 }, data: { ok: false, code: "file_required" } };
    const fd = new FormData();
    fd.append("file", fileInput.files[0]);
    fd.append("csrf_token", csrf);
    Object.keys(extra || {}).forEach(function (k) { fd.append(k, extra[k]); });
    const res = await fetch(url, { method: "POST", headers: { "X-CSRF-Token": csrf }, body: fd });
    const data = await res.json().catch(function () { return {}; });
    setCsrf(data.csrf_token);
    return { res: res, data: data };
  }

  document.getElementById("banorte-excel-inspect").addEventListener("click", async function () {
    const out = await excelMultipart("/nomina/exportaciones/banorte/excel/inspect");
    if (!out.data.ok) { alert(out.data.code || "inspect error"); return; }
    excelToken = out.data.token;
    const sel = document.getElementById("banorte-excel-sheet");
    sel.innerHTML = "";
    (out.data.sheets || []).forEach(function (s) {
      const opt = document.createElement("option");
      opt.value = s;
      opt.textContent = s;
      sel.appendChild(opt);
    });
    sel.disabled = false;
    document.getElementById("banorte-excel-preview").disabled = false;
    document.getElementById("banorte-excel-prepare").disabled = false;
  });

  document.getElementById("banorte-excel-preview").addEventListener("click", async function () {
    const sheet = document.getElementById("banorte-excel-sheet").value;
    const out = await excelMultipart("/nomina/exportaciones/banorte/excel/preview", { sheet: sheet, token: excelToken || "" });
    const pre = document.getElementById("banorte-excel-preview-out");
    if (!out.data.ok) { pre.hidden = false; pre.textContent = out.data.code || "preview error"; return; }
    pre.hidden = false;
    pre.textContent = JSON.stringify(out.data.preview, null, 2);
  });

  document.getElementById("banorte-excel-prepare").addEventListener("click", async function () {
    const sheet = document.getElementById("banorte-excel-sheet").value;
    const out = await excelMultipart("/nomina/exportaciones/banorte/excel/prepare", { sheet: sheet, token: excelToken || "" });
    if (!out.data.ok) { alert(out.data.code || "prepare error"); return; }
    showHub();
    renderEditor(out.data.draft);
  });
})();
