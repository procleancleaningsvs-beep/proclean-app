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

  /* ——— Historial IMSS (misma fuente que Movimientos IMSS: sin filtro por usuario) ——— */
  (function imssModal() {
    const dlg = document.getElementById("carrier-vincular-imss-modal");
    const pickDlg = document.getElementById("carrier-imss-pick-modal");
    if (!dlg || !imssJsonUrl) return;

    const tbody = document.getElementById("carrier-imss-tbody");
    const search = document.getElementById("carrier-imss-search");
    const countEl = document.getElementById("carrier-imss-count");
    const empty = document.getElementById("carrier-imss-empty");
    const pager = document.getElementById("carrier-imss-pager");
    const prevBtn = document.getElementById("carrier-imss-prev");
    const nextBtn = document.getElementById("carrier-imss-next");
    const pageLabel = document.getElementById("carrier-imss-page-label");
    const pickForm = document.getElementById("carrier-imss-pick-form");
    const pickFid = document.getElementById("carrier-imss-pick-fid");
    const pickSelect = document.getElementById("carrier-imss-pick-select");

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

    function openPickModal(rec) {
      if (!pickDlg || !pickForm || !pickFid || !pickSelect) return;
      pickForm.action = vincUrl();
      pickFid.value = String(rec.id);
      pickSelect.innerHTML = "";
      const names = rec.nombres && rec.nombres.length ? rec.nombres : [];
      const n = Math.max(1, parseInt(rec.movement_count, 10) || 1);
      for (let i = 0; i < n; i++) {
        const opt = document.createElement("option");
        opt.value = String(i);
        opt.textContent = names[i] != null && String(names[i]).trim() ? String(names[i]) : "Movimiento " + (i + 1);
        pickSelect.appendChild(opt);
      }
      if (pickDlg.showModal) pickDlg.showModal();
    }

    if (pickDlg) {
      pickDlg.querySelectorAll("[data-imss-pick-close]").forEach(function (x) {
        x.addEventListener("click", function () {
          pickDlg.close();
        });
      });
    }

    async function load() {
      if (!tbody) return;
      tbody.innerHTML = '<tr><td colspan="5" class="carrier-imss-loading">Cargando…</td></tr>';
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
          const can = rec.can_vincular !== false;
          const multi = (parseInt(rec.movement_count, 10) || 0) > 1;
          tr.innerHTML =
            "<td>" +
            esc(rec.created_at) +
            '</td><td class="history-cell-wrap">' +
            esc(rec.filename) +
            sub +
            '</td><td class="history-cell-wrap">' +
            esc(rec.username || "") +
            "</td><td>" +
            esc(String(rec.movement_count)) +
            '</td><td class="carrier-imss-actions-cell"></td>';
          const cell = tr.querySelector(".carrier-imss-actions-cell");
          const b = document.createElement("button");
          b.type = "button";
          b.className = "btn btn-primary btn-sm";
          b.textContent = "Vincular";
          if (!can) {
            b.disabled = true;
            b.title = "Solo puedes vincular constancias generadas con tu usuario.";
          } else if (multi) {
            b.addEventListener("click", function () {
              openPickModal(rec);
            });
          } else {
            b.addEventListener("click", function () {
              const f = document.createElement("form");
              f.method = "post";
              f.action = vincUrl();
              f.style.display = "none";
              f.innerHTML =
                '<input type="hidden" name="format_history_id" value="' +
                esc(String(rec.id)) +
                '"><input type="hidden" name="movimiento_idx" value="0">';
              document.body.appendChild(f);
              f.submit();
            });
          }
          cell.appendChild(b);
          tbody.appendChild(tr);
        });
      } catch (e) {
        tbody.innerHTML =
          '<tr><td colspan="5" class="carrier-imss-err">No se pudo cargar el historial.</td></tr>';
      }
    }

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

  /* ——— Hoja: Fabric.js (colocación con manijas; recorte en modo aparte) ——— */
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
    const cropActions = document.getElementById("carrier-sheet-crop-actions");
    const cropApply = document.getElementById("carrier-sheet-crop-apply");
    const cropCancel = document.getElementById("carrier-sheet-crop-cancel");
    const modeLayoutBtn = document.getElementById("carrier-sheet-mode-layout");
    const modeCropBtn = document.getElementById("carrier-sheet-mode-crop");
    if (!modal || !sheet || !slotMetaUrl) return;

    const fabricEditors = {};
    let activeSlots = [];
    let scaleWrapEl = null;
    let uploadState = null;
    let editMode = null;
    let uploadPdfOnly = false;
    let activeEditorSlot = null;
    let cropActiveEditor = null;
    let cropBtnsWired = false;

    function clampScale(u) {
      const x = Number(u) || 1;
      return Math.max(0.25, Math.min(3, x));
    }

    function syncSliderLabel(u) {
      if (scaleVal) scaleVal.textContent = Math.round(u * 100) + "%";
    }

    function getActiveEditor() {
      if (activeEditorSlot && fabricEditors[activeEditorSlot]) return fabricEditors[activeEditorSlot];
      for (let i = 0; i < activeSlots.length; i++) {
        const s = activeSlots[i];
        if (fabricEditors[s]) {
          activeEditorSlot = s;
          return fabricEditors[s];
        }
      }
      return null;
    }

    function refreshModeButtons() {
      const ed = getActiveEditor();
      const inCrop = !!(ed && ed.isCropMode && ed.isCropMode());
      if (modeLayoutBtn) modeLayoutBtn.classList.toggle("is-active", !inCrop);
      if (modeCropBtn) modeCropBtn.classList.toggle("is-active", inCrop);
    }

    function updateCropUi() {
      let any = false;
      Object.keys(fabricEditors).forEach(function (k) {
        if (fabricEditors[k] && fabricEditors[k].isCropMode()) any = true;
      });
      if (cropActions) cropActions.hidden = !any;
      if (modeLayoutBtn) modeLayoutBtn.disabled = uploadPdfOnly;
      if (modeCropBtn) modeCropBtn.disabled = uploadPdfOnly;
      if (!modeBanner) {
        refreshModeButtons();
        return;
      }
      if (uploadPdfOnly) {
        refreshModeButtons();
        return;
      }
      if (any) {
        modeBanner.hidden = false;
        modeBanner.textContent =
          "Modo recorte: el marco azul es independiente de la imagen. Ajusta y pulsa «Aplicar recorte», doble clic en la hoja, o ESC / «Cancelar».";
      } else {
        modeBanner.hidden = true;
        modeBanner.textContent = "";
      }
      refreshModeButtons();
    }

    function destroyAllFabric() {
      Object.keys(fabricEditors).forEach(function (k) {
        try {
          fabricEditors[k].dispose();
        } catch (e) {
          /* ignore */
        }
        delete fabricEditors[k];
      });
      cropActiveEditor = null;
      activeEditorSlot = null;
      if (cropActions) cropActions.hidden = true;
      if (modeBanner && !uploadPdfOnly) {
        modeBanner.hidden = true;
        modeBanner.textContent = "";
      }
    }

    function wireCropButtonsOnce() {
      if (cropBtnsWired || !cropApply || !cropCancel) return;
      cropBtnsWired = true;
      cropApply.addEventListener("click", function () {
        if (cropActiveEditor) cropActiveEditor.applyCrop();
      });
      cropCancel.addEventListener("click", function () {
        if (cropActiveEditor) cropActiveEditor.cancelCrop();
      });
    }
    wireCropButtonsOnce();

    function mountFabricInContainer(container, slot, imageUrl, meta) {
      if (fabricEditors[slot]) {
        try {
          fabricEditors[slot].dispose();
        } catch (e) {
          /* ignore */
        }
        delete fabricEditors[slot];
      }
      if (!window.CarrierFabricLetter) {
        const ph = document.createElement("div");
        ph.className = "carrier-ine-placeholder";
        ph.textContent = "No se cargó Fabric.js; recarga la página.";
        container.appendChild(ph);
        return;
      }
      const host = document.createElement("div");
      host.className = "carrier-fabric-host";
      container.appendChild(host);
      const ed = window.CarrierFabricLetter.create(host, {
        onUserScale: function (u) {
          const v = clampScale(u);
          activeEditorSlot = slot;
          if (scaleSlider) scaleSlider.value = String(Math.round(v * 100));
          syncSliderLabel(v);
        },
        onCropMode: function (on, _ed) {
          activeEditorSlot = slot;
          cropActiveEditor = on ? fabricEditors[slot] : null;
          updateCropUi();
        },
        onReady: function () {
          activeEditorSlot = slot;
          if (fabricEditors[slot]) {
            const v = fabricEditors[slot].getUserScale();
            if (scaleSlider) scaleSlider.value = String(Math.round(v * 100));
            syncSliderLabel(v);
          }
          refreshModeButtons();
        },
        onFocus: function () {
          activeEditorSlot = slot;
          if (fabricEditors[slot]) {
            const v = fabricEditors[slot].getUserScale();
            if (scaleSlider) scaleSlider.value = String(Math.round(v * 100));
            syncSliderLabel(v);
          }
          refreshModeButtons();
        },
      });
      fabricEditors[slot] = ed;
      ed.mount(imageUrl, meta || {});
    }

    function initImageSlot(container, slot, st) {
      if (!st || !st.has_file) {
        const ph = document.createElement("div");
        ph.className = "carrier-ine-placeholder";
        ph.textContent = slot.indexOf("reverso") >= 0 ? "Sin segundo archivo" : "Sin archivo";
        container.appendChild(ph);
        return;
      }
      mountFabricInContainer(container, slot, st.preview_url + "?t=" + Date.now(), {
        crop_norm: st.crop_norm,
        render_scale: st.render_scale,
      });
    }

    function initUploadImageSlot(container, slot, blobUrl) {
      mountFabricInContainer(container, slot, blobUrl, {});
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

    function applyScaleSliderToFabric() {
      const raw = scaleSlider ? parseInt(String(scaleSlider.value), 10) : 100;
      const v = clampScale((raw || 100) / 100);
      syncSliderLabel(v);
      const ed = getActiveEditor();
      if (ed) ed.applyUserScale(v);
    }

    function updatePdfScaleVisual() {
      const v = scaleSlider ? (parseInt(scaleSlider.value, 10) || 100) / 100 : 1;
      if (scaleVal) scaleVal.textContent = Math.round(v * 100) + "%";
      if (scaleWrapEl) {
        scaleWrapEl.style.transform = "scale(" + v + ")";
        scaleWrapEl.style.transformOrigin = "center center";
      }
    }

    function openEditor(mode) {
      setFooterEdit();
      editMode = mode;
      const slotsState = readSlotsState();
      destroyAllFabric();
      sheet.innerHTML = "";
      activeSlots = [];

      const inner = document.createElement("div");
      inner.className = "carrier-doc-sheet-inner";
      scaleWrapEl = inner;
      sheet.appendChild(inner);

      if (mode === "ine") {
        if (titleEl) titleEl.textContent = "Vista previa / editar — INE (misma hoja)";
        if (hintEl) {
          hintEl.textContent =
            "Misma composición que el PDF final. Cada lado se edita aparte. Doble clic en una imagen entra al recorte (marco azul).";
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
        activeEditorSlot = stL && stL.has_file ? "ine_frente" : stR && stR.has_file ? "ine_reverso" : null;
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
            "Hoja carta como en el PDF. Selecciona la imagen y usa las manijas; doble clic = modo recorte (marco aparte).";
        }
        inner.classList.add("carrier-doc-sheet-inner--single");
        const box = document.createElement("div");
        box.className = "carrier-single-canvas";
        inner.appendChild(box);
        initImageSlot(box, mode, st);
        if (st && st.has_file) activeSlots.push(mode);
        activeEditorSlot = st && st.has_file ? mode : null;
      }

      const activeState = activeEditorSlot ? slotsState[activeEditorSlot] : null;
      const rs0 = activeState && activeState.render_scale ? activeState.render_scale : null;
      const pct = rs0 ? Math.round(Number(rs0) * 100) : 100;
      if (scaleSlider) scaleSlider.value = String(Math.max(25, Math.min(300, pct)));
      applyScaleSliderToFabric();
      updateCropUi();
      modal.showModal();
    }

    function openUploadStudio(detail) {
      setFooterUpload();
      editMode = null;
      uploadState = { slot: detail.slot, file: detail.file, uploadUrl: detail.uploadUrl };
      destroyAllFabric();
      sheet.innerHTML = "";
      activeSlots = [];

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
        activeEditorSlot = null;
        if (hintEl) hintEl.textContent = "PDF: ajusta la escala con el control inferior. Se subirá el archivo completo.";
        if (modeBanner) {
          modeBanner.hidden = false;
          modeBanner.textContent =
            "Vista previa del PDF en la hoja (primera página como referencia visual en exportación se aplica al rasterizar).";
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
            "Misma experiencia que «Vista previa / editar»: manijas para escalar, arrastre para mover; doble clic = recorte.";
        }
        const url = URL.createObjectURL(f);
        initUploadImageSlot(box, detail.slot, url);
        activeSlots.push(detail.slot);
        activeEditorSlot = detail.slot;
      }

      if (scaleSlider) scaleSlider.value = "100";
      if (uploadPdfOnly) updatePdfScaleVisual();
      else applyScaleSliderToFabric();
      updateCropUi();
      modal.showModal();
    }

    document.addEventListener("carrier-sheet-open", function (ev) {
      const d = ev.detail;
      if (!d || d.kind !== "upload") return;
      openUploadStudio(d);
    });

    if (scaleSlider) {
      scaleSlider.addEventListener("input", function () {
        if (uploadPdfOnly) updatePdfScaleVisual();
        else applyScaleSliderToFabric();
      });
    }
    if (modeLayoutBtn) {
      modeLayoutBtn.addEventListener("click", function () {
        const ed = getActiveEditor();
        if (!ed) return;
        if (ed.isCropMode && ed.isCropMode()) ed.applyCrop();
        refreshModeButtons();
      });
    }
    if (modeCropBtn) {
      modeCropBtn.addEventListener("click", function () {
        const ed = getActiveEditor();
        if (!ed) return;
        if (!ed.isCropMode || !ed.isCropMode()) ed.enterCropMode();
        refreshModeButtons();
      });
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
      destroyAllFabric();
      sheet.innerHTML = "";
      scaleWrapEl = null;
      if (modeBanner) {
        modeBanner.hidden = true;
        modeBanner.textContent = "";
      }
      if (cropActions) cropActions.hidden = true;
      updateCropUi();
    }

    document.querySelectorAll("[data-sheet-close]").forEach(function (b) {
      b.addEventListener("click", closeSheet);
    });

    if (saveBtn) {
      saveBtn.addEventListener("click", async function () {
        const headers = {
          "Content-Type": "application/x-www-form-urlencoded",
          "X-Carrier-Xhr": "1",
        };
        try {
          for (let i = 0; i < activeSlots.length; i++) {
            const slot = activeSlots[i];
            const ed = fabricEditors[slot];
            if (!ed) {
              alert("Espera a que carguen las imágenes de la vista previa antes de guardar.");
              return;
            }
            const body = new URLSearchParams();
            body.set("slot", slot);
            body.set("crop_norm_json", ed.getCropNormJson());
            body.set("render_scale", ed.getRenderScaleForSave());
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

        if (uploadPdfOnly) {
          const scaleV = scaleSlider ? parseInt(scaleSlider.value, 10) || 100 : 100;
          rsH.value = scaleV === 100 ? "" : String(scaleV / 100);
          cropJson.value = "";
        } else {
          const slot = uploadState.slot;
          const ed = fabricEditors[slot];
          if (!ed) {
            alert("Espera a que cargue la imagen.");
            return;
          }
          cropJson.value = ed.getCropNormJson();
          rsH.value = ed.getRenderScaleForSave();
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
