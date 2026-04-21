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

  function postBlob(url, body, fallbackName) {
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
    var nombres = document.getElementById("em-nombres");
    var apellidos = document.getElementById("em-apellidos");
    var nombreCompleto = document.getElementById("em-nombre-completo");
    var fnac = document.getElementById("em-fnac");
    var edadEl = document.getElementById("em-edad");
    var peso = document.getElementById("em-peso");
    var est = document.getElementById("em-estatura");
    var imcVal = document.getElementById("em-imc-val");
    var imcClas = document.getElementById("em-imc-clas");
    var horaToma = form.querySelector('input[name="hora_toma"]');
    var horaVal = form.querySelector('input[name="hora_val"]');

    function syncNombre() {
      nombreCompleto.value = [nombres.value, apellidos.value].filter(Boolean).join(" ").trim();
    }
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
      nombres.addEventListener(ev, syncNombre);
      apellidos.addEventListener(ev, syncNombre);
      fnac.addEventListener(ev, syncEdad);
      peso.addEventListener(ev, syncImc);
      est.addEventListener(ev, syncImc);
    });
    syncNombre();
    syncEdad();
    syncImc();

    if (horaToma && horaVal) {
      horaToma.addEventListener("change", function () {
        horaVal.value = addHoursToTimeStr(horaToma.value, 4);
      });
    }

    document.querySelectorAll("[data-export]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        var target = btn.getAttribute("data-export");
        var format = btn.getAttribute("data-format") || "pdf";
        var data = formDataObject(form);
        data.target = target;
        data.format = format;
        if (data.codigo_barra) data.codigo_barra = String(data.codigo_barra).toUpperCase();
        showMsg(msg, "Generando…", false);
        var ext = format === "docx" ? ".docx" : ".pdf";
        postBlob(window.__emExportUrl, data, "examen" + ext)
          .then(function () {
            showMsg(msg, "Descarga iniciada.", false);
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

    document.getElementById("em-btn-imc-save")?.addEventListener("click", function () {
      var data = formDataObject(form);
      if (data.codigo_barra) data.codigo_barra = String(data.codigo_barra).toUpperCase();
      showMsg(msg, "Guardando IMC…", false);
      fetch(window.__emImcUrl, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(data),
        credentials: "same-origin",
      })
        .then(function (r) {
          return r.json().then(function (j) {
            return { ok: r.ok, j: j };
          });
        })
        .then(function (x) {
          if (!x.j.ok) {
            var t =
              x.j.errors && x.j.errors.length ? x.j.errors.join(" ") : x.j.error || "Error";
            showMsg(msg, t, true);
            return;
          }
          showMsg(msg, "IMC guardado en historial (#" + x.j.id + ").", false);
        })
        .catch(function () {
          showMsg(msg, "Error de red.", true);
        });
    });

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
          showMsg(msg, "No se pudo cargar la vista previa.", true);
        });
    });
  });
})();
