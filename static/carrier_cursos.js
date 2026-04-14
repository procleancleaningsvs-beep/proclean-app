/**
 * Carrier > Cursos: modal de subida de anexos con vista previa (PDF o imagen).
 */
(function () {
  const root = document.querySelector("[data-carrier-workspace]");
  const modal = document.getElementById("carrier-anexo-modal");
  const form = document.getElementById("carrier-anexo-form");
  if (!root || !modal || !form || !window.HTMLDialogElement) return;

  const eid = root.getAttribute("data-expediente-id");
  const uploadPrefix = (root.getAttribute("data-upload-prefix") || "").replace(/\/+$/, "") + "/";
  const fileInput = document.getElementById("carrier-anexo-file");
  const previewWrap = document.getElementById("carrier-anexo-preview-wrap");
  const preview = document.getElementById("carrier-anexo-preview");
  const cropJson = document.getElementById("carrier-anexo-crop-json");
  const dz = document.getElementById("carrier-anexo-dropzone");

  function revokePreviewUrls() {
    preview.querySelectorAll("img, iframe").forEach(function (el) {
      const u = el.src;
      if (u && u.indexOf("blob:") === 0) URL.revokeObjectURL(u);
    });
  }

  function resetModal() {
    revokePreviewUrls();
    preview.innerHTML = "";
    previewWrap.hidden = true;
    cropJson.value = "";
    form.reset();
  }

  function showPreview(file) {
    revokePreviewUrls();
    preview.innerHTML = "";
    if (!file) {
      previewWrap.hidden = true;
      return;
    }
    const type = file.type || "";
    if (type === "application/pdf") {
      const url = URL.createObjectURL(file);
      const iframe = document.createElement("iframe");
      iframe.className = "carrier-anexo-preview-iframe";
      iframe.src = url;
      iframe.title = "Vista previa PDF";
      preview.appendChild(iframe);
      previewWrap.hidden = false;
      return;
    }
    if (type.indexOf("image/") === 0) {
      const url = URL.createObjectURL(file);
      const img = document.createElement("img");
      img.src = url;
      img.alt = "Vista previa";
      img.className = "carrier-anexo-preview-img";
      preview.appendChild(img);
      previewWrap.hidden = false;
      return;
    }
    previewWrap.hidden = true;
  }

  document.querySelectorAll("[data-open-anexo-modal]").forEach(function (btn) {
    btn.addEventListener("click", function () {
      const slot = btn.getAttribute("data-slot");
      if (!slot || !uploadPrefix || uploadPrefix === "/") return;
      form.action = uploadPrefix + encodeURIComponent(slot);
      document.getElementById("carrier-anexo-title").textContent = "Subir anexo";
      resetModal();
      modal.showModal();
      setTimeout(function () {
        fileInput.focus();
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
})();
