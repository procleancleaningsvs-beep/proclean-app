(function () {
  const root = document.getElementById("banorte-root");
  if (!root) return;
  let csrf = root.dataset.csrf || "";
  let draft = null;

  function setCsrf(token) { if (token) csrf = token; }
  function money(cents) {
    return (Number(cents || 0) / 100).toFixed(2);
  }
  function statusClass(state) {
    if (state === "OK") return "banorte-status--ok";
    if (state === "NEEDS_REVIEW") return "banorte-status--warn";
    if (state === "BLOCKED") return "banorte-status--bad";
    return "banorte-status--muted";
  }
  function statusLabel(state, row) {
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

  function renderRecon(rec) {
    const el = document.getElementById("banorte-recon");
    if (!rec) { el.innerHTML = ""; return; }
    el.innerHTML =
      "<div><span>Originales</span><strong>" + rec.original_row_count + "</strong></div>" +
      "<div><span>Incluidas</span><strong>" + rec.included_count + "</strong></div>" +
      "<div><span>Excluidas</span><strong>" + rec.excluded_count + "</strong></div>" +
      "<div><span>Total orig.</span><strong>$" + money(rec.total_original_cents) + "</strong></div>" +
      "<div><span>Ajustes +</span><strong>$" + money(rec.adjustments_positive_cents) + "</strong></div>" +
      "<div><span>Ajustes −</span><strong>$" + money(rec.adjustments_negative_cents) + "</strong></div>" +
      "<div><span>Total final</span><strong>$" + money(rec.total_final_cents) + "</strong></div>" +
      "<div><span>Diferencia</span><strong>$" + money(rec.difference_cents) + "</strong></div>" +
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
        beneficiary_id: tr.querySelector(".c-ben").value ? Number(tr.querySelector(".c-ben").value) : null,
        employee_number_snapshot: tr.querySelector(".c-emp").value || null,
        account_number_snapshot: tr.querySelector(".c-acct").value || null,
        amount_original_cents: Number(tr.dataset.originalCents || 0),
        amount_final_cents: Math.round(Number(tr.querySelector(".c-final").value || 0) * 100),
        included: tr.querySelector(".c-inc").checked ? 1 : 0,
        match_kind: tr.dataset.matchKind || "NONE",
        alias_id: tr.dataset.aliasId ? Number(tr.dataset.aliasId) : null,
        row_state: tr.dataset.rowState || "OK",
        warnings: JSON.parse(tr.dataset.warnings || "[]"),
        user_decision: JSON.parse(tr.dataset.userDecision || "{}"),
        nss_snapshot: tr.dataset.nss || null,
        banco_snapshot: tr.dataset.banco || null,
      });
    });
    return rows;
  }

  function renderEditor(d) {
    draft = d;
    const panel = document.getElementById("banorte-editor-panel");
    panel.hidden = false;
    document.getElementById("banorte-origin-box").textContent =
      "Borrador #" + d.id + " · " + d.origin_kind +
      (d.calculo_id ? (" · Cálculo #" + d.calculo_id) : "") +
      " · rev " + d.revision;
    renderRecon(d.reconciliation);
    const tbody = document.querySelector("#banorte-editor tbody");
    tbody.innerHTML = "";
    (d.rows || []).forEach(function (row) {
      const tr = document.createElement("tr");
      tr.dataset.rowId = row.id;
      tr.dataset.calculoRowId = row.calculo_row_id || "";
      tr.dataset.matchKind = row.match_kind || "NONE";
      tr.dataset.aliasId = row.alias_id || "";
      tr.dataset.rowState = row.row_state || "";
      tr.dataset.warnings = JSON.stringify(row.warnings || JSON.parse(row.warnings_json || "[]"));
      tr.dataset.userDecision = JSON.stringify(row.user_decision || JSON.parse(row.user_decision_json || "{}"));
      tr.dataset.originalCents = row.amount_original_cents;
      tr.dataset.nss = row.nss_snapshot || "";
      tr.dataset.banco = row.banco_snapshot || "";
      const warn = (row.warnings || JSON.parse(row.warnings_json || "[]")).join(", ");
      tr.innerHTML =
        '<td class="c-pos">' + row.position + "</td>" +
        '<td><input class="c-name" type="text" value="' + (row.nombre_recibido || "").replace(/"/g, "&quot;") + '"></td>' +
        '<td><input class="c-ben" type="number" value="' + (row.beneficiary_id || "") + '"></td>' +
        '<td><input class="c-emp" type="text" value="' + (row.employee_number_snapshot || "") + '"></td>' +
        '<td><input class="c-acct" type="text" value="' + (row.account_number_snapshot || "") + '" autocomplete="off"></td>' +
        "<td>" + money(row.amount_original_cents) + "</td>" +
        '<td><input class="c-final" type="number" step="0.01" value="' + money(row.amount_final_cents) + '"></td>' +
        '<td><span class="banorte-status ' + statusClass(row.row_state) + '" title="' + statusLabel(row.row_state) + '">' + statusLabel(row.row_state) + "</span></td>" +
        "<td>" + (warn || "—") + "</td>" +
        '<td><input class="c-inc" type="checkbox" ' + (row.included ? "checked" : "") + "></td>";
      tbody.appendChild(tr);
    });
  }

  document.querySelectorAll("[data-banorte-drawer]").forEach(function (btn) {
    btn.addEventListener("click", function () {
      const id = "drawer-" + btn.getAttribute("data-banorte-drawer");
      document.getElementById("banorte-drawer-backdrop").hidden = false;
      document.getElementById(id).hidden = false;
    });
  });
  function closeDrawers() {
    document.getElementById("banorte-drawer-backdrop").hidden = true;
    document.querySelectorAll(".banorte-drawer").forEach(function (d) { d.hidden = true; });
  }
  document.getElementById("banorte-drawer-backdrop").addEventListener("click", closeDrawers);
  document.querySelectorAll("[data-close-drawer]").forEach(function (b) {
    b.addEventListener("click", closeDrawers);
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
      closeDrawers();
      renderEditor(out.data.draft);
    });
  });

  document.getElementById("banorte-save-draft").addEventListener("click", async function () {
    if (!draft) return;
    const out = await api("/nomina/exportaciones/banorte/drafts/" + draft.id + "/save", {
      expected_revision: draft.revision,
      rows: collectRows(),
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
      consecutive: document.getElementById("banorte-consec").value,
      layout_date: document.getElementById("banorte-date").value || null,
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
    closeDrawers();
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
        closeDrawers();
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
        if (again.data.ok) { closeDrawers(); renderEditor(again.data.draft); }
        else alert(again.data.code || "error");
      };
      return;
    }
    if (!out.data.ok) {
      alert(out.data.code || "error");
      return;
    }
    closeDrawers();
    renderEditor(out.data.draft);
  });
})();
