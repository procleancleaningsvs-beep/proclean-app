/**
 * Carrier > Cursos: pegar imagen desde portapapeles en el apartado seleccionado.
 * Requiere permisos del navegador; es opcional.
 */
(function () {
  const wrap = document.querySelector("[data-carrier-paste-wrap]");
  if (!wrap) return;

  document.addEventListener("paste", function (ev) {
    const items = ev.clipboardData && ev.clipboardData.items;
    if (!items || !items.length) return;

    const active = document.querySelector('input[name="carrier_active_slot"]:checked');
    if (!active) return;

    const slot = active.value;
    let file = null;
    for (let i = 0; i < items.length; i++) {
      const it = items[i];
      if (it.kind === "file") {
        const f = it.getAsFile();
        if (f && f.type && f.type.indexOf("image/") === 0) {
          file = f;
          break;
        }
      }
    }
    if (!file) return;

    ev.preventDefault();
    const input = document.querySelector('input[type="file"][data-carrier-slot-input="' + slot + '"]');
    if (!input) {
      window.alert("Este apartado ya tiene archivo. Usa «Reemplazar archivo» o elige otro apartado.");
      return;
    }
    const dt = new DataTransfer();
    const ext = (file.type.split("/")[1] || "png").replace("jpeg", "jpg");
    const renamed = new File([file], "pegado_desde_portapapeles." + ext, { type: file.type });
    dt.items.add(renamed);
    input.files = dt.files;
    input.dispatchEvent(new Event("change", { bubbles: true }));
    window.alert("Imagen pegada en «" + slot + "». Pulsa Subir para guardarla.");
  });
})();
