(function () {
  function pad2(n) {
    return String(n).padStart(2, "0");
  }

  function addHoursToTimeStr(s, hoursToAdd) {
    if (!s) return "";
    const parts = s.split(":");
    const h = parseInt(parts[0], 10) || 0;
    const m = parseInt(parts[1], 10) || 0;
    const sec = parseInt(parts[2], 10) || 0;
    let t = h * 3600 + m * 60 + sec + hoursToAdd * 3600;
    t = ((t % 86400) + 86400) % 86400;
    const nh = Math.floor(t / 3600);
    const nm = Math.floor((t % 3600) / 60);
    const ns = t % 60;
    return pad2(nh) + ":" + pad2(nm) + ":" + pad2(ns);
  }

  function tabSetup() {
    var tabs = document.querySelectorAll(".em-tab");
    var panels = {
      orina: document.getElementById("em-panel-orina"),
      sangre: document.getElementById("em-panel-sangre"),
      imc: document.getElementById("em-panel-imc"),
    };
    function activate(name) {
      Object.keys(panels).forEach(function (k) {
        var p = panels[k];
        if (!p) return;
        if (k === name) {
          p.hidden = false;
          p.classList.add("em-panel-active");
        } else {
          p.hidden = true;
          p.classList.remove("em-panel-active");
        }
      });
      tabs.forEach(function (btn) {
        var on = btn.getAttribute("data-tab") === name;
        btn.classList.toggle("em-tab-active", on);
        btn.setAttribute("aria-selected", on ? "true" : "false");
      });
    }
    tabs.forEach(function (btn) {
      btn.addEventListener("click", function () {
        activate(btn.getAttribute("data-tab") || "orina");
      });
    });
    activate("orina");
  }

  function formDataJson(form) {
    var fd = new FormData(form);
    var o = {};
    fd.forEach(function (v, k) {
      o[k] = typeof v === "string" ? v.trim() : v;
    });
    return o;
  }

  function postExport(url, body, filenameHint) {
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
      if (!res.ok) {
        return Promise.reject({ error: "Error de red (" + res.status + ")" });
      }
      return res.blob().then(function (blob) {
        var dispo = res.headers.get("Content-Disposition") || "";
        var m = /filename\*=UTF-8''([^;]+)|filename="([^"]+)"|filename=([^;]+)/i.exec(dispo);
        var name = filenameHint;
        if (m) {
          name = decodeURIComponent((m[1] || m[2] || m[3] || "").trim());
        }
        var a = document.createElement("a");
        a.href = URL.createObjectURL(blob);
        a.download = name || filenameHint;
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

  function wireOrina() {
    var form = document.getElementById("form-orina");
    var msg = document.getElementById("em-orina-msg");
    if (!form) return;
    form.addEventListener("submit", function (ev) {
      ev.preventDefault();
      var btn = ev.submitter;
      var fmt = (btn && btn.getAttribute("data-format")) || "pdf";
      var data = formDataJson(form);
      data.format = fmt;
      showMsg(msg, "Generando…", false);
      postExport(window.__emOrinaUrl || ".", data, "examen." + fmt)
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
  }

  function wireSangre() {
    var form = document.getElementById("form-sangre");
    var msg = document.getElementById("em-sangre-msg");
    var horaToma = form && form.querySelector('input[name="hora_toma"]');
    var horaVal = form && form.querySelector('input[name="hora_val"]');
    if (horaToma && horaVal) {
      horaToma.addEventListener("change", function () {
        horaVal.value = addHoursToTimeStr(horaToma.value, 4);
      });
    }
    if (!form) return;
    form.addEventListener("submit", function (ev) {
      ev.preventDefault();
      var fmt = (ev.submitter && ev.submitter.getAttribute("data-format")) || "pdf";
      var data = formDataJson(form);
      data.format = fmt;
      if (data.codigo_barra) data.codigo_barra = String(data.codigo_barra).toUpperCase();
      showMsg(msg, "Generando…", false);
      postExport(window.__emSangreUrl || ".", data, "examen." + fmt)
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
  }

  function wireImc() {
    var form = document.getElementById("form-imc");
    var msg = document.getElementById("em-imc-msg");
    var box = document.getElementById("em-imc-result");
    var vEl = document.getElementById("em-imc-valor");
    var cEl = document.getElementById("em-imc-clas");
    if (!form) return;
    form.addEventListener("submit", function (ev) {
      ev.preventDefault();
      showMsg(msg, "Guardando…", false);
      var data = formDataJson(form);
      fetch(window.__emImcUrl || ".", {
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
        .then(function (_ref) {
          var j = _ref.j;
          if (!j.ok) {
            var t =
              j.errors && j.errors.length ? j.errors.join(" ") : j.error || "Error";
            showMsg(msg, t, true);
            return;
          }
          showMsg(msg, "Registrado en historial.", false);
          if (box && vEl && cEl) {
            vEl.textContent = String(j.imc);
            cEl.textContent = String(j.clasificacion || "");
            box.hidden = false;
          }
        })
        .catch(function () {
          showMsg(msg, "Error de red.", true);
        });
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    tabSetup();
    wireOrina();
    wireSangre();
    wireImc();
  });
})();
