(function (global) {
  "use strict";

  var ICON_SIZE = 18;

  function escapeAttr(value) {
    return String(value || "")
      .replace(/&/g, "&amp;")
      .replace(/"/g, "&quot;")
      .replace(/</g, "&lt;");
  }

  global.pcLucideIcon = function (iconName) {
    return '<i data-lucide="' + escapeAttr(iconName) + '" aria-hidden="true"></i>';
  };

  global.pcLucideRefresh = function (root) {
    if (!global.lucide || typeof global.lucide.createIcons !== "function") return;
    var opts = {
      attrs: {
        width: ICON_SIZE,
        height: ICON_SIZE,
        "stroke-width": 2,
      },
    };
    if (root) opts.root = root;
    global.lucide.createIcons(opts);
  };

  global.pcIconButton = function (opts) {
    opts = opts || {};
    var icon = opts.icon || "circle";
    var title = opts.title || "";
    var variant = opts.variant ? " btn-icon--" + opts.variant : "";
    var extraClass = opts.className ? " " + opts.className : "";
    var cls = "btn-icon" + variant + extraClass;
    var attrs = opts.attrs || "";
    var label =
      ' title="' +
      escapeAttr(title) +
      '" aria-label="' +
      escapeAttr(title) +
      '"';
    var inner = global.pcLucideIcon(icon);

    if (opts.tag === "a") {
      return (
        '<a href="' +
        escapeAttr(opts.href || "#") +
        '" class="' +
        cls +
        '"' +
        label +
        " " +
        attrs +
        ">" +
        inner +
        "</a>"
      );
    }

    var type = opts.type || "button";
    var disabled = opts.disabled ? " disabled" : "";
    return (
      '<button type="' +
      escapeAttr(type) +
      '" class="' +
      cls +
      '"' +
      label +
      disabled +
      " " +
      attrs +
      ">" +
      inner +
      "</button>"
    );
  };
})(window);
