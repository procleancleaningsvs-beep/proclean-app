/**
 * Vitroflex — captura, import Excel, vista previa y PDF desde servidor
 * (plantillas DOCX oficiales → LibreOffice → PDF).
 */
(function () {
  const MESES = [
    "enero", "febrero", "marzo", "abril", "mayo", "junio",
    "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
  ];

  const DEFAULT_ACTIVIDAD = "Aux. de limpieza";
  const DEFAULT_TEL = "81 2183 9413";

  const cfg = window.VF_CONFIG || {};
  const kind = cfg.kind || "memo";

  const tbody = document.getElementById("vf-tbody");
  const btnAdd = document.getElementById("vf-add-row");
  const btnImport = document.getElementById("vf-import");
  const fileInput = document.getElementById("vf-file");
  const btnPreview = document.getElementById("vf-preview");
  const btnPdf = document.getElementById("vf-pdf");
  const selNombrePdf = document.getElementById("vf-pdf-name-mode");

  const fechaTexto = document.getElementById("vf-fecha");
  const plantaSel = document.getElementById("vf-planta");
  const permiso1 = document.getElementById("vf-permiso1");
  const permiso2 = document.getElementById("vf-permiso2");

  const modalDefaults = document.getElementById("vf-modal-defaults");
  const btnDefaultsSi = document.getElementById("vf-defaults-si");
  const btnDefaultsNo = document.getElementById("vf-defaults-no");

  const previewBackdrop = document.getElementById("vf-preview-backdrop");
  const previewFrame = document.getElementById("vf-preview-frame");
  const btnPreviewClose = document.getElementById("vf-preview-close");
  const statusBanner = document.getElementById("vf-status-banner");

  let permiso2Manual = false;
  let pendingImportRows = null;
  let lastPreviewUrl = null;

  function parseYMD(s) {
    if (!s) return null;
    const p = String(s).slice(0, 10).split("-");
    if (p.length !== 3) return null;
    const y = +p[0], m = +p[1], d = +p[2];
    if (!y || !m || !d) return null;
    return new Date(y, m - 1, d);
  }

  function fmtYMD(d) {
    const y = d.getFullYear();
    const mo = String(d.getMonth() + 1).padStart(2, "0");
    const day = String(d.getDate()).padStart(2, "0");
    return `${y}-${mo}-${day}`;
  }

  function addMonths(d, months) {
    const y = d.getFullYear() + Math.floor((d.getMonth() + months) / 12);
    const m = ((d.getMonth() + months) % 12 + 12) % 12;
    const last = new Date(y, m + 1, 0).getDate();
    const day = Math.min(d.getDate(), last);
    return new Date(y, m, day);
  }

  function computePermiso2(p1) {
    const d = parseYMD(p1);
    if (!d) return "";
    const d2 = addMonths(d, 1);
    d2.setDate(d2.getDate() + 17);
    return fmtYMD(d2);
  }

  if (permiso1 && permiso2) {
    permiso2.addEventListener("input", function () {
      permiso2Manual = true;
    });
    permiso1.addEventListener("change", function () {
      if (!permiso2Manual) {
        permiso2.value = computePermiso2(permiso1.value);
      }
    });
  }

  function rowTemplate() {
    const tr = document.createElement("tr");
    tr.innerHTML =
      '<td><input type="text" class="vf-inp" data-f="nombre" autocomplete="off" /></td>' +
      '<td><input type="text" class="vf-inp" data-f="imss" autocomplete="off" /></td>' +
      '<td><input type="text" class="vf-inp" data-f="actividad" autocomplete="off" /></td>' +
      '<td><input type="text" class="vf-inp" data-f="tel" autocomplete="off" /></td>' +
      '<td class="vf-row-actions"><button type="button" class="btn btn-small danger-btn vf-btn-small vf-remove">Quitar</button></td>';
    tr.querySelector(".vf-remove").addEventListener("click", function () {
      tr.remove();
      if (!tbody.querySelector("tr")) addRow();
    });
    return tr;
  }

  function addRow() {
    tbody.appendChild(rowTemplate());
  }

  function readRows() {
    const out = [];
    tbody.querySelectorAll("tr").forEach(function (tr) {
      const o = {};
      tr.querySelectorAll(".vf-inp").forEach(function (inp) {
        o[inp.getAttribute("data-f")] = (inp.value || "").trim();
      });
      if (o.nombre || o.imss || o.actividad || o.tel) out.push(o);
    });
    return out;
  }

  function applyRows(rows) {
    tbody.innerHTML = "";
    if (!rows.length) {
      addRow();
      return;
    }
    rows.forEach(function (r) {
      const tr = rowTemplate();
      tr.querySelector('[data-f="nombre"]').value = r.nombre || "";
      tr.querySelector('[data-f="imss"]').value = r.imss || "";
      tr.querySelector('[data-f="actividad"]').value = r.actividad || "";
      tr.querySelector('[data-f="tel"]').value = r.tel || "";
      tbody.appendChild(tr);
    });
  }

  if (btnAdd) btnAdd.addEventListener("click", function () { addRow(); });

  function mergeImport(rows, fillDefaults) {
    const merged = rows.map(function (r) {
      const x = {
        nombre: r.nombre || "",
        imss: r.imss || "",
        actividad: r.actividad || "",
        tel: r.tel || "",
      };
      if (fillDefaults) {
        if (!x.actividad) x.actividad = DEFAULT_ACTIVIDAD;
        if (!x.tel) x.tel = DEFAULT_TEL;
      }
      return x;
    });
    const cur = readRows();
    applyRows(cur.concat(merged));
  }

  function showDefaultsModal(rows) {
    pendingImportRows = rows;
    modalDefaults.hidden = false;
  }

  function hideDefaultsModal() {
    modalDefaults.hidden = true;
    pendingImportRows = null;
  }

  if (btnDefaultsSi) {
    btnDefaultsSi.addEventListener("click", function () {
      if (pendingImportRows) mergeImport(pendingImportRows, true);
      hideDefaultsModal();
    });
  }
  if (btnDefaultsNo) {
    btnDefaultsNo.addEventListener("click", function () {
      if (pendingImportRows) mergeImport(pendingImportRows, false);
      hideDefaultsModal();
    });
  }

  if (btnImport && fileInput) {
    btnImport.addEventListener("click", function () { fileInput.click(); });
    fileInput.addEventListener("change", function () {
      const f = fileInput.files && fileInput.files[0];
      if (!f) return;
      const fd = new FormData();
      fd.append("file", f);
      fetch(cfg.importUrl, { method: "POST", body: fd, credentials: "same-origin" })
        .then(function (r) { return r.json(); })
        .then(function (data) {
          fileInput.value = "";
          if (!data.ok) {
            alert(data.error || "Error al importar.");
            return;
          }
          if (data.needs_default_fields) {
            showDefaultsModal(data.rows);
          } else {
            mergeImport(data.rows, false);
          }
        })
        .catch(function () {
          fileInput.value = "";
          alert("No se pudo importar el archivo.");
        });
    });
  }

  function buildPayload() {
    const workers = readRows();
    if (kind === "memo") {
      return {
        kind: "memo",
        fecha_texto: fechaTexto ? fechaTexto.value : "",
        permiso1: permiso1 ? permiso1.value || null : null,
        permiso2: permiso2 ? permiso2.value || null : null,
        workers: workers,
      };
    }
    return {
      kind: "cr",
      fecha_texto: fechaTexto ? fechaTexto.value : "",
      planta: plantaSel ? plantaSel.value || "" : "",
      workers: workers,
    };
  }

  function sanitizeFilename(s) {
    return String(s)
      .replace(/[<>:"/\\|?*\x00-\x1f]/g, "_")
      .replace(/\s+/g, " ")
      .trim()
      .slice(0, 160) || "documento";
  }

  function resumirNombres(nombres) {
    const limpios = nombres.filter(Boolean);
    if (!limpios.length) return "sin_nombres";
    if (limpios.length <= 2) return limpios.join(" ");
    return limpios.slice(0, 2).join(" ") + " y " + (limpios.length - 2) + " más";
  }

  function mesDePermiso1() {
    const d = parseYMD(permiso1 && permiso1.value);
    if (!d) return "documento";
    return MESES[d.getMonth()] + " " + d.getFullYear();
  }

  function mesCRFilename() {
    const d = parseYMD(permiso1 && permiso1.value);
    if (d) return MESES[d.getMonth()] + " " + d.getFullYear();
    const t = fechaTexto ? fechaTexto.value : "";
    const m = t.match(/de\s+([a-záéíóúñ]+)\s+de\s+(\d{4})/i);
    if (m) return m[1] + " " + m[2];
    return MESES[new Date().getMonth()] + " " + new Date().getFullYear();
  }

  function pdfFilename() {
    const rows = readRows();
    const nombres = rows.map(function (r) { return r.nombre; });
    const mode = selNombrePdf ? selNombrePdf.value : "a";
    const planta = plantaSel ? (plantaSel.value || "").trim() || "Planta" : "Planta";

    if (kind === "memo") {
      if (mode === "b") {
        return sanitizeFilename("MEMO MENSUAL " + mesDePermiso1()) + ".pdf";
      }
      return sanitizeFilename("MEMO " + resumirNombres(nombres)) + ".pdf";
    }
    if (mode === "b") {
      return sanitizeFilename("CR " + planta + " " + mesCRFilename()) + ".pdf";
    }
    return sanitizeFilename("CR " + planta + " " + resumirNombres(nombres)) + ".pdf";
  }

  function fetchPdf(disposition) {
    const payload = buildPayload();
    payload.filename = pdfFilename().replace(/\.pdf$/i, "");
    payload.disposition = disposition;
    return fetch(cfg.generatePdfUrl, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
      credentials: "same-origin",
    }).then(function (r) {
      if (r.ok) return r.blob();
      return r.json().then(function (j) {
        throw new Error(j.error || r.statusText || "Error al generar PDF");
      });
    });
  }

  function openPreview() {
    if (!previewFrame) return;
    fetchPdf("inline")
      .then(function (blob) {
        if (lastPreviewUrl) {
          URL.revokeObjectURL(lastPreviewUrl);
        }
        lastPreviewUrl = URL.createObjectURL(blob);
        previewFrame.src = lastPreviewUrl;
        if (previewBackdrop) previewBackdrop.hidden = false;
      })
      .catch(function (e) {
        alert(e.message || String(e));
      });
  }

  function runDownload() {
    fetchPdf("attachment")
      .then(function (blob) {
        const a = document.createElement("a");
        a.href = URL.createObjectURL(blob);
        a.download = pdfFilename();
        document.body.appendChild(a);
        a.click();
        a.remove();
        setTimeout(function () { URL.revokeObjectURL(a.href); }, 2000);
      })
      .catch(function (e) {
        alert(e.message || String(e));
      });
  }

  if (btnPreview) btnPreview.addEventListener("click", openPreview);
  if (btnPdf) btnPdf.addEventListener("click", runDownload);
  if (btnPreviewClose) {
    btnPreviewClose.addEventListener("click", function () {
      if (previewBackdrop) previewBackdrop.hidden = true;
      if (previewFrame) previewFrame.src = "about:blank";
    });
  }
  if (previewBackdrop) {
    previewBackdrop.addEventListener("click", function (e) {
      if (e.target === previewBackdrop) {
        previewBackdrop.hidden = true;
        if (previewFrame) previewFrame.src = "about:blank";
      }
    });
  }

  if (cfg.statusUrl && statusBanner) {
    fetch(cfg.statusUrl, { credentials: "same-origin" })
      .then(function (r) { return r.json(); })
      .then(function (st) {
        const okMemo = kind === "memo" ? st.templates && st.templates.memo_docx : st.templates && st.templates.cr_docx;
        const okLo = st.libreoffice;
        if (!okMemo) {
          statusBanner.hidden = false;
          statusBanner.textContent =
            "Falta la plantilla DOCX oficial en vitroflex_templates/. Copia MEMO MENSUAL FORMATO.docx o CR MENSUAL FORMATO.docx según corresponda. Ver README en esa carpeta.";
        } else if (!okLo) {
          statusBanner.hidden = false;
          statusBanner.textContent =
            "No se detectó LibreOffice (soffice). Instálalo o define PROCLEAN_LIBREOFFICE con la ruta a soffice.exe.";
        }
      })
      .catch(function () {});
  }

  if (tbody && !tbody.querySelector("tr")) addRow();
})();
