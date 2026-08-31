(function () {
  const root = document.getElementById("banorte-root");
  if (!root) return;
  let csrf = root.dataset.csrf || "";
  const canOperate = root.dataset.canOperate === "1";
  let draft = null;
  const editorPanel = document.getElementById("banorte-editor-panel");

  /** Serialized mutation queue per draft — ordinary + terminal actions. */
  const mutationQueue = {
    active: false,
    closing: false,
    pendingByRow: Object.create(null),
    amountTimers: Object.create(null),
    pendingOrdinary: [],
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
    mutationQueue.pendingOrdinary = [];
    mutationQueue.pendingTerminal = null;
    mutationQueue.active = false;
    mutationQueue.closing = false;
    setBusy(false);
  }

  function enqueueOrdinary(job) {
    if (!draft || mutationQueue.closing) return Promise.resolve({ ok: false });
    return new Promise(function (resolve) {
      mutationQueue.pendingOrdinary.push(Object.assign({}, job, { _resolve: resolve }));
      drainMutationQueue();
    });
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
          mutationQueue.pendingOrdinary = [];
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
        if (mutationQueue.pendingOrdinary.length) {
          const ordinary = mutationQueue.pendingOrdinary.shift();
          const result = await runOrdinaryJob(ordinary);
          if (ordinary._resolve) ordinary._resolve(result);
          if (!result.ok) return;
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
        Object.keys(mutationQueue.pendingByRow).length > 0 ||
        mutationQueue.pendingOrdinary.length > 0 ||
        !!mutationQueue.pendingTerminal;
      setBusy(pending || mutationQueue.closing);
      if (pending && draft) drainMutationQueue();
    }
  }

  async function runOrdinaryJob(job) {
    if (job.type === "add_payment") {
      return runAddPaymentJob(job);
    }
    if (job.type === "exclude" || job.type === "undo" || job.type === "save") {
      return runTerminalJob(job);
    }
    return { ok: false };
  }

  async function runAddPaymentJob(job) {
    const rev = confirmedRevision();
    const body = {
      expected_revision: rev,
      beneficiary_id: job.beneficiary_id,
      amount_final: job.amount_final,
      request_nonce: job.request_nonce,
      confirm_duplicate_beneficiary: !!job.confirm_duplicate_beneficiary,
    };
    const out = await api(
      "/nomina/exportaciones/banorte/drafts/" + draft.id + "/add-payment",
      body
    );
    if (out.res.status === 409 || out.data.code === "draft_stale") {
      if (out.data.code === "duplicate_beneficiary_payment_confirmation_required") {
        const ok = await showModal(out.data.message || "¿Agregar otro pago para esta persona?");
        if (!ok) return { ok: false };
        return runAddPaymentJob(Object.assign({}, job, { confirm_duplicate_beneficiary: true }));
      }
      await handleStale();
      return { ok: false, stale: true };
    }
    if (!out.data.ok) {
      const msg = document.getElementById("banorte-add-pay-msg");
      if (msg) {
        msg.hidden = false;
        msg.textContent = out.data.message || "No se pudo agregar el pago.";
      } else {
        alert(out.data.message || out.data.code || "error");
      }
      return { ok: false };
    }
    const msgOk = document.getElementById("banorte-add-pay-msg");
    if (msgOk) { msgOk.hidden = true; msgOk.textContent = ""; }
    const amt = document.getElementById("banorte-add-pay-amount");
    if (amt) amt.value = "";
    const hid = document.getElementById("banorte-add-pay-ben");
    const q = document.getElementById("banorte-add-pay-q");
    if (hid) hid.value = "";
    if (q) q.value = "";
    noteConfirmedDraft(out.data.draft);
    patchEditorFromDraft(out.data.draft);
    return { ok: true };
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
      const exportMsg = document.getElementById("banorte-export-msg");
      if (exportMsg) { exportMsg.hidden = true; exportMsg.textContent = ""; }
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
      if (out.data.undone_action === "ADD_ROW") {
        const banner = document.getElementById("banorte-add-pay-msg");
        if (banner) {
          banner.hidden = false;
          banner.textContent = out.data.message || "Pago agregado deshecho";
        }
      }
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
    const origCount = rec.original_count != null ? rec.original_count : rec.original_row_count;
    const origTotal = rec.original_total_cents != null ? rec.original_total_cents : rec.total_original_cents;
    const manualCount = rec.manual_added_count != null ? rec.manual_added_count : 0;
    const manualTotal = rec.manual_added_total_cents != null ? rec.manual_added_total_cents : 0;
    const includedTotal = rec.included_total_cents != null ? rec.included_total_cents : rec.total_final_cents;
    el.innerHTML =
      "<div><span>Originales</span><strong>" + origCount + "</strong></div>" +
      "<div><span>Total original</span><strong>$" + money(origTotal) + "</strong></div>" +
      "<div><span>Agregados</span><strong>" + manualCount + "</strong></div>" +
      "<div><span>Total agregados</span><strong>$" + money(manualTotal) + "</strong></div>" +
      "<div><span>Incluidas</span><strong>" + rec.included_count + "</strong></div>" +
      "<div><span>Total incluido</span><strong>$" + money(includedTotal) + "</strong></div>" +
      "<div><span>Excluidas</span><strong>" + rec.excluded_count + "</strong></div>" +
      "<div><span>Total final</span><strong>$" + money(rec.total_final_cents) + "</strong></div>";
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
    refreshAddPayBeneficiaries();
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
        await enqueueOrdinary({ type: "exclude", row_id: Number(tr.dataset.rowId) });
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
    await enqueueOrdinary({ type: "undo" });
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
    await enqueueOrdinary({ type: "save" });
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
    const generatedSave = document.getElementById("banorte-generated-save");
    const generatedSaveWrap = document.getElementById("banorte-generated-save-wrap");
    if (generatedSaveWrap) generatedSaveWrap.hidden = true;
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
    msg.textContent = "Generado " + out.data.filename + ". Use Guardar como para elegir la ubicación.";
    const rawUrl = "/nomina/exportaciones/banorte/historial/" + out.data.export_id + "/download";
    if (generatedSave) {
      generatedSave.href = rawUrl;
      generatedSave.dataset.exportId = String(out.data.export_id);
      generatedSave.dataset.filename = out.data.filename;
      generatedSave.dataset.sha256 = out.data.sha256 || "";
      generatedSave.textContent = "Guardar " + out.data.filename;
      if (generatedSaveWrap) generatedSaveWrap.hidden = false;
      if (window.BanortePagSave && typeof window.BanortePagSave.bindSaveTriggers === "function") {
        window.BanortePagSave.bindSaveTriggers(generatedSaveWrap || document);
      }
    }
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

  let manualBatchContext = null;
  let reportBatch = null;
  let availableAfter = null;
  let availableNumbers = [];
  let beneficiaryGrid = null;
  let historyGeneration = 1;
  let historyLoadedPages = new Set([1]);
  let historyHasNext = false;
  let historyPageRequestActive = false;
  let historyResetPending = false;
  let historyInitiallyPositioned = false;

  function recentStatusMarkup(beneficiary) {
    if (beneficiary.provenance_category === "A") {
      return '<span class="banorte-status banorte-status--ok">TXT activo</span>';
    }
    if (beneficiary.validation_status === "IMPORTADO_EXITOSO" && beneficiary.record_status === "ACTIVO") {
      return '<span class="banorte-status banorte-status--ok">Alta posterior · Validada</span>';
    }
    return '<span class="banorte-status banorte-status--warn">Alta posterior · Pendiente de validación</span>';
  }

  function scrollHistoryToLatest(viewport) {
    viewport.scrollTop = viewport.scrollHeight;
    historyInitiallyPositioned = true;
  }

  function renderHistoryRows(rows, prepend) {
    const viewport = document.getElementById("banorte-beneficiary-history-viewport");
    if (!viewport) return;
    const previousScrollHeight = viewport.scrollHeight;
    const previousScrollTop = viewport.scrollTop;
    if (!prepend) viewport.innerHTML = "";
    const known = new Set(Array.from(viewport.querySelectorAll("[data-history-ben-id]")).map(function (el) {
      return el.getAttribute("data-history-ben-id");
    }));
    const fragment = document.createDocumentFragment();
    (rows || []).slice().reverse().forEach(function (beneficiary) {
      const id = String(beneficiary.id);
      if (known.has(id)) return;
      known.add(id);
      const row = document.createElement("div");
      row.className = "banorte-beneficiary-history-row";
      row.setAttribute("role", "row");
      row.setAttribute("data-history-ben-id", id);
      row.setAttribute("data-provenance-category", String(beneficiary.provenance_category || ""));
      row.innerHTML =
        '<span class="banorte-mono" role="cell">' + esc(beneficiary.display_employee_number || beneficiary.employee_number_effective) + "</span>" +
        '<span role="cell">' + esc(beneficiary.display_name || beneficiary.nombre_original) +
        recentStatusMarkup(beneficiary) + "</span>" +
        '<span class="banorte-mono" role="cell">' + esc(beneficiary.display_account_number || beneficiary.account_number) + "</span>";
      fragment.appendChild(row);
    });
    if (prepend) viewport.insertBefore(fragment, viewport.firstChild);
    else viewport.appendChild(fragment);
    if (!viewport.querySelector("[data-history-ben-id]")) {
      viewport.innerHTML = '<p class="banorte-empty">No hay beneficiarios vigentes para mostrar.</p>';
    }
    if (prepend) {
      viewport.scrollTop = previousScrollTop + (viewport.scrollHeight - previousScrollHeight);
    } else {
      scrollHistoryToLatest(viewport);
    }
  }

  async function loadHistoryPage(page, generation, reset) {
    if (historyPageRequestActive) {
      if (reset) historyResetPending = true;
      return;
    }
    if (!reset && historyLoadedPages.has(page)) return;
    historyPageRequestActive = true;
    let out;
    try {
      out = await api("/nomina/exportaciones/banorte/beneficiarios/search", {
        scope: "current",
        page: page,
        q_name: "",
        q_emp: "",
        validation_status: "",
        record_status: "",
        sort: "id_desc",
      });
    } catch (error) {
      out = { data: { ok: false } };
    } finally {
      historyPageRequestActive = false;
    }
    if (historyResetPending) {
      historyResetPending = false;
      loadHistoryPage(1, historyGeneration, true);
    }
    if (generation !== historyGeneration || !out.data.ok) return;
    if (reset) historyLoadedPages = new Set();
    historyLoadedPages.add(page);
    historyHasNext = !!out.data.listing.has_next;
    renderHistoryRows(out.data.listing.rows || [], !reset);
    const viewport = document.getElementById("banorte-beneficiary-history-viewport");
    if (viewport) {
      viewport.dataset.nextPage = String(page + 1);
      viewport.dataset.hasNext = historyHasNext ? "1" : "0";
    }
  }

  async function resetHistory() {
    historyGeneration += 1;
    historyLoadedPages = new Set();
    historyHasNext = true;
    await loadHistoryPage(1, historyGeneration, true);
  }

  const historyViewport = document.getElementById("banorte-beneficiary-history-viewport");
  if (historyViewport) {
    historyHasNext = historyViewport.dataset.hasNext === "1";
    historyViewport.addEventListener("scroll", function () {
      if (!historyHasNext || historyPageRequestActive) return;
      if (historyViewport.scrollTop > 70) return;
      const page = Number(historyViewport.dataset.nextPage || 2);
      loadHistoryPage(page, historyGeneration, false);
    });
  }

  function batchEffectiveEmployee(r) {
    if (parseInt(r.use_account_as_employee_number || 0, 10) === 1) {
      return r.cuenta || r.employee_number || "";
    }
    return r.employee_number || "";
  }

  function renderReportBatch(batch) {
    reportBatch = batch;
    const wrap = document.getElementById("banorte-report-batch");
    const tbody = document.querySelector("#banorte-report-batch-table tbody");
    if (!tbody) return;
    if (wrap) wrap.hidden = !batch;
    tbody.innerHTML = "";
    ((batch && batch.rows) || []).forEach(function (r) {
      const tr = document.createElement("tr");
      tr.innerHTML =
        "<td>" + esc(r.nombre) + "</td>" +
        '<td class="banorte-mono">' + esc(r.cuenta) + "</td>" +
        '<td class="banorte-mono">' + esc(batchEffectiveEmployee(r)) + "</td>" +
        "<td>" + esc(r.row_state === "ERROR" ? "Error" : "Pendiente") + "</td>" +
        "<td>" + esc(r.error_message || r.comment || "") + "</td>";
      tbody.appendChild(tr);
    });
    if (batch && !(batch.rows || []).length) {
      tbody.innerHTML = '<tr><td colspan="5" class="banorte-empty">El lote no contiene filas.</td></tr>';
    }
  }

  function showBeneficiaryMessage(text, isError) {
    const msg = document.getElementById("banorte-beneficiary-msg");
    if (!msg) return;
    msg.hidden = false;
    msg.textContent = text;
    msg.classList.toggle("banorte-warn", !!isError);
  }

  const beneficiaryWorkspace = document.getElementById("banorte-beneficiary-workspace");
  if (beneficiaryWorkspace && window.BanorteBeneficiaryGrid) {
    beneficiaryGrid = window.BanorteBeneficiaryGrid.mount({
      root: beneficiaryWorkspace,
      onChange: function () { renderAvailableNumbers(); },
      onError: function (code) {
        showBeneficiaryMessage(
          code === "paste_too_many_columns"
            ? "El pegado contiene más de tres columnas; no se aplicó ningún dato."
            : "No se pudo aplicar el pegado.",
          true
        );
      },
    });
    fetch("/nomina/exportaciones/banorte/beneficiarios/batches/open", {
      method: "GET",
      headers: { "Accept": "application/json", "X-Requested-With": "XMLHttpRequest" },
      credentials: "same-origin",
    }).then(function (response) { return response.json(); }).then(function (data) {
      if (!data.ok || !data.batch) return;
      manualBatchContext = { id: data.batch.id, expected_revision: data.batch.revision };
      beneficiaryGrid.hydrate((data.batch.rows || []).map(function (row) {
        return {
          client_row_key: "batch-" + row.id,
          employee_number: row.employee_number,
          nombre: row.nombre,
          account: row.cuenta,
          use_account_as_employee_number: !!row.use_account_as_employee_number,
        };
      }));
      showBeneficiaryMessage("Se recuperó la lista manual pendiente de esta cuenta.", false);
    });
  }

  const beneficiarySave = document.getElementById("banorte-beneficiary-save");
  if (beneficiarySave) beneficiarySave.addEventListener("click", async function () {
    if (!beneficiaryGrid) return;
    if (beneficiaryGrid.entryHasAnyValue()) {
      showBeneficiaryMessage("Añade primero el beneficiario activo a la lista antes de guardar.", true);
      return;
    }
    const rows = beneficiaryGrid.getPendingPayload();
    if (!rows.length) {
      showBeneficiaryMessage("Añada al menos un beneficiario a la lista.", true);
      return;
    }
    if (!confirm("¿Guardar todos los beneficiarios del lote?")) return;
    const payload = { rows: rows };
    if (manualBatchContext) payload.batch_context = manualBatchContext;
    const out = await api("/nomina/exportaciones/banorte/beneficiarios/manual-save", payload);
    if (!out.data.ok) {
      beneficiaryGrid.setErrors(out.data.errors || []);
      showBeneficiaryMessage(out.data.message || out.data.code || "Error al guardar.", true);
      return;
    }
    beneficiaryGrid.clear();
    manualBatchContext = null;
    showBeneficiaryMessage("Beneficiarios guardados.", false);
    availableAfter = null;
    availableNumbers = [];
    await Promise.all([resetHistory(), loadAvailableNumbers(false)]);
  });

  const reportConfirm = document.getElementById("banorte-report-batch-confirm");
  if (reportConfirm) reportConfirm.addEventListener("click", async function () {
    if (!reportBatch || !confirm("¿Guardar el lote importado desde el reporte?")) return;
    const out = await api(
      "/nomina/exportaciones/banorte/beneficiarios/batches/" + reportBatch.id + "/confirm",
      { expected_revision: reportBatch.revision }
    );
    const msg = document.getElementById("banorte-report-batch-msg");
    if (!out.data.ok) {
      msg.hidden = false;
      msg.textContent = out.data.message || out.data.code || "Error al confirmar el reporte.";
      if (out.data.batch) renderReportBatch(out.data.batch);
      return;
    }
    renderReportBatch(null);
    await resetHistory();
  });
  const reportAbandon = document.getElementById("banorte-report-batch-abandon");
  if (reportAbandon) reportAbandon.addEventListener("click", async function () {
    if (!reportBatch || !confirm("¿Abandonar el lote del reporte?")) return;
    await api(
      "/nomina/exportaciones/banorte/beneficiarios/batches/" + reportBatch.id + "/abandon",
      { expected_revision: reportBatch.revision, confirm: true }
    );
    renderReportBatch(null);
  });

  const altasForm = document.getElementById("banorte-import-altas-form");
  if (altasForm) altasForm.addEventListener("submit", async function (e) {
    e.preventDefault();
    const fileInput = document.getElementById("banorte-altas-file");
    if (!fileInput.files || !fileInput.files[0]) return;
    const msg = document.getElementById("banorte-altas-msg");
    async function send(confirmReimport) {
      const fd = new FormData();
      fd.append("file", fileInput.files[0]);
      fd.append("csrf_token", csrf);
      if (confirmReimport) fd.append("confirm_reimport", "1");
      const res = await fetch("/nomina/exportaciones/banorte/import/altas", {
        method: "POST",
        headers: { "X-CSRF-Token": csrf, "Accept": "application/json", "X-Requested-With": "XMLHttpRequest" },
        body: fd,
      });
      const data = await res.json().catch(function () { return {}; });
      setCsrf(data.csrf_token);
      return { res: res, data: data };
    }
    let out = await send(false);
    if (out.res.status === 409 && out.data.code === "duplicate_file_confirmation_required") {
      const ok = await showModal(
        out.data.message || "Este archivo de base ya fue procesado anteriormente. ¿Deseas importarlo de nuevo?"
      );
      if (!ok) return;
      out = await send(true);
    }
    if (!out.data.ok) {
      if (msg) { msg.hidden = false; msg.textContent = out.data.message || out.data.code || "Error"; }
      else alert(out.data.message || out.data.code || "Error");
      return;
    }
    if (msg) { msg.hidden = false; msg.textContent = out.data.message || "Importación ALTAS OK."; }
    showHub();
    loadBenefListing(1);
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
    renderReportBatch(out.data.batch);
    const msg = document.getElementById("banorte-report-batch-msg");
    msg.hidden = false;
    msg.textContent = "Lote de reporte listo para revisar y guardar.";
  });

  document.getElementById("manual-prepare").addEventListener("click", async function () {
    const choice = document.getElementById("manual-choice");
    const payload = { force_new: false };
    if (window.banortePaymentGrid && typeof window.banortePaymentGrid.getRowsPayload === "function") {
      payload.rows = window.banortePaymentGrid.getRowsPayload();
    } else {
      payload.names = document.getElementById("manual-names").value;
      payload.amounts = document.getElementById("manual-amounts").value;
    }
    const out = await api("/nomina/exportaciones/banorte/drafts/manual", payload);
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
        const againPayload = { force_new: false };
        if (window.banortePaymentGrid && typeof window.banortePaymentGrid.getRowsPayload === "function") {
          againPayload.rows = window.banortePaymentGrid.getRowsPayload();
        } else {
          againPayload.names = document.getElementById("manual-names").value;
          againPayload.amounts = document.getElementById("manual-amounts").value;
        }
        const again = await api("/nomina/exportaciones/banorte/drafts/manual", againPayload);
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

  const beneficiaryViews = {
    current: {
      scope: "current",
      prefix: "banorte-ben",
      page: 1,
      searchSeq: 0,
      searchTimer: null,
      emptyMessage: "No hay beneficiarios vigentes para mostrar.",
    },
    historical: {
      scope: "historical",
      prefix: "banorte-hist",
      page: 1,
      searchSeq: 0,
      searchTimer: null,
      emptyMessage: "No hay datos históricos anteriores.",
    },
  };
  let openBenEditId = null;

  function closeBenEditPanel() {
    const existing = document.getElementById("banorte-ben-edit-row");
    if (existing) existing.remove();
    openBenEditId = null;
  }

  async function openBenEdit(ben) {
    const view = beneficiaryViews[ben.scope] || beneficiaryViews.current;
    const tbody = document.querySelector("#" + view.prefix + "-table tbody");
    if (!tbody) return;
    closeBenEditPanel();
    openBenEditId = ben.id;
    const hostTr = tbody.querySelector('tr[data-ben-id="' + ben.id + '"]');
    if (!hostTr) return;
    const hist = await fetch("/nomina/exportaciones/banorte/beneficiarios/" + ben.id + "/history");
    const histData = await hist.json().catch(function () { return {}; });
    setCsrf(histData.csrf_token);
    if (!hist.ok || !histData.ok) {
      alert(histData.message || "No se pudo cargar el beneficiario.");
      return;
    }
    const fresh = histData.beneficiary || {};
    const provenance = histData.provenance || {};
    const policy = histData.action_policy || { allowed_actions: [], identity_fields_read_only: true };
    const allowed = canOperate ? (policy.allowed_actions || []) : [];
    const events = (histData.events || []).slice(0, 8);
    const chain = histData.chain || [];
    const editTr = document.createElement("tr");
    editTr.id = "banorte-ben-edit-row";
    const td = document.createElement("td");
    td.colSpan = 6;
    const readOnly = policy.identity_fields_read_only ? " readonly" : "";
    const actionLabels = {
      replace: "Guardar cambios (versión nueva)",
      mark_usable_manual: "Marcar utilizable",
      keep_pending: "Mantener pendiente",
      deactivate: "Desactivar",
    };
    const actionButtons = allowed.map(function (action) {
      const primary = action === "replace" ? "btn-primary" : "btn-secondary";
      return '<button type="button" class="btn ' + primary +
        ' btn-sm" data-ben-act="' + esc(action) + '">' +
        esc(actionLabels[action] || action) + "</button>";
    }).join("");
    let provenanceMeta = esc(provenance.provenance_label || "Procedencia no disponible");
    if (provenance.catalog_scope === "ACTIVE") {
      provenanceMeta += " · TXT #" + esc(provenance.active_catalog_version_id || "—") +
        " · corte " + esc(provenance.active_catalog_report_date || "—") +
        " · conciliación " + esc(provenance.catalog_reconciliation_status || "—") +
        " · " + (provenance.reconciliation_fresh === false ? "drift detectado" : "conciliación vigente");
    } else if (provenance.post_snapshot) {
      provenanceMeta += " · posterior al corte " + esc(provenance.active_catalog_report_date || "—");
    }
    td.innerHTML =
      '<div class="banorte-ben-edit-panel" data-ben-id="' + ben.id + '">' +
      '<p class="banorte-hint">' + provenanceMeta + "</p>" +
      '<p class="banorte-hint">Fuente ' + esc(provenance.source_kind || "—") +
      " · validación " + esc(provenance.validation_status || "—") +
      " · ciclo " + esc(provenance.record_status || "—") + "</p>" +
      '<label>Nombre <input id="ben-edit-nombre" value="' + esc(fresh.display_name || "") + '"' + readOnly + "></label>" +
      '<label>Cuenta <input id="ben-edit-cuenta" class="banorte-mono" value="' + esc(fresh.display_account_number || "") + '"' + readOnly + "></label>" +
      '<label>Número de empleado <input id="ben-edit-emp" class="banorte-mono" value="' + esc(fresh.display_employee_number || "") + '"' + readOnly + "></label>" +
      (allowed.length
        ? '<label>Motivo / comentario <input id="ben-edit-reason" required placeholder="Obligatorio"></label>'
        : '<p class="banorte-hint">Registro disponible únicamente para consulta.</p>') +
      '<div class="banorte-ben-edit-actions">' +
      actionButtons +
      '<button type="button" class="btn btn-secondary btn-sm" id="ben-edit-close">Cerrar</button>' +
      "</div>" +
      "<h4 class=\"pc-panel-title\">Cadena de versiones</h4>" +
      '<ul class="banorte-ben-history">' +
      (chain.length
        ? chain.map(function (version) {
            return "<li>#" + esc(version.id || "") + " · " +
              esc(version.record_status || "") + " · " +
              esc(version.nombre_original || "") +
              (version.replace_reason ? (": " + esc(version.replace_reason)) : "") +
              "</li>";
          }).join("")
        : "<li>Sin cadena de reemplazo.</li>") +
      "</ul>" +
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
        const reasonInput = document.getElementById("ben-edit-reason");
        const reason = ((reasonInput && reasonInput.value) || "").trim();
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
          alert(out.data.message || "No se pudo aplicar la acción");
          if (out.res.status === 409) {
            closeBenEditPanel();
            loadBenefListing(view.page, view.scope);
          }
          return;
        }
        if (out.data.message) {
          /* soft confirm */
        }
        closeBenEditPanel();
        loadBenefListing(view.page, view.scope);
      });
    });
  }

  function bindBenDetailButtons(scope) {
    (scope || document).querySelectorAll(".banorte-ben-edit, .banorte-hist-view").forEach(function (btn) {
      if (btn.dataset.bound) return;
      btn.dataset.bound = "1";
      btn.addEventListener("click", function () {
        const id = Number(btn.getAttribute("data-ben-id"));
        openBenEdit({
          id: id,
          scope: btn.getAttribute("data-ben-scope") || "current",
        });
      });
    });
  }

  function renderBenefRows(listing, scope) {
    const view = beneficiaryViews[scope] || beneficiaryViews.current;
    const tbody = document.querySelector("#" + view.prefix + "-table tbody");
    if (!tbody) return;
    closeBenEditPanel();
    tbody.innerHTML = "";
    (listing.rows || []).forEach(function (b, index) {
      const tr = document.createElement("tr");
      const visibleId = (listing.start_index || 0) + index;
      tr.setAttribute("data-ben-id", String(b.id));
      if (view.scope === "current") tr.setAttribute("data-visible-id", String(visibleId));
      tr.setAttribute("data-ben-scope", view.scope);
      tr.setAttribute("data-record-status", String(b.record_status || ""));
      let st = '<span class="banorte-status banorte-status--warn">Pendiente</span>';
      if (view.scope === "historical" && b.provenance_category === "C") {
        st = '<span class="banorte-status banorte-status--muted">Legacy</span>';
      } else if (view.scope === "historical") {
        st = '<span class="banorte-status banorte-status--muted">Reemplazado / inactivo</span>';
      } else if (b.validation_status === "IMPORTADO_EXITOSO" && b.record_status === "ACTIVO") {
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
      const provenance = b.provenance_label || "Procedencia no disponible";
      const buttonClass = view.scope === "historical" ? "banorte-hist-view" : "banorte-ben-edit";
      const buttonLabel = view.scope === "historical" ? "Ver detalle" : "Editar";
      const editButton = canOperate
        ? '<button type="button" class="btn btn-secondary btn-sm ' + buttonClass +
          '" data-ben-id="' + b.id + '" data-ben-scope="' + view.scope + '">' +
          buttonLabel + "</button>"
        : "";
      const idLabel = view.scope === "historical" ? ("#" + esc(b.id)) : String(visibleId);
      tr.innerHTML =
        "<td>" + idLabel + "</td><td>" + esc(b.display_name || b.nombre_original) +
        '<div class="banorte-hint">' + esc(provenance) + "</div>" +
        (note ? ('<div class="banorte-hint">' + esc(note) + "</div>") : "") +
        "</td>" +
        "<td>" + esc(b.display_employee_number || b.employee_number_effective) + "</td>" +
        '<td class="banorte-mono">' + esc(b.display_account_number || b.account_number) +
        "</td><td>" + st + "</td><td>" + editButton + "</td>";
      tbody.appendChild(tr);
    });
    if (!(listing.rows || []).length) {
      tbody.innerHTML = '<tr><td colspan="6" class="banorte-empty">' +
        view.emptyMessage + "</td></tr>";
    }
    bindBenDetailButtons(tbody);
    const rangeLabel = "Mostrando " + (listing.start_index || 0) + "–" +
      (listing.end_index || 0);
    document.getElementById(view.prefix + "-meta").textContent = view.scope === "historical"
      ? rangeLabel + " de " + (listing.total || 0) + " históricos"
      : "Beneficiarios vigentes: " + (listing.total || 0) + " · " + rangeLabel;
    const pager = document.getElementById(view.prefix + "-pager");
    if (pager) {
      pager.innerHTML = "";
      function mk(label, page, disabled) {
        const btn = document.createElement("button");
        btn.type = "button";
        btn.className = "btn btn-secondary btn-sm";
        btn.textContent = label;
        btn.disabled = !!disabled;
        btn.addEventListener("click", function () { loadBenefListing(page, view.scope); });
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
    view.page = listing.page || 1;
  }

  async function loadBenefListing(page, scope) {
    const view = beneficiaryViews[scope] || beneficiaryViews.current;
    const seq = ++view.searchSeq;
    const out = await api("/nomina/exportaciones/banorte/beneficiarios/search", {
      scope: view.scope,
      page: page || 1,
      q_name: (document.getElementById(view.prefix + "-q").value || "").trim(),
      q_emp: (document.getElementById(view.prefix + "-emp").value || "").trim(),
      validation_status: document.getElementById(view.prefix + "-val").value,
      record_status: document.getElementById(view.prefix + "-rec").value,
      sort: document.getElementById(view.prefix + "-sort").value || "id_desc",
    });
    if (seq !== view.searchSeq) return;
    if (out.data.ok) renderBenefRows(out.data.listing, view.scope);
  }

  function scheduleBenefSearch(scope) {
    const view = beneficiaryViews[scope] || beneficiaryViews.current;
    clearTimeout(view.searchTimer);
    view.searchTimer = setTimeout(function () {
      loadBenefListing(1, view.scope);
    }, 300);
  }

  Object.keys(beneficiaryViews).forEach(function (scope) {
    const view = beneficiaryViews[scope];
    ["q", "emp", "val", "rec", "sort"].forEach(function (suffix) {
      const el = document.getElementById(view.prefix + "-" + suffix);
      if (!el) return;
      el.addEventListener(el.tagName === "SELECT" ? "change" : "input", function () {
        scheduleBenefSearch(view.scope);
      });
    });
  });
  bindBenDetailButtons(document.getElementById("banorte-ben-table"));
  bindBenDetailButtons(document.getElementById("banorte-hist-table"));

  function hydrateBenefListing(scope) {
    const view = beneficiaryViews[scope] || beneficiaryViews.current;
    const pager = document.getElementById(view.prefix + "-pager");
    const page = pager ? Number(pager.getAttribute("data-page") || 1) : 1;
    loadBenefListing(page || 1, view.scope);
  }
  hydrateBenefListing("current");
  document.querySelectorAll('[data-banorte-tab="legacy-beneficiarios"]').forEach(function (button) {
    button.addEventListener("click", function () {
      hydrateBenefListing("historical");
    });
  });

  function refreshAddPayBeneficiaries() {
    /* autocomplete hydrates on demand */
  }

  let addPaySearchSeq = 0;
  let addPaySearchTimer = null;
  const addPayQ = document.getElementById("banorte-add-pay-q");
  const addPayBen = document.getElementById("banorte-add-pay-ben");
  const addPaySuggest = document.getElementById("banorte-add-pay-suggest");
  if (addPayQ) addPayQ.addEventListener("input", function () {
    if (addPayBen) addPayBen.value = "";
    const q = (addPayQ.value || "").trim();
    clearTimeout(addPaySearchTimer);
    if (!addPaySuggest) return;
    if (q.length < 3) {
      addPaySuggest.hidden = true;
      addPaySuggest.innerHTML = "";
      return;
    }
    addPaySearchTimer = setTimeout(async function () {
      const seq = ++addPaySearchSeq;
      const out = await api("/nomina/exportaciones/banorte/beneficiarios/search", {
        page: 1,
        q_name: q,
        record_status: "ACTIVO",
        sort: "name_asc",
      });
      if (seq !== addPaySearchSeq) return;
      if (!out.data.ok) return;
      addPaySuggest.innerHTML = "";
      (out.data.listing.rows || []).slice(0, 8).forEach(function (b) {
        const btn = document.createElement("button");
        btn.type = "button";
        btn.className = "banorte-add-pay-suggest-item";
        btn.textContent = b.nombre_original + " · " + (b.employee_number_effective || "");
        btn.addEventListener("click", function () {
          if (addPayBen) addPayBen.value = String(b.id);
          addPayQ.value = b.nombre_original;
          addPaySuggest.hidden = true;
        });
        addPaySuggest.appendChild(btn);
      });
      addPaySuggest.hidden = !addPaySuggest.children.length;
    }, 300);
  });

  const addPayBtn = document.getElementById("banorte-add-pay-btn");
  if (addPayBtn) addPayBtn.addEventListener("click", function () {
    if (!draft) {
      alert("Abra o prepare un borrador primero.");
      return;
    }
    const benId = addPayBen ? addPayBen.value : "";
    const amount = (document.getElementById("banorte-add-pay-amount").value || "").trim();
    const msg = document.getElementById("banorte-add-pay-msg");
    if (!benId) {
      if (msg) { msg.hidden = false; msg.textContent = "Seleccione un beneficiario de la lista."; }
      return;
    }
    const nonce = "pay-" + draft.id + "-" + Date.now() + "-" + Math.random().toString(36).slice(2, 10);
    enqueueOrdinary({
      type: "add_payment",
      beneficiary_id: Number(benId),
      amount_final: amount,
      request_nonce: nonce,
    });
  });

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
    if (!append) availableNumbers = [];
    nums.forEach(function (n) {
      if (availableNumbers.indexOf(n) < 0) availableNumbers.push(n);
      availableAfter = n;
    });
    renderAvailableNumbers();
  }

  function renderAvailableNumbers() {
    const box = document.getElementById("banorte-available-emps");
    if (!box) return;
    const locallyUsed = beneficiaryGrid
      ? beneficiaryGrid.locallyUsedEffectiveEmployees()
      : new Set();
    box.innerHTML = "";
    availableNumbers.filter(function (n) { return !locallyUsed.has(n); }).forEach(function (n) {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "banorte-available-num";
      btn.textContent = n;
      btn.addEventListener("click", function () {
        if (beneficiaryGrid) beneficiaryGrid.applyAvailableNumber(n);
      });
      box.appendChild(btn);
    });
    if (!box.children.length) box.textContent = availableNumbers.length ? "Sin números libres en esta página." : "—";
  }
  const altaTabBtn = document.querySelector('[data-banorte-tab="agregar-benef"]');
  if (altaTabBtn) altaTabBtn.addEventListener("click", function () {
    availableAfter = null;
    availableNumbers = [];
    loadAvailableNumbers(false);
    if (!historyInitiallyPositioned && historyViewport) {
      requestAnimationFrame(function () { scrollHistoryToLatest(historyViewport); });
    }
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
