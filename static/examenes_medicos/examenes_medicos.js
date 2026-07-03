(function () {
  function formDataObject(form) {
    var fd = new FormData(form);
    var o = {};
    fd.forEach(function (v, k) {
      o[k] = typeof v === "string" ? v.trim() : v;
    });
    return o;
  }

  function edadDesdeFnac(iso) {
    if (!iso || iso.length < 10) return "";
    var p = iso.slice(0, 10).split("-");
    if (p.length !== 3) return "";
    var y = parseInt(p[0], 10);
    var mo = parseInt(p[1], 10) - 1;
    var d = parseInt(p[2], 10);
    var fn = new Date(y, mo, d);
    if (isNaN(fn.getTime())) return "";
    var today = new Date();
    var age = today.getFullYear() - fn.getFullYear();
    var m = today.getMonth() - fn.getMonth();
    if (m < 0 || (m === 0 && today.getDate() < fn.getDate())) age--;
    return age >= 0 ? String(age) : "";
  }

  function clasificarImc(imc) {
    if (!(imc > 0)) return "";
    if (imc < 18.5) return "Bajo peso";
    if (imc < 25) return "Normal";
    if (imc < 30) return "Sobrepeso";
    return "Obesidad";
  }

  function postDownload(url, body, fallbackName) {
    return fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/octet-stream" },
      body: JSON.stringify(body),
      credentials: "same-origin",
    }).then(function (res) {
      var ct = res.headers.get("Content-Type") || "";
      if (ct.indexOf("application/json") !== -1) {
        return res.json().then(function (j) {
          return Promise.reject(j);
        });
      }
      if (!res.ok) return Promise.reject({ error: "Error " + res.status });
      var expedienteId = res.headers.get("X-Examenes-Expediente-Id") || "";
      return res.blob().then(function (blob) {
        var dispo = res.headers.get("Content-Disposition") || "";
        var m = /filename\*=UTF-8''([^;]+)|filename="([^"]+)"|filename=([^;]+)/i.exec(dispo);
        var name = fallbackName;
        if (m) name = decodeURIComponent((m[1] || m[2] || m[3] || "").trim());
        var a = document.createElement("a");
        a.href = URL.createObjectURL(blob);
        a.download = name || fallbackName;
        document.body.appendChild(a);
        a.click();
        a.remove();
        setTimeout(function () {
          URL.revokeObjectURL(a.href);
        }, 2000);
        return { expedienteId: expedienteId };
      });
    });
  }

  function showMsg(el, text, isError) {
    if (!el) return;
    el.hidden = !text;
    el.textContent = text || "";
    el.classList.toggle("em-error", !!isError);
  }

  document.addEventListener("DOMContentLoaded", function () {
    var form = document.getElementById("form-master");
    if (!form) return;

    var msg = document.getElementById("em-msg");
    var fnac = document.getElementById("em-fnac");
    var edadEl = document.getElementById("em-edad");
    var expedienteIdInput = document.getElementById("em-expediente-id");

    function syncEdad() {
      edadEl.value = edadDesdeFnac(fnac.value);
    }
    ["input", "change"].forEach(function (ev) {
      fnac.addEventListener(ev, syncEdad);
    });
    syncEdad();

    var backdrop = document.getElementById("em-modal-backdrop");
    var modal = document.getElementById("em-modal");
    var scopeState = "unificado";
    var formatState = "pdf";

    function syncSegActive(container, attr, value) {
      if (!container) return;
      var btns = container.querySelectorAll(".em-seg-btn");
      btns.forEach(function (b) {
        var on = b.getAttribute(attr) === value;
        b.setAttribute("aria-pressed", on ? "true" : "false");
        b.classList.toggle("is-active", on);
      });
    }

    function wireSeg(container, attr, initial, onPick) {
      if (!container) return;
      syncSegActive(container, attr, initial);
      container.addEventListener("click", function (e) {
        var t = e.target.closest(".em-seg-btn");
        if (!t || !container.contains(t)) return;
        var v = t.getAttribute(attr);
        if (!v) return;
        onPick(v);
        syncSegActive(container, attr, v);
      });
    }

    var seg2 = modal && modal.querySelector(".em-seg-2");
    wireSeg(seg2, "data-em-format", formatState, function (v) {
      formatState = v;
    });

    function openModal() {
      if (!form.reportValidity()) return;
      if (backdrop) backdrop.hidden = false;
      if (modal) modal.hidden = false;
    }
    function closeModal() {
      if (backdrop) backdrop.hidden = true;
      if (modal) modal.hidden = true;
    }

    document.getElementById("em-open-modal")?.addEventListener("click", openModal);
    document.getElementById("em-modal-cancel")?.addEventListener("click", closeModal);
    backdrop?.addEventListener("click", closeModal);

    document.getElementById("em-modal-confirm")?.addEventListener("click", function () {
      var data = formDataObject(form);
      data.scope = scopeState;
      data.format = formatState;
      data.confirmar_generacion = true;
      showMsg(msg, "Generando…", false);
      closeModal();
      var ext = formatState === "docx" ? ".docx" : ".pdf";
      postDownload(window.__emDownloadUrl, data, "examenes" + ext)
        .then(function (result) {
          if (result && result.expedienteId && expedienteIdInput) {
            expedienteIdInput.value = result.expedienteId;
          }
          showMsg(msg, "Descarga iniciada. El historial del paciente se actualizó automáticamente.", false);
        })
        .catch(function (err) {
          var t =
            err && err.errors && err.errors.length
              ? err.errors.join(" ")
              : (err && err.error) || "No se pudo generar.";
          showMsg(msg, t, true);
        });
    });
  });
})();
