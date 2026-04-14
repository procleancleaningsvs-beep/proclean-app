/**
 * Modal de paquete mensual Carrier > Cursos: sincroniza AAAA-MM y abre/cierra <dialog>.
 */
(function () {
  const dlg = document.getElementById("carrier-paquete-modal");
  if (!dlg || !window.HTMLDialogElement) return;

  const form = document.getElementById("carrier-paquete-form");
  const hiddenYm = document.getElementById("carrier-paquete-ym-hidden");
  const selMonth = document.getElementById("carrier-pkg-month");
  const selYear = document.getElementById("carrier-pkg-year");
  const isAdmin = dlg.getAttribute("data-is-admin") === "1";
  const operationalYm = (dlg.getAttribute("data-operational-ym") || "").trim();
  const capYm = (dlg.getAttribute("data-cap-ym") || "").trim();

  function pad2(n) {
    return (n < 10 ? "0" : "") + n;
  }

  function syncHiddenYmFromSelects() {
    if (!isAdmin || !selMonth || !selYear || !hiddenYm) return;
    const y = parseInt(selYear.value, 10);
    const m = parseInt(selMonth.value, 10);
    if (!y || !m) return;
    hiddenYm.value = y + "-" + pad2(m);
  }

  function parseYm(s) {
    const p = /^(\d{4})-(\d{2})$/.exec((s || "").trim());
    if (!p) return null;
    return { y: parseInt(p[1], 10), m: parseInt(p[2], 10) };
  }

  function ymKey(ym) {
    const p = parseYm(ym);
    if (!p) return null;
    return p.y * 12 + (p.m - 1);
  }

  function applyDefaultYm(ym) {
    const parsed = parseYm(ym || operationalYm);
    if (!hiddenYm) return;
    if (!isAdmin) {
      hiddenYm.value = operationalYm || ym || "";
      return;
    }
    if (!parsed || !selMonth || !selYear) {
      hiddenYm.value = ym || operationalYm || "";
      return;
    }
    selYear.value = String(parsed.y);
    selMonth.value = pad2(parsed.m);
    hiddenYm.value = parsed.y + "-" + pad2(parsed.m);
  }

  document.querySelectorAll("[data-open-paquete-modal]").forEach(function (btn) {
    btn.addEventListener("click", function () {
      const def = (btn.getAttribute("data-default-ym") || operationalYm || "").trim();
      applyDefaultYm(def);
      dlg.showModal();
    });
  });

  document.querySelectorAll("[data-close-paquete-modal]").forEach(function (btn) {
    btn.addEventListener("click", function () {
      dlg.close();
    });
  });

  if (selMonth) selMonth.addEventListener("change", syncHiddenYmFromSelects);
  if (selYear) selYear.addEventListener("change", syncHiddenYmFromSelects);

  if (form) {
    form.addEventListener("submit", function (ev) {
      if (isAdmin) syncHiddenYmFromSelects();
      if (isAdmin && capYm && hiddenYm) {
        const k = ymKey(hiddenYm.value);
        const ck = ymKey(capYm);
        if (k != null && ck != null && k > ck) {
          ev.preventDefault();
          window.alert(
            "Ese mes de pago aún no es utilizable como paquete (tope: mes calendario anterior al actual)."
          );
        }
      }
    });
  }
})();
