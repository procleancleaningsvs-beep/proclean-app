(function () {
  const root = document.getElementById("banorte-root");
  if (!root) return;
  let csrf = root.dataset.csrf || "";
  let draft = null;
  const editorPanel = document.getElementById("banorte-editor-panel");

  /** Serialized mutation queue per draft — ordinary + terminal actions. */
  const mutationQueue = {
    active: false,
    closing: false,
    pendingByRow: Object.create(null),
    amountTimers: Object.create(null),
    pendingTerminal: null,
    latestConfirmedRevision: null,
  };

  const STALE_MSG = "El borrador cambió en otra operación. Se actualizó con la versión más reciente.";

  function setCsrf(token) { if (token) csrf = token; }
  function confirmedRevision() {
    if (mutationQueue.latestConfirmedRevision != null) return mutationQueue.latestConfirmedRevision;
    return draft ? draft.revision : null;
  }
  function noteConfirmedDraft(d) {
    if (!d) return;
    draft = d;
    mutationQueue.latestConfirmedRevision = d.revision;
  }
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

  function clearAmountTimers() {
    Object.keys(mutationQueue.amountTimers).forEach(function (k) {
      clearTimeout(mutationQueue.amountTimers[k]);
    });
    mutationQueue.amountTimers = Object.create(null);
  }

  function clearMutationQueue() {
    clearAmountTimers();
    mutationQueue.pendingByRow = Object.create(null);
    mutationQueue.pendingTerminal = null;
    mutationQueue.active = false;
    mutationQueue.closing = false;
    setBusy(false);
  }

  function enqueueApply(rowId, payload, opts) {
    opts = opts || {};
    if (!draft || mutationQueue.closing) return;
    const key = String(rowId);
    const prev = mutationQueue.pendingByRow[key] || {};
    const next = Object.assign({}, prev, payload, { rowId: Number(rowId), kind: "apply" });
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

  function enqueueTerminal(job) {
    if (!draft) return Promise.resolve({ ok: false });
    return new Promise(function (resolve) {
      mutationQueue.pendingTerminal = Object.assign({}, job, { _resolve: resolve });
      if (job.type === "abandon" || job.type === "generate") {
        mutationQueue.closing = true;
        if (job.type === "abandon") {
          clearAmountTimers();
          mutationQueue.pendingByRow = Object.create(null);
        }
      }
      drainMutationQueue();
    });
  }

  async function handleStale() {
    clearMutationQueue();
    await reloadDraft(STALE_MSG);
  }

  async function runApplyJob(job) {
    const body = {
      expected_revision: confirmedRevision(),
      amount_final: job.amount_final,
    };
    if (job.beneficiary_id != null) body.beneficiary_id = job.beneficiary_id;
    if (job.nombre_recibido != null) body.nombre_recibido = job.nombre_recibido;
    const out = await api(
      "/nomina/exportaciones/banorte/drafts/" + draft.id + "/rows/" + job.rowId + "/apply",
      body
    );
    if (out.res.status === 409 || out.data.code === "draft_stale") {
      await handleStale();
      return false;
    }
    if (!out.data.ok) {
      alert(out.data.message || out.data.code || "No se pudo aplicar el cambio");
      return false;
    }
    noteConfirmedDraft(out.data.draft);
    patchEditorFromDraft(out.data.draft);
    return true;
  }

  async function drainMutationQueue() {
    if (mutationQueue.active || !draft) return;
    mutationQueue.active = true;
    setBusy(true);
    try {
      while (draft) {
        const keys = Object.keys(mutationQueue.pendingByRow);
        if (keys.length && !(mutationQueue.closing && mutationQueue.pendingTerminal && mutationQueue.pendingTerminal.type === "abandon")) {
          keys.sort(function (a, b) {
            const pa = mutationQueue.pendingByRow[a]._priority === "beneficiary" ? 0 : 1;
            const pb = mutationQueue.pendingByRow[b]._priority === "beneficiary" ? 0 : 1;
            return pa - pb;
          });
          const key = keys[0];
          const job = mutationQueue.pendingByRow[key];
          delete mutationQueue.pendingByRow[key];
          const ok = await runApplyJob(job);
          if (!ok) return;
          continue;
        }
        const term = mutationQueue.pendingTerminal;
        if (!term) break;
        mutationQueue.pendingTerminal = null;
        const result = await runTerminalJob(term);
        if (term._resolve) term._resolve(result);
        if (term.type === "abandon" || !result.ok) return;
      }
    } finally {
      mutationQueue.active = false;
      const pending =
        Object.keys(mutationQueue.pendingByRow).length > 0 || !!mutationQueue.pendingTerminal;
      setBusy(pending || mutationQueue.closing);
      if (pending && draft) drainMutationQueue();
    }
  }

  async function runTerminalJob(term) {
    const rev = confirmedRevision();
    if (term.type === "exclude") {
      const out = await api("/nomina/exportaciones/banorte/drafts/" + draft.id + "/exclude-row", {
        expected_revision: rev,
        row_id: term.row_id,
        confirm: true,
      });
      if (out.res.status === 409 || out.data.code === "draft_stale") {
        await handleStale();
        return { ok: false, stale: true };
      }
      if (!out.data.ok) {
        alert(out.data.message || out.data.code || "error");
        return { ok: false };
      }
      noteConfirmedDraft(out.data.draft);
      patchEditorFromDraft(out.data.draft);
      return { ok: true };
    }
    if (term.type === "undo") {
      const out = await api("/nomina/exportaciones/banorte/drafts/" + draft.id + "/undo", {
        expected_revision: rev,
      });
      if (out.res.status === 409 || out.data.code === "draft_stale") {
        await handleStale();
        return { ok: false, stale: true };
      }
      if (!out.data.ok) {
        alert(out.data.message || out.data.code || "error");
        return { ok: false };
      }
      noteConfirmedDraft(out.data.draft);
      patchEditorFromDraft(out.data.draft);
      return { ok: true };
    }
    if (term.type === "save") {
      const out = await api("/nomina/exportaciones/banorte/drafts/" + draft.id + "/save", {
        expected_revision: rev,
        rows: collectRows(),
        consecutive_pref: getConsecutiveValue(),
      });
      if (out.res.status === 409 || out.data.code === "draft_stale") {
        await handleStale();
        return { ok: false, stale: true };
      }
      if (!out.data.ok) {
        alert("Error al guardar: " + (out.data.code || ""));
        return { ok: false };
      }
      noteConfirmedDraft(out.data.draft);
      patchEditorFromDraft(out.data.draft);
      return { ok: true };
    }
    if (term.type === "abandon") {
      const out = await api("/nomina/exportaciones/banorte/drafts/" + draft.id + "/abandon", {
        expected_revision: rev,
        confirm: true,
      });
      if (out.res.status === 409 || out.data.code === "draft_stale") {
        mutationQueue.closing = false;
        await handleStale();
        return { ok: false, stale: true };
      }
      if (!out.data.ok) {
        mutationQueue.closing = false;
        alert(out.data.message || out.data.code || "No se pudo abandonar");
        return { ok: false };
      }
      clearEditorAfterAbandon();
      return { ok: true };
    }
    if (term.type === "generate") {
      return runGenerateQueued(term.flags || {});
    }
    return { ok: false };
  }

  function clearEditorAfterAbandon() {
    clearMutationQueue();
    draft = null;
    mutationQueue.latestConfirmedRevision = null;
    const tbody = document.querySelector("#banorte-editor tbody");
    const xtbody = document.querySelector("#banorte-excluded tbody");
    if (tbody) tbody.innerHTML = "";
    if (xtbody) xtbody.innerHTML = "";
    const wrap = document.getElementById("banorte-excluded-wrap");
    if (wrap) wrap.hidden = true;
    const recon = document.getElementById("banorte-recon");
    if (recon) recon.innerHTML = "";
    const origin = document.getElementById("banorte-origin-box");
    if (origin) origin.textContent = "";
    const viewQ = document.getElementById("banorte-view-q");
    const viewState = document.getElementById("banorte-view-state");
    const viewSort = document.getElementById("banorte-view-sort");
    if (viewQ) viewQ.value = "";
    if (viewState) viewState.value = "all";
    if (viewSort) viewSort.value = "position";
    const panel = document.getElementById("banorte-editor-panel");
    if (panel) panel.hidden = true;
    showHub();
  }

  function patchEditorFromDraft(d) {
    renderEditor(d);
  }

  async function reloadDraft(message) {
    if (!draft) return;
    const id = draft.id;
    const res = await fetch("/nomina/exportaciones/banorte/drafts/" + id);
    const data = await res.json().catch(function () { return {}; });
    setCsrf(data.csrf_token);
    if (data.ok && data.draft) {
      noteConfirmedDraft(data.draft);
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

  function updateRestoreButton(d) {
    const btn = document.getElementById("banorte-restore-last");
    btn.disabled = !d || !d.undo_available || mutationQueue.closing;
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
    noteConfirmedDraft(d);
    mutationQueue.closing = false;
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
        if (!draft || mutationQueue.closing) return;
        if (!confirm("¿Excluir esta fila del archivo .pag?")) return;
        await enqueueTerminal({ type: "exclude", row_id: Number(tr.dataset.rowId) });
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
    if (!draft || mutationQueue.closing) return;
    await enqueueTerminal({ type: "undo" });
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
    if (!draft || mutationQueue.closing) return;
    await enqueueTerminal({ type: "save" });
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

  async function runGenerateQueued(flags) {
    flags = flags || {};
    const out = await api("/nomina/exportaciones/banorte/drafts/" + draft.id + "/generate", {
      expected_revision: confirmedRevision(),
      consecutive: getConsecutiveValue(),
      confirm_manuals: !!flags.confirm_manuals,
      confirm_duplicate_consecutive: !!flags.confirm_duplicate_consecutive,
    });
    const msg = document.getElementById("banorte-export-msg");
    if (out.res.status === 409 || out.data.code === "draft_stale") {
      mutationQueue.closing = false;
      await handleStale();
      return { ok: false, stale: true };
    }
    if (!out.data.ok) {
      mutationQueue.closing = false;
      if (out.data.code === "manual_beneficiaries_confirmation_required") {
        const ok = await showModal("Hay beneficiarios de alta manual. ¿Confirma incluirlos en el archivo .pag?");
        if (ok) {
          mutationQueue.closing = true;
          return runGenerateQueued({ confirm_manuals: true, confirm_duplicate_consecutive: flags.confirm_duplicate_consecutive });
        }
        return { ok: false };
      }
      if (out.data.code === "duplicate_consecutive_confirmation_required") {
        const prior = out.data.prior_export_id ? (" (exportación #" + out.data.prior_export_id + ")") : "";
        const ok = await showModal("El consecutivo ya existe para hoy" + prior + ". ¿Confirma generar de todos modos?");
        if (ok) {
          mutationQueue.closing = true;
          return runGenerateQueued({ confirm_manuals: flags.confirm_manuals, confirm_duplicate_consecutive: true });
        }
        return { ok: false };
      }
      msg.hidden = false;
      msg.textContent = "Bloqueado: " + (out.data.message || out.data.code || out.res.status);
      return { ok: false };
    }
    if (out.data.layout_date_display) {
      document.getElementById("banorte-app-date").textContent = out.data.layout_date_display;
    }
    msg.hidden = false;
    msg.textContent = "Generado " + out.data.filename;
    window.location.href = "/nomina/exportaciones/banorte/historial/" + out.data.export_id + "/download";
    return { ok: true };
  }

  document.getElementById("banorte-generate").addEventListener("click", async function () {
    if (!draft || mutationQueue.closing) return;
    await enqueueTerminal({ type: "generate", flags: {} });
  });

  document.getElementById("banorte-abandon-draft").addEventListener("click", async function () {
    if (!draft || mutationQueue.closing) return;
    if (!confirm("¿Abandonar este borrador? No se generará archivo.")) return;
    await enqueueTerminal({ type: "abandon" });
  });

  let stagingBatch = null;
  let availableAfter = null;

  function syncUseAccountCheckbox() {
    const cb = document.getElementById("batch-use-acct");
    const acct = document.getElementById("batch-cuenta");
    const emp = document.getElementById("batch-emp");
    if (!cb || !acct || !emp) return;
    if (cb.checked) {
      const digits = String(acct.value || "").replace(/\D/g, "");
      if (digits.length !== 10) {
        cb.checked = false;
        emp.readOnly = false;
        alert("La cuenta debe tener exactamente 10 dígitos para usarla como número.");
        return;
      }
      emp.value = digits;
      emp.readOnly = true;
    } else {
      emp.readOnly = false;
    }
  }
  const batchUse = document.getElementById("batch-use-acct");
  const batchCuenta = document.getElementById("batch-cuenta");
  if (batchUse) batchUse.addEventListener("change", syncUseAccountCheckbox);
  if (batchCuenta) batchCuenta.addEventListener("input", function () {
    if (batchUse && batchUse.checked) syncUseAccountCheckbox();
  });

  function renderBatchTable(batch) {
    stagingBatch = batch;
    const tbody = document.querySelector("#banorte-batch-table tbody");
    if (!tbody) return;
    tbody.innerHTML = "";
    (batch.rows || []).forEach(function (r) {
      const tr = document.createElement("tr");
      tr.innerHTML =
        "<td>" + esc(r.nombre) + "</td>" +
        '<td class="banorte-mono">' + esc(r.cuenta) + "</td>" +
        '<td class="banorte-mono">' + esc(r.employee_number) + "</td>" +
        "<td>" + esc(r.row_state) + "</td>" +
        "<td>" + esc(r.error_message || r.comment || "") + "</td>" +
        "<td><button type='button' class='btn btn-secondary btn-sm' data-del='" + r.id + "'>Eliminar</button></td>";
      tbody.appendChild(tr);
    });
    tbody.querySelectorAll("[data-del]").forEach(function (btn) {
      btn.addEventListener("click", async function () {
        if (!stagingBatch) return;
        const out = await api(
          "/nomina/exportaciones/banorte/beneficiarios/batches/" + stagingBatch.id + "/rows/" + btn.getAttribute("data-del") + "/delete",
          { expected_revision: stagingBatch.revision }
        );
        if (out.data.ok) renderBatchTable(out.data.batch);
        else alert(out.data.message || out.data.code || "error");
      });
    });
  }

  async function ensureStagingBatch() {
    if (stagingBatch && stagingBatch.status === "OPEN") return stagingBatch;
    const out = await api("/nomina/exportaciones/banorte/beneficiarios/batches", { origin_kind: "MANUAL" });
    if (!out.data.ok) throw new Error(out.data.code || "batch");
    renderBatchTable(out.data.batch);
    return stagingBatch;
  }

  document.getElementById("banorte-alta-form").addEventListener("submit", async function (e) {
    e.preventDefault();
    try {
      await ensureStagingBatch();
      syncUseAccountCheckbox();
      const out = await api(
        "/nomina/exportaciones/banorte/beneficiarios/batches/" + stagingBatch.id + "/rows",
        {
          expected_revision: stagingBatch.revision,
          nombre: document.getElementById("batch-nombre").value,
          cuenta: document.getElementById("batch-cuenta").value,
          employee_number: document.getElementById("batch-emp").value,
          use_account_as_employee_number: !!(document.getElementById("batch-use-acct") || {}).checked,
        }
      );
      if (!out.data.ok) {
        alert(out.data.message || out.data.code || "No se pudo agregar");
        return;
      }
      renderBatchTable(out.data.batch);
      document.getElementById("batch-nombre").value = "";
      document.getElementById("batch-cuenta").value = "";
      document.getElementById("batch-emp").value = "";
      document.getElementById("batch-use-acct").checked = false;
      document.getElementById("batch-emp").readOnly = false;
    } catch (err) {
      alert("No se pudo preparar el lote");
    }
  });

  const batchConfirm = document.getElementById("banorte-batch-confirm");
  if (batchConfirm) batchConfirm.addEventListener("click", async function () {
    if (!stagingBatch) return;
    if (!confirm("¿Guardar todos los beneficiarios del lote?")) return;
    const out = await api(
      "/nomina/exportaciones/banorte/beneficiarios/batches/" + stagingBatch.id + "/confirm",
      { expected_revision: stagingBatch.revision }
    );
    const msg = document.getElementById("banorte-batch-msg");
    if (!out.data.ok) {
      msg.hidden = false;
      msg.textContent = out.data.message || out.data.code || "Error al confirmar";
      if (out.data.batch) renderBatchTable(out.data.batch);
      return;
    }
    stagingBatch = null;
    msg.hidden = false;
    msg.textContent = "Beneficiarios guardados.";
    showHub();
    loadBenefListing(1);
  });
  const batchAbandon = document.getElementById("banorte-batch-abandon");
  if (batchAbandon) batchAbandon.addEventListener("click", async function () {
    if (!stagingBatch) return;
    if (!confirm("¿Abandonar la lista temporal?")) return;
    await api(
      "/nomina/exportaciones/banorte/beneficiarios/batches/" + stagingBatch.id + "/abandon",
      { expected_revision: stagingBatch.revision, confirm: true }
    );
    stagingBatch = null;
    renderBatchTable({ rows: [], revision: 0, id: 0, status: "ABANDONED" });
  });

  const reporteForm = document.getElementById("banorte-reporte-form");
  if (reporteForm) reporteForm.addEventListener("submit", async function (e) {
    e.preventDefault();
    const fileInput = document.getElementById("banorte-reporte-file");
    if (!fileInput.files || !fileInput.files[0]) return;
    async function send(confirmReimport) {
      const fd = new FormData();
      fd.append("file", fileInput.files[0]);
      fd.append("csrf_token", csrf);
      if (confirmReimport) fd.append("confirm_reimport", "1");
      const res = await fetch("/nomina/exportaciones/banorte/import/reporte/prepare-batch", {
        method: "POST",
        headers: { "X-CSRF-Token": csrf },
        body: fd,
      });
      const data = await res.json().catch(function () { return {}; });
      setCsrf(data.csrf_token);
      return { res: res, data: data };
    }
    let out = await send(false);
    if (out.res.status === 409 && out.data.code === "duplicate_file_confirmation_required") {
      const prior = out.data.prior || {};
      const ok = await showModal(
        (out.data.message || "Este reporte ya fue procesado.") +
        (prior.imported_at ? (" Fecha: " + prior.imported_at + ".") : "") +
        (prior.batch_ref ? (" Ref: " + prior.batch_ref + ".") : "")
      );
      if (!ok) return;
      out = await send(true);
    }
    if (!out.data.ok) {
      alert(out.data.message || out.data.code || "Error");
      return;
    }
    renderBatchTable(out.data.batch);
    const msg = document.getElementById("banorte-batch-msg");
    msg.hidden = false;
    msg.textContent = "Lote de reporte listo para revisar y guardar.";
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

  let benPage = 1;
  let openBenEditId = null;

  function closeBenEditPanel() {
    const existing = document.getElementById("banorte-ben-edit-row");
    if (existing) existing.remove();
    openBenEditId = null;
  }

  async function openBenEdit(ben) {
    const tbody = document.querySelector("#banorte-ben-table tbody");
    if (!tbody) return;
    closeBenEditPanel();
    openBenEditId = ben.id;
    const hostTr = tbody.querySelector('tr[data-ben-id="' + ben.id + '"]');
    if (!hostTr) return;
    const hist = await fetch("/nomina/exportaciones/banorte/beneficiarios/" + ben.id + "/history");
    const histData = await hist.json().catch(function () { return {}; });
    setCsrf(histData.csrf_token);
    const events = (histData.events || []).slice(0, 8);
    const editTr = document.createElement("tr");
    editTr.id = "banorte-ben-edit-row";
    const td = document.createElement("td");
    td.colSpan = 6;
    td.innerHTML =
      '<div class="banorte-ben-edit-panel" data-ben-id="' + ben.id + '">' +
      "<p class=\"banorte-hint\">" + esc(ben.status_explanation || "Edición administrativa") + "</p>" +
      '<label>Nombre <input id="ben-edit-nombre" value="' + esc(ben.nombre_original || "") + '"></label>' +
      '<label>Cuenta <input id="ben-edit-cuenta" class="banorte-mono" value="' + esc(ben.account_number || "") + '"></label>' +
      '<label>Número de empleado <input id="ben-edit-emp" class="banorte-mono" value="' + esc(ben.employee_number_effective || "") + '"></label>' +
      '<label>Motivo / comentario <input id="ben-edit-reason" required placeholder="Obligatorio"></label>' +
      '<div class="banorte-ben-edit-actions">' +
      '<button type="button" class="btn btn-primary btn-sm" data-ben-act="replace">Guardar cambios (versión nueva)</button>' +
      '<button type="button" class="btn btn-secondary btn-sm" data-ben-act="mark_usable_manual">Marcar utilizable</button>' +
      '<button type="button" class="btn btn-secondary btn-sm" data-ben-act="keep_pending">Mantener pendiente</button>' +
      '<button type="button" class="btn btn-secondary btn-sm" data-ben-act="deactivate">Desactivar</button>' +
      '<button type="button" class="btn btn-secondary btn-sm" id="ben-edit-close">Cerrar</button>' +
      "</div>" +
      "<h4 class=\"pc-panel-title\">Historial</h4>" +
      '<ul class="banorte-ben-history">' +
      (events.length
        ? events.map(function (ev) {
            return "<li>" + esc(ev.created_at || "") + " · " + esc(ev.action || "") +
              ": " + esc(ev.reason || "") +
              (ev.created_by ? (" (" + esc(ev.created_by) + ")") : "") +
              "</li>";
          }).join("")
        : "<li>Sin eventos registrados.</li>") +
      "</ul></div>";
    editTr.appendChild(td);
    hostTr.insertAdjacentElement("afterend", editTr);
    document.getElementById("ben-edit-close").addEventListener("click", closeBenEditPanel);
    td.querySelectorAll("[data-ben-act]").forEach(function (btn) {
      btn.addEventListener("click", async function () {
        const action = btn.getAttribute("data-ben-act");
        const reason = (document.getElementById("ben-edit-reason").value || "").trim();
        if (!reason) {
          alert("Indique un motivo.");
          return;
        }
        const payload = { action: action, reason: reason };
        if (action === "replace") {
          payload.nombre = document.getElementById("ben-edit-nombre").value;
          payload.account = document.getElementById("ben-edit-cuenta").value;
          payload.employee_number_effective = document.getElementById("ben-edit-emp").value;
        }
        const out = await api(
          "/nomina/exportaciones/banorte/beneficiarios/" + ben.id + "/actions",
          payload
        );
        if (!out.data.ok) {
          alert(out.data.code || "No se pudo aplicar la acción");
          return;
        }
        closeBenEditPanel();
        loadBenefListing(benPage);
      });
    });
  }

  function bindBenEditButtons(scope) {
    (scope || document).querySelectorAll(".banorte-ben-edit").forEach(function (btn) {
      if (btn.dataset.bound) return;
      btn.dataset.bound = "1";
      btn.addEventListener("click", function () {
        const id = Number(btn.getAttribute("data-ben-id"));
        const tr = btn.closest("tr");
        const cells = tr ? tr.querySelectorAll("td") : [];
        openBenEdit({
          id: id,
          nombre_original: cells[1] ? cells[1].childNodes[0].textContent.trim() : "",
          employee_number_effective: cells[2] ? cells[2].textContent.trim() : "",
          account_number: cells[3] ? cells[3].textContent.trim() : "",
          status_explanation: (tr && tr.querySelector(".banorte-hint") && tr.querySelector(".banorte-hint").textContent) || "",
        });
      });
    });
  }

  function renderBenefRows(listing) {
    const tbody = document.querySelector("#banorte-ben-table tbody");
    if (!tbody) return;
    closeBenEditPanel();
    tbody.innerHTML = "";
    (listing.rows || []).forEach(function (b) {
      const tr = document.createElement("tr");
      tr.setAttribute("data-ben-id", String(b.id));
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
      const note = b.status_explanation || b.last_event_reason || b.banorte_comment || "";
      tr.innerHTML =
        "<td>" + b.id + "</td><td>" + esc(b.nombre_original) +
        (note ? ('<div class="banorte-hint">' + esc(note) + "</div>") : "") +
        "</td>" +
        "<td>" + esc(b.employee_number_effective) + "</td>" +
        '<td class="banorte-mono">' + esc(b.account_number) + "</td><td>" + st + "</td>" +
        '<td><button type="button" class="btn btn-secondary btn-sm banorte-ben-edit" data-ben-id="' +
        b.id + '">Editar</button></td>';
      tbody.appendChild(tr);
    });
    bindBenEditButtons(tbody);
    document.getElementById("banorte-ben-meta").textContent =
      "Mostrando " + (listing.start_index || 0) + "–" + (listing.end_index || 0) +
      " de " + (listing.total || 0) + " · Página " + listing.page + " de " + (listing.total_pages || 1);
    const pager = document.getElementById("banorte-ben-pager");
    if (pager) {
      pager.innerHTML = "";
      function mk(label, page, disabled) {
        const btn = document.createElement("button");
        btn.type = "button";
        btn.className = "btn btn-secondary btn-sm";
        btn.textContent = label;
        btn.disabled = !!disabled;
        btn.addEventListener("click", function () { loadBenefListing(page); });
        pager.appendChild(btn);
      }
      mk("Primera", 1, !listing.has_previous);
      mk("Anterior", Math.max(1, (listing.page || 1) - 1), !listing.has_previous);
      const span = document.createElement("span");
      span.className = "banorte-hint";
      span.textContent = " Página " + listing.page + " de " + (listing.total_pages || 1) + " ";
      pager.appendChild(span);
      mk("Siguiente", (listing.page || 1) + 1, !listing.has_next);
      mk("Última", listing.total_pages || 1, !listing.has_next);
    }
    benPage = listing.page || 1;
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
      sort: (document.getElementById("banorte-ben-sort") || {}).value || "id_desc",
    });
    if (seq !== benSearchSeq) return;
    if (out.data.ok) renderBenefRows(out.data.listing);
  }

  function scheduleBenefSearch() {
    clearTimeout(benSearchTimer);
    benSearchTimer = setTimeout(function () { loadBenefListing(1); }, 300);
  }

  ["banorte-ben-q", "banorte-ben-emp", "banorte-ben-val", "banorte-ben-rec", "banorte-ben-sort"].forEach(function (id) {
    const el = document.getElementById(id);
    if (!el) return;
    el.addEventListener(el.tagName === "SELECT" ? "change" : "input", scheduleBenefSearch);
  });
  bindBenEditButtons(document.getElementById("banorte-ben-table"));

  function empSortKey(text) {
    const digits = String(text || "").replace(/\D/g, "");
    if (!digits) return Number.POSITIVE_INFINITY;
    const n = Number(digits);
    return Number.isFinite(n) ? n : Number.POSITIVE_INFINITY;
  }

  function applyViewFilters() {
    const state = (document.getElementById("banorte-view-state") || {}).value || "all";
    const q = ((document.getElementById("banorte-view-q") || {}).value || "").trim().toLowerCase();
    const sort = (document.getElementById("banorte-view-sort") || {}).value || "position";
    const tbody = document.querySelector("#banorte-editor tbody");
    if (!tbody) return;
    const rows = Array.prototype.slice.call(tbody.querySelectorAll("tr"));
    rows.forEach(function (tr) {
      const rs = tr.dataset.rowState || "";
      const name = (tr.querySelector(".c-name") && tr.querySelector(".c-name").value || "").toLowerCase();
      let ok = true;
      if (state === "OK" && rs !== "OK") ok = false;
      if (state === "NEEDS_REVIEW" && rs !== "NEEDS_REVIEW" && rs !== "BLOCKED") ok = false;
      if (q && name.indexOf(q) < 0) ok = false;
      tr.hidden = !ok;
    });
    if (sort === "position") {
      rows.sort(function (a, b) {
        return Number(a.dataset.position || 0) - Number(b.dataset.position || 0);
      });
    } else if (sort === "name_asc" || sort === "name_desc") {
      rows.sort(function (a, b) {
        const na = (a.querySelector(".c-name") && a.querySelector(".c-name").value || "").toLowerCase();
        const nb = (b.querySelector(".c-name") && b.querySelector(".c-name").value || "").toLowerCase();
        const cmp = na.localeCompare(nb, "es");
        return sort === "name_asc" ? cmp : -cmp;
      });
    } else if (sort === "emp_asc" || sort === "emp_desc") {
      rows.sort(function (a, b) {
        const ea = empSortKey(a.querySelector(".c-emp") && a.querySelector(".c-emp").textContent);
        const eb = empSortKey(b.querySelector(".c-emp") && b.querySelector(".c-emp").textContent);
        if (ea !== eb) return sort === "emp_asc" ? ea - eb : eb - ea;
        return Number(a.dataset.rowId || 0) - Number(b.dataset.rowId || 0);
      });
    } else if (sort === "amount_asc" || sort === "amount_desc") {
      rows.sort(function (a, b) {
        const aa = Number((a.querySelector(".c-final") && a.querySelector(".c-final").value) || 0);
        const ab = Number((b.querySelector(".c-final") && b.querySelector(".c-final").value) || 0);
        return sort === "amount_asc" ? aa - ab : ab - aa;
      });
    }
    rows.forEach(function (tr) { tbody.appendChild(tr); });
  }
  const viewState = document.getElementById("banorte-view-state");
  const viewQ = document.getElementById("banorte-view-q");
  const viewSort = document.getElementById("banorte-view-sort");
  if (viewState) viewState.addEventListener("change", applyViewFilters);
  if (viewQ) viewQ.addEventListener("input", applyViewFilters);
  if (viewSort) viewSort.addEventListener("change", applyViewFilters);

  async function loadAvailableNumbers(append) {
    const box = document.getElementById("banorte-available-emps");
    if (!box) return;
    const out = await api("/nomina/exportaciones/banorte/beneficiarios/available-employee-numbers", {
      limit: 20,
      after: append ? availableAfter : null,
    });
    if (!out.data.ok) { box.textContent = "No disponibles"; return; }
    const nums = out.data.numbers || [];
    if (!append) box.innerHTML = "";
    nums.forEach(function (n) {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "banorte-available-num";
      btn.textContent = n;
      btn.addEventListener("click", function () {
        const emp = document.getElementById("batch-emp");
        const use = document.getElementById("batch-use-acct");
        if (use) { use.checked = false; }
        if (emp) { emp.readOnly = false; emp.value = n; }
        box.querySelectorAll(".banorte-available-num").forEach(function (el) {
          el.classList.toggle("is-selected", el === btn);
        });
      });
      box.appendChild(btn);
      availableAfter = n;
    });
  }
  const altaTabBtn = document.querySelector('[data-banorte-tab="agregar-benef"]');
  if (altaTabBtn) altaTabBtn.addEventListener("click", function () {
    availableAfter = null;
    loadAvailableNumbers(false);
    ensureStagingBatch().catch(function () {});
  });
  const moreBtn = document.getElementById("banorte-available-more");
  if (moreBtn) moreBtn.addEventListener("click", function () { loadAvailableNumbers(true); });

  let excelToken = null;
  async function excelMultipart(url, extra) {
    const fileInput = document.getElementById("banorte-excel-file");
    if (!fileInput.files || !fileInput.files[0]) {
      return { res: { status: 400 }, data: { ok: false, success: false, code: "file_required", message: "Seleccione un archivo." } };
    }
    const fd = new FormData();
    fd.append("file", fileInput.files[0]);
    fd.append("csrf_token", csrf);
    Object.keys(extra || {}).forEach(function (k) { fd.append(k, extra[k]); });
    const res = await fetch(url, { method: "POST", headers: { "X-CSRF-Token": csrf }, body: fd });
    const data = await res.json().catch(function () { return {}; });
    setCsrf(data.csrf_token);
    return { res: res, data: data };
  }

  function excelOk(data) {
    return data && (data.success === true || data.ok === true);
  }

  function formatExcelPreview(prev) {
    if (!prev) return "Sin vista previa.";
    const lines = [];
    lines.push("Hoja: " + (prev.sheet || "—"));
    lines.push("Pagos Banorte: " + (prev.banorte_count || 0));
    lines.push("Total Banorte: $" + money(prev.total_banorte_cents || 0));
    if (prev.excluded_other_bank_count) lines.push("Otros bancos omitidos: " + prev.excluded_other_bank_count);
    if (prev.excluded_hidden_count) lines.push("Filas ocultas omitidas: " + prev.excluded_hidden_count);
    if (prev.blocked_formula_count) lines.push("Fórmulas sin valor: " + prev.blocked_formula_count);
    if (prev.warnings && prev.warnings.length) lines.push("Avisos: " + prev.warnings.join(", "));
    return lines.join("\n");
  }

  document.getElementById("banorte-excel-inspect").addEventListener("click", async function () {
    const btn = this;
    if (btn.disabled) return;
    btn.disabled = true;
    try {
      const out = await excelMultipart("/nomina/exportaciones/banorte/excel/inspect");
      if (!excelOk(out.data)) {
        alert(out.data.message || out.data.code || "No se pudo inspeccionar el archivo");
        return;
      }
      const payload = out.data.data || out.data;
      excelToken = payload.token || out.data.token;
      const sheets = payload.sheets || out.data.sheets || [];
      const sel = document.getElementById("banorte-excel-sheet");
      sel.innerHTML = "";
      sheets.forEach(function (s) {
        const opt = document.createElement("option");
        opt.value = s;
        opt.textContent = s;
        sel.appendChild(opt);
      });
      sel.disabled = false;
      document.getElementById("banorte-excel-preview").disabled = false;
      document.getElementById("banorte-excel-prepare").disabled = false;
    } finally {
      btn.disabled = false;
    }
  });

  document.getElementById("banorte-excel-preview").addEventListener("click", async function () {
    const btn = this;
    if (btn.disabled) return;
    btn.disabled = true;
    try {
      const sheet = document.getElementById("banorte-excel-sheet").value;
      const out = await excelMultipart("/nomina/exportaciones/banorte/excel/preview", { sheet: sheet, token: excelToken || "" });
      const pre = document.getElementById("banorte-excel-preview-out");
      pre.hidden = false;
      if (!excelOk(out.data)) {
        pre.textContent = out.data.message || out.data.code || "Error en vista previa";
        return;
      }
      const prev = (out.data.data && out.data.data.preview) || out.data.preview;
      pre.textContent = (out.data.message ? out.data.message + "\n\n" : "") + formatExcelPreview(prev);
    } finally {
      btn.disabled = false;
    }
  });

  document.getElementById("banorte-excel-prepare").addEventListener("click", async function () {
    const btn = this;
    if (btn.disabled) return;
    btn.disabled = true;
    try {
      const sheet = document.getElementById("banorte-excel-sheet").value;
      const out = await excelMultipart("/nomina/exportaciones/banorte/excel/prepare", { sheet: sheet, token: excelToken || "" });
      if (!excelOk(out.data)) {
        alert(out.data.message || out.data.code || "No se pudo preparar el borrador");
        return;
      }
      const draft = (out.data.data && out.data.data.draft) || out.data.draft;
      const amountErrors = (out.data.data && out.data.data.amount_errors) || out.data.amount_errors || [];
      const omitted = (out.data.data && out.data.data.omitted) || out.data.omitted || [];
      showHub();
      renderEditor(draft);
      if (amountErrors.length || omitted.length) {
        const msg = document.getElementById("banorte-export-msg");
        msg.hidden = false;
        const bits = [];
        omitted.forEach(function (o) { bits.push((o.causa || "omitido") + ": " + o.count); });
        amountErrors.forEach(function (e) {
          bits.push((e.causa || "error") + " fila " + (e.excel_row || "?") + " (" + (e.nombre || "") + ")");
        });
        msg.textContent = "Resumen Excel — " + bits.join("; ");
      }
    } finally {
      btn.disabled = false;
    }
  });
})();
