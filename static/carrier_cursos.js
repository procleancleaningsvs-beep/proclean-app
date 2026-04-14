/**
 * Carrier > Cursos: hoja única (vista previa + subida + edición), historial IMSS, modo constancia.
 */
(function () {
  function esc(s) {
    const d = document.createElement("div");
    d.textContent = s == null ? "" : String(s);
    return d.innerHTML;
  }

  function readSlotsState() {
    const el = document.getElementById("carrier-slots-state-json");
    if (!el || !el.textContent) return {};
    try {
      return JSON.parse(el.textContent);
    } catch (e) {
      return {};
    }
  }

  (function modeSwitch() {
    const form = document.getElementById("carrier-mod-form");
    const hidden = document.getElementById("carrier-mod-hidden");
    if (!form || !hidden) return;
    document.querySelectorAll("[data-carrier-mod]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        const m = btn.getAttribute("data-carrier-mod");
        if (!m || hidden.value === m) return;
        hidden.value = m;
        form.submit();
      });
    });
  })();

  const root = document.querySelector("[data-carrier-workspace]");
  if (!root || !window.HTMLDialogElement) return;

  const expedienteId = root.getAttribute("data-expediente-id");
  const uploadPrefix = (root.getAttribute("data-upload-prefix") || "").replace(/\/+$/, "") + "/";
  const slotMetaUrl = root.getAttribute("data-slot-meta-url") || "";
  const imssJsonUrl = root.getAttribute("data-imss-json-url") || "";
  const vincularPost = root.getAttribute("data-vincular-post") || "";

  /* ——— Subir: solo elegir archivo → misma hoja que vista previa ——— */
  (function uploadPicker() {
    const modal = document.getElementById("carrier-anexo-modal");
    const form = document.getElementById("carrier-anexo-form");
    const fileInput = document.getElementById("carrier-anexo-file");
    const titleEl = document.getElementById("carrier-anexo-title");
    const dz = document.getElementById("carrier-anexo-dropzone");
    if (!modal || !form || !fileInput) return;

    let lastSlot = "";

    function wireFile(f) {
      if (!f || !lastSlot) return;
      const action = uploadPrefix + encodeURIComponent(lastSlot);
      modal.close();
      document.dispatchEvent(
        new CustomEvent("carrier-sheet-open", {
          detail: { kind: "upload", slot: lastSlot, file: f, uploadUrl: action },
        })
      );
    }

    document.querySelectorAll("[data-open-anexo-modal]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        const slot = btn.getAttribute("data-slot");
        if (!slot || !uploadPrefix || uploadPrefix === "/") return;
        lastSlot = slot;
        form.action = uploadPrefix + encodeURIComponent(slot);
        const t = btn.getAttribute("data-slot-title");
        if (titleEl) titleEl.textContent = t && t.trim() ? t.trim() : "Subir anexo";
        fileInput.value = "";
        modal.showModal();
        setTimeout(function () {
          fileInput.focus();
        }, 50);
      });
    });

    document.querySelectorAll("[data-anexo-close]").forEach(function (b) {
      b.addEventListener("click", function () {
        modal.close();
        fileInput.value = "";
      });
    });

    fileInput.addEventListener("change", function () {
      const f = fileInput.files && fileInput.files[0];
      wireFile(f || null);
    });

    if (dz) {
      dz.addEventListener("dragover", function (ev) {
        ev.preventDefault();
      });
      dz.addEventListener("drop", function (ev) {
        ev.preventDefault();
        const f = ev.dataTransfer.files && ev.dataTransfer.files[0];
        if (!f) return;
        const dt = new DataTransfer();
        dt.items.add(f);
        fileInput.files = dt.files;
        wireFile(f);
      });
    }

    document.addEventListener("paste", function (ev) {
      if (!modal.open) return;
      const items = ev.clipboardData && ev.clipboardData.items;
      if (!items) return;
      for (let i = 0; i < items.length; i++) {
        const it = items[i];
        if (it.kind !== "file") continue;
        const f = it.getAsFile();
        if (f && f.type && f.type.indexOf("image/") === 0) {
          ev.preventDefault();
          const dt = new DataTransfer();
          dt.items.add(f);
          fileInput.files = dt.files;
          wireFile(f);
          break;
        }
      }
    });
  })();

  /* ——— Historial IMSS ——— */
  (function imssModal() {
    const dlg = document.getElementById("carrier-vincular-imss-modal");
    if (!dlg || !imssJsonUrl) return;

    const tbody = document.getElementById("carrier-imss-tbody");
    const search = document.getElementById("carrier-imss-search");
    const countEl = document.getElementById("carrier-imss-count");
    const empty = document.getElementById("carrier-imss-empty");
    const pager = document.getElementById("carrier-imss-pager");
    const prevBtn = document.getElementById("carrier-imss-prev");
    const nextBtn = document.getElementById("carrier-imss-next");
    const pageLabel = document.getElementById("carrier-imss-page-label");

    let page = 1;
    let perPage = 12;
    let q = "";
    let debounceTimer = null;
    let lastTotalPages = 1;

    function vincUrl() {
      if (vincularPost) return vincularPost;
      if (slotMetaUrl) return slotMetaUrl.replace(/\/slot-meta$/, "/vincular-formato");
      return "/carrier/cursos/expediente/" + encodeURIComponent(expedienteId) + "/vincular-formato";
    }

    async function load() {
      if (!tbody) return;
      tbody.innerHTML = '<tr><td colspan="4" class="carrier-imss-loading">Cargando…</td></tr>';
      const u = new URL(imssJsonUrl, window.location.origin);
      u.searchParams.set("page", String(page));
      u.searchParams.set("per_page", String(perPage));
      if (q) u.searchParams.set("q", q);
      try {
        const res = await fetch(u.toString(), { credentials: "same-origin" });
        const data = await res.json();
        if (!data.ok) throw new Error("bad");
        tbody.innerHTML = "";
        if (countEl) {
          countEl.textContent =
            data.total + " registro" + (data.total === 1 ? "" : "s") + (q ? " (filtrados)" : "");
        }
        lastTotalPages = Math.max(1, parseInt(data.total_pages, 10) || 1);
        if (page > lastTotalPages) {
          page = lastTotalPages;
          load();
          return;
        }
        if (!data.rows || !data.rows.length) {
          if (empty) empty.hidden = false;
          if (pager) pager.hidden = true;
          return;
        }
        if (empty) empty.hidden = true;
        if (pager) pager.hidden = lastTotalPages <= 1;
        if (pageLabel) pageLabel.textContent = "Página " + data.page + " de " + lastTotalPages;
        if (prevBtn) prevBtn.disabled = data.page <= 1;
        if (nextBtn) nextBtn.disabled = data.page >= lastTotalPages;

        data.rows.forEach(function (rec) {
          const tr = document.createElement("tr");
          const folioLote = [rec.folio, rec.lote].filter(Boolean).join(" · ");
          const sub = folioLote ? '<br><span class="helper">' + esc(folioLote) + "</span>" : "";
          tr.innerHTML =
            "<td>" +
            esc(rec.created_at) +
            '</td><td class="history-cell-wrap">' +
            esc(rec.filename) +
            sub +
            "</td><td>" +
            esc(String(rec.movement_count)) +
            '</td><td class="carrier-imss-actions-cell"></td>';
          const cell = tr.querySelector(".carrier-imss-actions-cell");
          if (rec.movement_count > 1) {
            const b = document.createElement("button");
            b.type = "button";
            b.className = "btn btn-secondary btn-sm";
            b.textContent = "Elegir persona…";
            b.setAttribute("data-imss-expand", String(rec.id));
            cell.appendChild(b);
          } else {
            const f = document.createElement("form");
            f.method = "post";
            f.action = vincUrl();
            f.className = "carrier-vincular-inline-form";
            f.innerHTML =
              '<input type="hidden" name="format_history_id" value="' +
              esc(String(rec.id)) +
              '">' +
              '<input type="hidden" name="movimiento_idx" value="0">' +
              '<button type="submit" class="btn btn-primary btn-sm">Vincular</button>';
            cell.appendChild(f);
          }
          tbody.appendChild(tr);

          if (rec.movement_count > 1) {
            const tr2 = document.createElement("tr");
            tr2.className = "carrier-imss-detail";
            tr2.hidden = true;
            tr2.id = "carrier-imss-detail-" + rec.id;
            const opts = (rec.nombres || [])
              .map(function (n, i) {
                return '<option value="' + i + '">' + esc(n) + "</option>";
              })
              .join("");
            tr2.innerHTML =
              '<td colspan="4"><form method="post" action="' +
              vincUrl() +
              '" class="carrier-imss-expand-form">' +
              '<input type="hidden" name="format_history_id" value="' +
              esc(String(rec.id)) +
              '">' +
              '<label class="carrier-inline-label">Persona para este expediente<select name="movimiento_idx" required>' +
              opts +
              "</select></label> " +
              '<button type="submit" class="btn btn-primary btn-sm">Vincular esta persona</button></form></td>';
            tbody.appendChild(tr2);
          }
        });
      } catch (e) {
        tbody.innerHTML =
          '<tr><td colspan="4" class="carrier-imss-err">No se pudo cargar el historial.</td></tr>';
      }
    }

    dlg.addEventListener("click", function (ev) {
      const t = ev.target;
      if (t && t.getAttribute && t.getAttribute("data-imss-expand")) {
        const id = t.getAttribute("data-imss-expand");
        const row = document.getElementById("carrier-imss-detail-" + id);
        if (row) row.hidden = !row.hidden;
      }
    });

    function openDlg() {
      page = 1;
      q = search ? search.value.trim() : "";
      if (dlg.showModal) dlg.showModal();
      load();
    }

    document.getElementById("carrier-btn-vincular-modal") &&
      document.getElementById("carrier-btn-vincular-modal").addEventListener("click", openDlg);
    document.getElementById("carrier-btn-vincular-modal-2") &&
      document.getElementById("carrier-btn-vincular-modal-2").addEventListener("click", openDlg);
    dlg.querySelectorAll("[data-vincular-close]").forEach(function (x) {
      x.addEventListener("click", function () {
        dlg.close();
      });
    });

    if (search) {
      search.addEventListener("input", function () {
        clearTimeout(debounceTimer);
        debounceTimer = setTimeout(function () {
          q = search.value.trim();
          page = 1;
          load();
        }, 320);
      });
    }
    if (prevBtn) {
      prevBtn.addEventListener("click", function () {
        if (page > 1) {
          page--;
          load();
        }
      });
    }
    if (nextBtn) {
      nextBtn.addEventListener("click", function () {
        if (page < lastTotalPages) {
          page++;
          load();
        }
      });
    }
  })();

  /* ——— Hoja: edición guardada + subida nueva (misma UX) ——— */
  (function sheetStudio() {
    const modal = document.getElementById("carrier-sheet-modal");
    const sheet = document.getElementById("carrier-doc-sheet");
    const titleEl = document.getElementById("carrier-sheet-title");
    const hintEl = document.getElementById("carrier-sheet-hint");
    const modeBanner = document.getElementById("carrier-sheet-mode-banner");
    const scaleSlider = document.getElementById("carrier-sheet-scale-slider");
    const scaleVal = document.getElementById("carrier-sheet-scale-val");
    const saveBtn = document.getElementById("carrier-sheet-save");
    const uploadBtn = document.getElementById("carrier-sheet-confirm-upload");
    if (!modal || !sheet || !slotMetaUrl) return;

    let croppers = {};
    const cropperPhases = {};
    const slotHosts = {};
    const slotNormCache = {};
    let activeSlots = [];
    let scaleWrapEl = null;
    let uploadState = null;
    let editMode = null;
    let uploadPdfOnly = false;

    function destroyAllCroppers() {
      Object.keys(croppers).forEach(function (k) {
        try {
          croppers[k].destroy();
        } catch (e) {
          /* ignore */
        }
        delete croppers[k];
        delete cropperPhases[k];
        delete slotHosts[k];
      });
    }

    function cropNormFromCropper(cp) {
      if (!cp) return "[0,0,1,1]";
      const d = cp.getData();
      const imgData = cp.getImageData();
      const nw = imgData.naturalWidth;
      const nh = imgData.naturalHeight;
      if (!(nw > 0 && nh > 0 && d && typeof d.x === "number")) return "[0,0,1,1]";
      const x0 = Math.max(0, Math.min(1, d.x / nw));
      const y0 = Math.max(0, Math.min(1, d.y / nh));
      const x1 = Math.max(0, Math.min(1, (d.x + d.width) / nw));
      const y1 = Math.max(0, Math.min(1, (d.y + d.height) / nh));
      return JSON.stringify([x0, y0, x1, y1]);
    }

    function applyNormToCropper(cp, norm) {
      if (!cp || !norm || norm.length !== 4) return;
      const imgData = cp.getImageData();
      const nw = imgData.naturalWidth;
      const nh = imgData.naturalHeight;
      if (!(nw > 0 && nh > 0)) return;
      cp.setData({
        x: norm[0] * nw,
        y: norm[1] * nh,
        width: (norm[2] - norm[0]) * nw,
        height: (norm[3] - norm[1]) * nh,
      });
    }

    function makeCropper(img, phase) {
      const layout = phase === "layout";
      return new window.Cropper(img, {
        viewMode: 1,
        dragMode: layout ? "move" : "crop",
        autoCropArea: 1,
        movable: true,
        scalable: layout,
        zoomable: true,
        zoomOnWheel: true,
        wheelZoomRatio: layout ? 0.12 : 0.08,
        cropBoxMovable: !layout,
        cropBoxResizable: !layout,
        guides: true,
        center: true,
        background: true,
        highlight: !layout,
        toggleDragModeOnDblclick: false,
      });
    }

    function updateModeBanner(slot) {
      if (!modeBanner) return;
      const ph = cropperPhases[slot] || "layout";
      if (uploadPdfOnly || !croppers[slot]) {
        modeBanner.hidden = true;
        modeBanner.textContent = "";
        return;
      }
      if (ph === "crop") {
        modeBanner.hidden = false;
        modeBanner.textContent =
          "Modo recorte activo: ajusta el marco sobre la imagen. Doble clic de nuevo para volver a colocación (mover y escalar sin recortar píxeles).";
      } else {
        modeBanner.hidden = false;
        modeBanner.textContent =
          "Colocación: mueve la imagen y escálala (esquinas del lienzo o rueda). Doble clic para entrar al modo recorte de píxeles.";
      }
    }

    function togglePhaseForSlot(slot) {
      const cp = croppers[slot];
      const host = slotHosts[slot];
      if (!cp || !host || !window.Cropper) return;
      const cur = cropperPhases[slot] || "layout";
      try {
        slotNormCache[slot] = JSON.parse(cropNormFromCropper(cp));
      } catch (e) {
        slotNormCache[slot] = [0, 0, 1, 1];
      }
      const next = cur === "layout" ? "crop" : "layout";
      const img = cp.image;
      cp.destroy();
      delete croppers[slot];
      croppers[slot] = makeCropper(img, next);
      cropperPhases[slot] = next;
      try {
        applyNormToCropper(croppers[slot], slotNormCache[slot] || [0, 0, 1, 1]);
      } catch (e) {
        /* ignore */
      }
      updateModeBanner(slot);
    }

    function bindSlotInteractions(slot, img) {
      img.addEventListener("dblclick", function (ev) {
        ev.preventDefault();
        ev.stopPropagation();
        togglePhaseForSlot(slot);
      });
    }

    function updateScaleVisual() {
      const v = scaleSlider ? (parseInt(scaleSlider.value, 10) || 100) / 100 : 1;
      if (scaleVal) scaleVal.textContent = Math.round(v * 100) + "%";
      if (scaleWrapEl) {
        scaleWrapEl.style.transform = "scale(" + v + ")";
        scaleWrapEl.style.transformOrigin = "center center";
      }
    }

    function initImageSlot(container, slot, st) {
      if (!st || !st.has_file) {
        const ph = document.createElement("div");
        ph.className = "carrier-ine-placeholder";
        ph.textContent = slot.indexOf("reverso") >= 0 ? "Sin segundo archivo" : "Sin archivo";
        container.appendChild(ph);
        return;
      }
      const wrap = document.createElement("div");
      wrap.className = "carrier-slot-visual-wrap";
      const img = document.createElement("img");
      img.className = "carrier-sheet-slot-img";
      img.alt = slot;
      img.src = st.preview_url + "?t=" + Date.now();
      wrap.appendChild(img);
      container.appendChild(wrap);
      slotHosts[slot] = container;

      img.addEventListener("load", function () {
        if (typeof window.Cropper !== "function") return;
        croppers[slot] = makeCropper(img, "layout");
        cropperPhases[slot] = "layout";
        if (st.crop_norm && Array.isArray(st.crop_norm) && st.crop_norm.length === 4) {
          try {
            applyNormToCropper(croppers[slot], st.crop_norm.map(Number));
            slotNormCache[slot] = st.crop_norm.map(Number);
          } catch (e) {
            /* ignore */
          }
        }
        bindSlotInteractions(slot, img);
        updateModeBanner(slot);
      });
    }

    function initUploadImageSlot(container, slot, blobUrl) {
      const wrap = document.createElement("div");
      wrap.className = "carrier-slot-visual-wrap";
      const img = document.createElement("img");
      img.className = "carrier-sheet-slot-img";
      img.alt = slot;
      img.src = blobUrl;
      wrap.appendChild(img);
      container.appendChild(wrap);
      slotHosts[slot] = container;
      img.addEventListener("load", function () {
        if (typeof window.Cropper !== "function") return;
        croppers[slot] = makeCropper(img, "layout");
        cropperPhases[slot] = "layout";
        bindSlotInteractions(slot, img);
        updateModeBanner(slot);
      });
    }

    function setFooterEdit() {
      uploadState = null;
      uploadPdfOnly = false;
      if (saveBtn) saveBtn.hidden = false;
      if (uploadBtn) uploadBtn.hidden = true;
    }

    function setFooterUpload() {
      if (saveBtn) saveBtn.hidden = true;
      if (uploadBtn) uploadBtn.hidden = false;
    }

    function openEditor(mode) {
      setFooterEdit();
      editMode = mode;
      const slotsState = readSlotsState();
      destroyAllCroppers();
      sheet.innerHTML = "";
      activeSlots = [];
      Object.keys(slotNormCache).forEach(function (k) {
        delete slotNormCache[k];
      });

      const inner = document.createElement("div");
      inner.className = "carrier-doc-sheet-inner";
      scaleWrapEl = inner;
      sheet.appendChild(inner);

      if (mode === "ine") {
        if (titleEl) titleEl.textContent = "Vista previa / editar — INE (misma hoja)";
        if (hintEl) {
          hintEl.textContent =
            "Misma composición que el PDF final. Doble clic en cada imagen alterna colocación / recorte.";
        }
        inner.classList.add("carrier-doc-sheet-inner--ine");
        const L = document.createElement("div");
        L.className = "carrier-ine-canvas";
        const R = document.createElement("div");
        R.className = "carrier-ine-canvas";
        inner.appendChild(L);
        inner.appendChild(R);
        const stL = slotsState.ine_frente;
        const stR = slotsState.ine_reverso;
        initImageSlot(L, "ine_frente", stL);
        initImageSlot(R, "ine_reverso", stR);
        if (stL && stL.has_file) activeSlots.push("ine_frente");
        if (stR && stR.has_file) activeSlots.push("ine_reverso");
      } else {
        const st = slotsState[mode];
        const labels = {
          curso_evidencia: "Evidencia del curso / Forms",
          foto_persona: "Foto de la persona",
          renovacion_sua: "Renovación / extracto SUA",
        };
        if (titleEl) titleEl.textContent = "Vista previa / editar — " + (labels[mode] || mode);
        if (hintEl) {
          hintEl.textContent =
            "Misma hoja que en el PDF. Colocación por defecto; doble clic en la imagen para modo recorte.";
        }
        inner.classList.add("carrier-doc-sheet-inner--single");
        const box = document.createElement("div");
        box.className = "carrier-single-canvas";
        inner.appendChild(box);
        initImageSlot(box, mode, st);
        if (st && st.has_file) activeSlots.push(mode);
      }

      const rs0 =
        mode === "ine"
          ? slotsState.ine_frente && slotsState.ine_frente.render_scale
            ? slotsState.ine_frente.render_scale
            : slotsState.ine_reverso && slotsState.ine_reverso.render_scale
            ? slotsState.ine_reverso.render_scale
            : null
          : slotsState[mode] && slotsState[mode].render_scale
          ? slotsState[mode].render_scale
          : null;
      const pct = rs0 ? Math.round(Number(rs0) * 100) : 100;
      if (scaleSlider) scaleSlider.value = String(Math.max(70, Math.min(130, pct)));
      updateScaleVisual();
      modal.showModal();
    }

    function openUploadStudio(detail) {
      setFooterUpload();
      editMode = null;
      uploadState = { slot: detail.slot, file: detail.file, uploadUrl: detail.uploadUrl };
      destroyAllCroppers();
      sheet.innerHTML = "";
      activeSlots = [];
      Object.keys(slotNormCache).forEach(function (k) {
        delete slotNormCache[k];
      });

      const inner = document.createElement("div");
      inner.className = "carrier-doc-sheet-inner carrier-doc-sheet-inner--single";
      scaleWrapEl = inner;
      sheet.appendChild(inner);
      const box = document.createElement("div");
      box.className = "carrier-single-canvas";
      inner.appendChild(box);

      const f = detail.file;
      const type = f.type || "";
      if (titleEl) titleEl.textContent = "Subir — vista en hoja";
      if (type === "application/pdf") {
        uploadPdfOnly = true;
        if (hintEl) hintEl.textContent = "PDF: ajusta la escala con el control inferior. Se subirá el archivo completo.";
        if (modeBanner) {
          modeBanner.hidden = false;
          modeBanner.textContent = "Vista previa del PDF en la hoja (primera página como referencia visual en exportación se aplica al rasterizar).";
        }
        const url = URL.createObjectURL(f);
        const iframe = document.createElement("iframe");
        iframe.className = "carrier-sheet-pdf-iframe";
        iframe.src = url;
        iframe.title = "Vista previa PDF";
        box.appendChild(iframe);
        activeSlots = [];
      } else {
        uploadPdfOnly = false;
        if (hintEl) {
          hintEl.textContent =
            "Misma experiencia que “Vista previa / editar”: colocación (mover y escalar) por defecto; doble clic = recorte de píxeles.";
        }
        const url = URL.createObjectURL(f);
        initUploadImageSlot(box, detail.slot, url);
        activeSlots.push(detail.slot);
      }

      if (scaleSlider) scaleSlider.value = "100";
      updateScaleVisual();
      modal.showModal();
    }

    document.addEventListener("carrier-sheet-open", function (ev) {
      const d = ev.detail;
      if (!d || d.kind !== "upload") return;
      openUploadStudio(d);
    });

    if (scaleSlider) {
      scaleSlider.addEventListener("input", updateScaleVisual);
    }

    document.querySelectorAll("[data-open-sheet-editor]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        const m = btn.getAttribute("data-open-sheet-editor");
        if (!m) return;
        openEditor(m);
      });
    });

    function closeSheet() {
      modal.close();
      destroyAllCroppers();
      sheet.innerHTML = "";
      scaleWrapEl = null;
      if (modeBanner) {
        modeBanner.hidden = true;
        modeBanner.textContent = "";
      }
    }

    document.querySelectorAll("[data-sheet-close]").forEach(function (b) {
      b.addEventListener("click", closeSheet);
    });

    if (saveBtn) {
      saveBtn.addEventListener("click", async function () {
        const scaleV = scaleSlider ? parseInt(scaleSlider.value, 10) || 100 : 100;
        const rs = scaleV === 100 ? "" : String(scaleV / 100);
        const headers = {
          "Content-Type": "application/x-www-form-urlencoded",
          "X-Carrier-Xhr": "1",
        };
        try {
          for (let i = 0; i < activeSlots.length; i++) {
            const slot = activeSlots[i];
            const cp = croppers[slot];
            if (!cp) {
              alert("Espera a que carguen las imágenes de la vista previa antes de guardar.");
              return;
            }
            const body = new URLSearchParams();
            body.set("slot", slot);
            body.set("crop_norm_json", cropNormFromCropper(cp));
            body.set("render_scale", rs);
            const res = await fetch(slotMetaUrl, { method: "POST", headers: headers, body: body.toString(), credentials: "same-origin" });
            const js = await res.json().catch(function () {
              return {};
            });
            if (!res.ok || !js.ok) throw new Error("save");
          }
          window.location.reload();
        } catch (e) {
          alert("No se pudieron guardar los cambios.");
        }
      });
    }

    if (uploadBtn) {
      uploadBtn.addEventListener("click", function () {
        const form = document.getElementById("carrier-anexo-form");
        const cropJson = document.getElementById("carrier-anexo-crop-json");
        const rsH = document.getElementById("carrier-anexo-render-scale");
        const fileInput = document.getElementById("carrier-anexo-file");
        if (!form || !uploadState || !fileInput || !cropJson || !rsH) return;

        const scaleV = scaleSlider ? parseInt(scaleSlider.value, 10) || 100 : 100;
        rsH.value = scaleV === 100 ? "" : String(scaleV / 100);

        if (uploadPdfOnly) {
          cropJson.value = "";
        } else {
          const slot = uploadState.slot;
          const cp = croppers[slot];
          if (!cp) {
            alert("Espera a que cargue la imagen.");
            return;
          }
          cropJson.value = cropNormFromCropper(cp);
        }

        const dt = new DataTransfer();
        dt.items.add(uploadState.file);
        fileInput.files = dt.files;
        form.action = uploadState.uploadUrl;
        closeSheet();
        form.submit();
      });
    }
  })();
})();
