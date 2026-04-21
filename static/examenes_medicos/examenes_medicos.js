(function () {
  function pad2(n) {
    return String(n).padStart(2, "0");
  }

  function addHoursToTimeStr(s, hoursToAdd) {
    if (!s) return "";
    var parts = s.split(":");
    var h = parseInt(parts[0], 10) || 0;
    var m = parseInt(parts[1], 10) || 0;
    var sec = parseInt(parts[2], 10) || 0;
    var t = h * 3600 + m * 60 + sec + hoursToAdd * 3600;
    t = ((t % 86400) + 86400) % 86400;
    var nh = Math.floor(t / 3600);
    var nm = Math.floor((t % 3600) / 60);
    var ns = t % 60;
    return pad2(nh) + ":" + pad2(nm) + ":" + pad2(ns);
  }

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
    var msg = document.getElementById("em-msg");
    var fnac = document.getElementById("em-fnac");
    var edadEl = document.getElementById("em-edad");
    var peso = document.getElementById("em-peso");
    var est = document.getElementById("em-estatura");
    var imcVal = document.getElementById("em-imc-val");
    var imcClas = document.getElementById("em-imc-clas");
    var horaToma = form.querySelector('input[name="hora_toma"]');
    var horaVal = form.querySelector('input[name="hora_val"]');

    function syncEdad() {
      edadEl.value = edadDesdeFnac(fnac.value);
    }
    function syncImc() {
      var p = parseFloat(String(peso.value).replace(",", "."));
      var e = parseFloat(String(est.value).replace(",", "."));
      if (!(p > 0) || !(e > 0)) {
        imcVal.value = "";
        imcClas.value = "";
        return;
      }
      var imc = p / (e * e);
      imcVal.value = imc.toFixed(2);
      imcClas.value = clasificarImc(imc);
    }

    ["input", "change"].forEach(function (ev) {
      fnac.addEventListener(ev, syncEdad);
      peso.addEventListener(ev, syncImc);
      est.addEventListener(ev, syncImc);
    });
    syncEdad();
    syncImc();

    if (horaToma && horaVal) {
      horaToma.addEventListener("change", function () {
        horaVal.value = addHoursToTimeStr(horaToma.value, 4);
      });
    }

    document.getElementById("em-btn-preview")?.addEventListener("click", function () {
      var sexoEl = document.getElementById("em-sexo");
      var sexo = sexoEl ? sexoEl.value : "Mujer";
      var u = window.__emClinicalPreviewUrl + "?sexo=" + encodeURIComponent(sexo || "Mujer");
      fetch(u, { credentials: "same-origin" })
        .then(function (r) {
          return r.json();
        })
        .then(function (j) {
          var pre = document.getElementById("em-clinical-json");
          if (!pre) return;
          pre.hidden = false;
          pre.textContent = JSON.stringify(j.bundle, null, 2);
        })
        .catch(function () {
          showMsg(msg, "No se pudo cargar la vista previa clínica.", true);
        });
    });

    function fillIdPreview() {
      var data = formDataObject(form);
      var n = data.nombres;
      var a = data.apellidos;
      var dl = document.getElementById("em-id-dl");
      if (!dl) return;
      if (!n || !a) {
        dl.innerHTML = "";
        return;
      }
      fetch(window.__emPreviewIdsUrl, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ nombres: n, apellidos: a }),
        credentials: "same-origin",
      })
        .then(function (r) {
          return r.json();
        })
        .then(function (j) {
          if (!j.ok) {
            dl.innerHTML = "";
            return;
          }
          var rows = [
            ["Código de barras (estable)", j.codigo_barra],
            ["Número de cliente (si ya existe)", j.cliente_numero_existente || "— (nuevo al descargar)"],
            ["Folios", j.nota_folios || ""],
          ];
          dl.innerHTML = rows
            .map(function (r) {
              return "<dt>" + r[0] + "</dt><dd>" + String(r[1]) + "</dd>";
            })
            .join("");
        })
        .catch(function () {
          showMsg(msg, "No se pudo cargar la vista previa de IDs.", true);
        });
    }

    document.getElementById("em-btn-preview-ids")?.addEventListener("click", fillIdPreview);

    var backdrop = document.getElementById("em-modal-backdrop");
    var modal = document.getElementById("em-modal");
    function openModal() {
      if (backdrop) backdrop.hidden = false;
      if (modal) {
        modal.hidden = false;
      }
    }
    function closeModal() {
      if (backdrop) backdrop.hidden = true;
      if (modal) modal.hidden = true;
    }

    document.getElementById("em-open-modal")?.addEventListener("click", openModal);
    document.getElementById("em-modal-cancel")?.addEventListener("click", closeModal);
    backdrop?.addEventListener("click", closeModal);

    document.getElementById("em-modal-confirm")?.addEventListener("click", function () {
      var scopeEl = document.querySelector('input[name="em_scope"]:checked');
      var fmtEl = document.querySelector('input[name="em_format"]:checked');
      var scope = (scopeEl && scopeEl.value) || "both";
      var format = (fmtEl && fmtEl.value) || "pdf";
      var data = formDataObject(form);
      data.scope = scope;
      data.format = format;
      showMsg(msg, "Generando…", false);
      closeModal();
      var ext = format === "docx" ? ".docx" : scope === "both" ? ".zip" : ".pdf";
      postDownload(window.__emDownloadUrl, data, "examenes" + ext)
        .then(function () {
          showMsg(msg, "Descarga iniciada. Revise el historial para los registros guardados.", false);
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
