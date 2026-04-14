/**
 * Carrier > Cursos: modal de anexos con vista previa, Cropper (imágenes) y escala (PDF/imagen).
 */
(function () {
  const root = document.querySelector("[data-carrier-workspace]");
  const modal = document.getElementById("carrier-anexo-modal");
  const form = document.getElementById("carrier-anexo-form");
  if (!root || !modal || !form || !window.HTMLDialogElement) return;

  let cropperInstance = null;
  let currentKind = null;

  const uploadPrefix = (root.getAttribute("data-upload-prefix") || "").replace(/\/+$/, "") + "/";
  const fileInput = document.getElementById("carrier-anexo-file");
  const previewWrap = document.getElementById("carrier-anexo-preview-wrap");
  const preview = document.getElementById("carrier-anexo-preview");
  const cropJson = document.getElementById("carrier-anexo-crop-json");
  const renderScaleHidden = document.getElementById("carrier-anexo-render-scale");
  const scaleSlider = document.getElementById("carrier-render-scale-slider");
  const scaleVal = document.getElementById("carrier-render-scale-val");
  const tools = document.getElementById("carrier-anexo-tools");
  const titleEl = document.getElementById("carrier-anexo-title");
  const dz = document.getElementById("carrier-anexo-dropzone");

  function destroyCropper() {
    if (cropperInstance) {
      try {
        cropperInstance.destroy();
      } catch (e) {
        /* ignore */
      }
      cropperInstance = null;
    }
  }

  function revokePreviewUrls() {
    preview.querySelectorAll("img, iframe").forEach(function (el) {
      const u = el.src;
      if (u && u.indexOf("blob:") === 0) URL.revokeObjectURL(u);
    });
  }

  function resetScaleUI() {
    if (scaleSlider) scaleSlider.value = "100";
    if (scaleVal) scaleVal.textContent = "100%";
    if (renderScaleHidden) renderScaleHidden.value = "";
  }

  function updateRenderScaleFromSlider() {
    const v = scaleSlider ? parseInt(scaleSlider.value, 10) || 100 : 100;
    if (scaleVal) scaleVal.textContent = v + "%";
    if (renderScaleHidden) {
      if (v === 100) renderScaleHidden.value = "";
      else renderScaleHidden.value = String(v / 100);
    }
  }

  function resetModal() {
    destroyCropper();
    revokePreviewUrls();
    preview.innerHTML = "";
    previewWrap.hidden = true;
    if (tools) tools.hidden = true;
    cropJson.value = "";
    resetScaleUI();
    form.reset();
    currentKind = null;
  }

  function showPreview(file) {
    destroyCropper();
    revokePreviewUrls();
    preview.innerHTML = "";
    resetScaleUI();
    if (!file) {
      previewWrap.hidden = true;
      if (tools) tools.hidden = true;
      currentKind = null;
      return;
    }
    const type = file.type || "";
    if (type === "application/pdf") {
      currentKind = "pdf";
      const url = URL.createObjectURL(file);
      const iframe = document.createElement("iframe");
      iframe.className = "carrier-anexo-preview-iframe";
      iframe.src = url;
      iframe.title = "Vista previa PDF";
      preview.appendChild(iframe);
      previewWrap.hidden = false;
      if (tools) tools.hidden = false;
      return;
    }
    if (type.indexOf("image/") === 0) {
      currentKind = "image";
      const url = URL.createObjectURL(file);
      const img = document.createElement("img");
      img.src = url;
      img.alt = "Vista previa para recorte";
      img.className = "carrier-anexo-crop-target";
      preview.appendChild(img);
      previewWrap.hidden = false;
      if (tools) tools.hidden = false;
      if (typeof window.Cropper === "function") {
        cropperInstance = new window.Cropper(img, {
          viewMode: 1,
          dragMode: "move",
          autoCropArea: 1,
          restore: false,
          guides: true,
          center: true,
          highlight: false,
          background: true,
          movable: true,
          rotatable: false,
          scalable: false,
          zoomable: true,
          zoomOnTouch: true,
          zoomOnWheel: true,
          wheelZoomRatio: 0.12,
          cropBoxMovable: true,
          cropBoxResizable: true,
          toggleDragModeOnDblclick: false,
        });
      }
      return;
    }
    currentKind = null;
    previewWrap.hidden = true;
    if (tools) tools.hidden = true;
  }

  if (scaleSlider) {
    scaleSlider.addEventListener("input", updateRenderScaleFromSlider);
  }

  document.querySelectorAll("[data-open-anexo-modal]").forEach(function (btn) {
    btn.addEventListener("click", function () {
      const slot = btn.getAttribute("data-slot");
      if (!slot || !uploadPrefix || uploadPrefix === "/") return;
      form.action = uploadPrefix + encodeURIComponent(slot);
      const t = btn.getAttribute("data-slot-title");
      if (titleEl) titleEl.textContent = t && t.trim() ? t.trim() : "Subir anexo";
      resetModal();
      modal.showModal();
      setTimeout(function () {
        if (fileInput) fileInput.focus();
      }, 50);
    });
  });

  document.querySelectorAll("[data-anexo-close]").forEach(function (b) {
    b.addEventListener("click", function () {
      modal.close();
      resetModal();
    });
  });

  if (fileInput) {
    fileInput.addEventListener("change", function () {
      const f = fileInput.files && fileInput.files[0];
      showPreview(f || null);
    });
  }

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
      showPreview(f);
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
        showPreview(f);
        break;
      }
    }
  });

  form.addEventListener("submit", function () {
    if (currentKind === "image" && cropperInstance) {
      const d = cropperInstance.getData();
      const imgData = cropperInstance.getImageData();
      const nw = imgData.naturalWidth;
      const nh = imgData.naturalHeight;
      if (nw > 0 && nh > 0 && d && typeof d.x === "number") {
        const x0 = Math.max(0, Math.min(1, d.x / nw));
        const y0 = Math.max(0, Math.min(1, d.y / nh));
        const x1 = Math.max(0, Math.min(1, (d.x + d.width) / nw));
        const y1 = Math.max(0, Math.min(1, (d.y + d.height) / nh));
        cropJson.value = JSON.stringify([x0, y0, x1, y1]);
      }
    } else {
      cropJson.value = "";
    }
    updateRenderScaleFromSlider();
  });
})();
