/**
 * Hoja carta (612×792) con Fabric.js:
 * - Modo normal: mover + redimensionar con manijas.
 * - Modo recorte: ajustar bordes efectivos de la imagen (tipo Word).
 */
(function (global) {
  var LW = 612;
  var LH = 792;
  var HANDLE_RADIUS = 6;
  var MIN_CROP_NORM = 0.001;

  function clamp(x, a, b) {
    return Math.max(a, Math.min(b, x));
  }

  function FabricLetterEditor(hostEl, options) {
    this.hostEl = hostEl;
    this.options = options || {};
    this.canvas = null;
    this.fabricImg = null;
    this.cropGuides = [];
    this.cropHandles = [];
    this.cropShadows = [];
    this.cropMode = false;
    this.baseFit = 1;
    this._appliedNorm = [0, 0, 1, 1];
    this._normBeforeCrop = [0, 0, 1, 1];
    this._pendingMeta = null;
    this._dblHandler = null;
    this._keydownHandler = null;
    this._onObjectMovingBound = this._onObjectMoving.bind(this);
  }

  FabricLetterEditor.prototype.dispose = function () {
    if (this.canvas && this._dblHandler) {
      this.canvas.off("mouse:dblclick", this._dblHandler);
    }
    if (this._keydownHandler) {
      document.removeEventListener("keydown", this._keydownHandler);
      this._keydownHandler = null;
    }
    if (this.canvas) {
      this.canvas.off("object:moving", this._onObjectMovingBound);
      try {
        this.canvas.dispose();
      } catch (e) {
        /* ignore */
      }
      this.canvas = null;
    }
    this.fabricImg = null;
    this.cropGuides = [];
    this.cropHandles = [];
    this.cropShadows = [];
    this.cropMode = false;
    if (this.hostEl) this.hostEl.innerHTML = "";
  };

  FabricLetterEditor.prototype._refreshClipPath = function () {
    if (!this.fabricImg || this.cropMode) return;
    var x0 = this._appliedNorm[0];
    var y0 = this._appliedNorm[1];
    var x1 = this._appliedNorm[2];
    var y1 = this._appliedNorm[3];
    var full = x0 <= 1e-5 && y0 <= 1e-5 && x1 >= 1 - 1e-5 && y1 >= 1 - 1e-5;
    if (full) {
      this.fabricImg.set("clipPath", null);
      return;
    }
    var el = this.fabricImg.getElement();
    var ew = el.naturalWidth || this.fabricImg.width || 1;
    var eh = el.naturalHeight || this.fabricImg.height || 1;
    var clip = new fabric.Rect({
      left: -ew / 2 + x0 * ew,
      top: -eh / 2 + y0 * eh,
      width: (x1 - x0) * ew,
      height: (y1 - y0) * eh,
      absolutePositioned: false,
    });
    this.fabricImg.set("clipPath", clip);
  };

  FabricLetterEditor.prototype._normToCropRectCanvas = function () {
    var br = this.fabricImg.getBoundingRect(true);
    var x0 = this._appliedNorm[0];
    var y0 = this._appliedNorm[1];
    var x1 = this._appliedNorm[2];
    var y1 = this._appliedNorm[3];
    return {
      left: br.left + x0 * br.width,
      top: br.top + y0 * br.height,
      width: Math.max(1, (x1 - x0) * br.width),
      height: Math.max(1, (y1 - y0) * br.height),
    };
  };

  FabricLetterEditor.prototype._createGuideLine = function () {
    return new fabric.Line([0, 0, 0, 0], {
      stroke: "#0ea5e9",
      strokeWidth: 1.2,
      strokeDashArray: [5, 4],
      selectable: false,
      evented: false,
      excludeFromExport: true,
    });
  };

  FabricLetterEditor.prototype._createCropShadowRect = function () {
    return new fabric.Rect({
      left: 0,
      top: 0,
      width: 10,
      height: 10,
      fill: "rgba(18, 20, 28, 0.58)",
      stroke: null,
      selectable: false,
      evented: false,
      excludeFromExport: true,
    });
  };

  FabricLetterEditor.prototype._createHandle = function (role) {
    return new fabric.Circle({
      radius: HANDLE_RADIUS,
      fill: "#ffffff",
      stroke: "#0284c7",
      strokeWidth: 2,
      selectable: true,
      evented: true,
      hasControls: false,
      hasBorders: false,
      lockScalingX: true,
      lockScalingY: true,
      lockRotation: true,
      originX: "center",
      originY: "center",
      excludeFromExport: true,
      hoverCursor: "pointer",
      _cropHandleRole: role,
    });
  };

  FabricLetterEditor.prototype._clearCropUiObjects = function () {
    var self = this;
    if (this.canvas) {
      this.canvas.off("object:moving", this._onObjectMovingBound);
    }
    this.cropGuides.forEach(function (o) {
      if (self.canvas) self.canvas.remove(o);
    });
    this.cropHandles.forEach(function (o) {
      if (self.canvas) self.canvas.remove(o);
    });
    this.cropShadows.forEach(function (o) {
      if (self.canvas) self.canvas.remove(o);
    });
    this.cropGuides = [];
    this.cropHandles = [];
    this.cropShadows = [];
  };

  FabricLetterEditor.prototype._updateCropUiPositions = function () {
    if (!this.cropMode) return;
    var rect = this._normToCropRectCanvas();
    var x0 = rect.left;
    var y0 = rect.top;
    var x1 = rect.left + rect.width;
    var y1 = rect.top + rect.height;
    if (this.cropGuides.length === 4) {
      this.cropGuides[0].set({ x1: x0, y1: y0, x2: x1, y2: y0 });
      this.cropGuides[1].set({ x1: x1, y1: y0, x2: x1, y2: y1 });
      this.cropGuides[2].set({ x1: x0, y1: y1, x2: x1, y2: y1 });
      this.cropGuides[3].set({ x1: x0, y1: y0, x2: x0, y2: y1 });
      this.cropGuides.forEach(function (g) {
        g.setCoords();
      });
    }
    var cw = this.canvas ? this.canvas.getWidth() : LW;
    var ch = this.canvas ? this.canvas.getHeight() : LH;
    if (this.cropShadows.length === 4) {
      this.cropShadows[0].set({ left: 0, top: 0, width: cw, height: Math.max(0, y0) });
      this.cropShadows[1].set({ left: 0, top: y1, width: cw, height: Math.max(0, ch - y1) });
      this.cropShadows[2].set({ left: 0, top: y0, width: Math.max(0, x0), height: Math.max(0, y1 - y0) });
      this.cropShadows[3].set({ left: x1, top: y0, width: Math.max(0, cw - x1), height: Math.max(0, y1 - y0) });
      this.cropShadows.forEach(function (s) {
        s.setCoords();
      });
    }
    var pts = {
      t: { x: (x0 + x1) / 2, y: y0 },
      r: { x: x1, y: (y0 + y1) / 2 },
      b: { x: (x0 + x1) / 2, y: y1 },
      l: { x: x0, y: (y0 + y1) / 2 },
      tl: { x: x0, y: y0 },
      tr: { x: x1, y: y0 },
      br: { x: x1, y: y1 },
      bl: { x: x0, y: y1 },
    };
    this.cropHandles.forEach(function (h) {
      var p = pts[h._cropHandleRole];
      if (!p) return;
      h.set({ left: p.x, top: p.y });
      h.setCoords();
    });
  };

  FabricLetterEditor.prototype._setNormByHandle = function (role, px, py) {
    var br = this.fabricImg.getBoundingRect(true);
    var nx = clamp((px - br.left) / br.width, 0, 1);
    var ny = clamp((py - br.top) / br.height, 0, 1);
    var n = this._appliedNorm.slice();
    var x0 = n[0];
    var y0 = n[1];
    var x1 = n[2];
    var y1 = n[3];
    if (role.indexOf("l") >= 0) x0 = clamp(nx, 0, x1 - MIN_CROP_NORM);
    if (role.indexOf("r") >= 0) x1 = clamp(nx, x0 + MIN_CROP_NORM, 1);
    if (role.indexOf("t") >= 0) y0 = clamp(ny, 0, y1 - MIN_CROP_NORM);
    if (role.indexOf("b") >= 0) y1 = clamp(ny, y0 + MIN_CROP_NORM, 1);
    if (role === "t" || role === "b") {
      x0 = n[0];
      x1 = n[2];
    }
    if (role === "l" || role === "r") {
      y0 = n[1];
      y1 = n[3];
    }
    this._appliedNorm = [x0, y0, x1, y1];
  };

  FabricLetterEditor.prototype._onObjectMoving = function (evt) {
    if (!this.cropMode || !evt || !evt.target) return;
    var t = evt.target;
    if (t._cropHandleRole) {
      this._setNormByHandle(t._cropHandleRole, t.left, t.top);
      this._updateCropUiPositions();
      this.canvas.requestRenderAll();
      return;
    }
    if (t === this.fabricImg) {
      this._updateCropUiPositions();
      this.canvas.requestRenderAll();
    }
  };

  FabricLetterEditor.prototype._reframeAfterCrop = function () {
    if (!this.fabricImg) return;
    var n = this._appliedNorm;
    var fracW = Math.max(n[2] - n[0], MIN_CROP_NORM);
    var fracH = Math.max(n[3] - n[1], MIN_CROP_NORM);
    var el = this.fabricImg.getElement();
    var nw = el.naturalWidth || this.fabricImg.width || 1;
    var nh = el.naturalHeight || this.fabricImg.height || 1;
    var crW = fracW * nw;
    var crH = fracH * nh;
    var u = this.getUserScale();
    var left = this.fabricImg.left;
    var top = this.fabricImg.top;
    this.baseFit = Math.min((LW * 0.98) / crW, (LH * 0.98) / crH);
    this.fabricImg.set({
      left: left,
      top: top,
      scaleX: this.baseFit * u,
      scaleY: this.baseFit * u,
    });
    this.fabricImg.setCoords();
    this._refreshClipPath();
  };

  FabricLetterEditor.prototype.enterCropMode = function () {
    if (!this.fabricImg || this.cropMode) return;
    this.fabricImg.set("clipPath", null);
    this._normBeforeCrop = this._appliedNorm.slice();
    this.cropMode = true;
    this.fabricImg.set({
      selectable: true,
      evented: true,
      hasControls: false,
      lockScalingX: true,
      lockScalingY: true,
      lockRotation: true,
    });
    this.canvas.discardActiveObject();
    this.cropShadows = [
      this._createCropShadowRect(),
      this._createCropShadowRect(),
      this._createCropShadowRect(),
      this._createCropShadowRect(),
    ];
    this.cropGuides = [
      this._createGuideLine(),
      this._createGuideLine(),
      this._createGuideLine(),
      this._createGuideLine(),
    ];
    this.cropHandles = [
      this._createHandle("t"),
      this._createHandle("r"),
      this._createHandle("b"),
      this._createHandle("l"),
      this._createHandle("tl"),
      this._createHandle("tr"),
      this._createHandle("br"),
      this._createHandle("bl"),
    ];
    for (var si = 0; si < this.cropShadows.length; si++) this.canvas.add(this.cropShadows[si]);
    for (var i = 0; i < this.cropGuides.length; i++) this.canvas.add(this.cropGuides[i]);
    for (var j = 0; j < this.cropHandles.length; j++) this.canvas.add(this.cropHandles[j]);
    this._updateCropUiPositions();
    if (this.cropHandles.length) this.canvas.setActiveObject(this.cropHandles[0]);
    if (typeof this.options.onFocus === "function") {
      this.options.onFocus(this);
    }
    this.canvas.off("object:moving", this._onObjectMovingBound);
    this.canvas.on("object:moving", this._onObjectMovingBound);
    if (typeof this.options.onCropMode === "function") {
      this.options.onCropMode(true, this);
    }
    this.canvas.requestRenderAll();
  };

  FabricLetterEditor.prototype._teardownCropUI = function () {
    this._clearCropUiObjects();
    this.cropMode = false;
    if (this.fabricImg) {
      this.fabricImg.set({
        selectable: true,
        evented: true,
        hasControls: true,
        lockScalingX: false,
        lockScalingY: false,
        lockRotation: true,
      });
    }
  };

  FabricLetterEditor.prototype.applyCrop = function () {
    if (!this.cropMode || !this.fabricImg) return;
    var n = this._appliedNorm;
    if (n[2] - n[0] < MIN_CROP_NORM || n[3] - n[1] < MIN_CROP_NORM) {
      this.cancelCrop();
      return;
    }
    this._teardownCropUI();
    this._reframeAfterCrop();
    this.canvas.setActiveObject(this.fabricImg);
    if (typeof this.options.onCropMode === "function") {
      this.options.onCropMode(false, this);
    }
    this.canvas.requestRenderAll();
  };

  FabricLetterEditor.prototype.cancelCrop = function () {
    if (!this.cropMode) return;
    this._appliedNorm = (this._normBeforeCrop || [0, 0, 1, 1]).slice();
    this._teardownCropUI();
    this._refreshClipPath();
    if (this.fabricImg) this.canvas.setActiveObject(this.fabricImg);
    if (typeof this.options.onCropMode === "function") {
      this.options.onCropMode(false, this);
    }
    this.canvas.requestRenderAll();
  };

  FabricLetterEditor.prototype.applyUserScale = function (u) {
    if (!this.fabricImg || this.cropMode) return;
    var v = clamp(Number(u) || 1, 0.25, 3);
    this.fabricImg.set({ scaleX: this.baseFit * v, scaleY: this.baseFit * v });
    this.fabricImg.setCoords();
    this.canvas && this.canvas.requestRenderAll();
  };

  FabricLetterEditor.prototype.getUserScale = function () {
    if (!this.fabricImg) return 1;
    return clamp(this.fabricImg.scaleX / this.baseFit, 0.25, 3);
  };

  FabricLetterEditor.prototype.getCropNormJson = function () {
    return JSON.stringify(this._appliedNorm);
  };

  FabricLetterEditor.prototype.getRenderScaleForSave = function () {
    var v = this.getUserScale();
    if (Math.abs(v - 1) < 0.004) return "";
    return String(v);
  };

  FabricLetterEditor.prototype.isCropMode = function () {
    return this.cropMode;
  };

  FabricLetterEditor.prototype._onImageReady = function (img) {
    var self = this;
    var el = img.getElement();
    var nw = el.naturalWidth || img.width || 1;
    var nh = el.naturalHeight || img.height || 1;
    img.set({
      originX: "center",
      originY: "center",
      left: LW / 2,
      top: LH / 2,
      selectable: true,
      hasControls: true,
      lockRotation: true,
      transparentCorners: false,
      cornerColor: "#0076b8",
      borderColor: "#0076b8",
    });
    img.setControlsVisibility({ mtr: false });
    var meta = this._pendingMeta || {};
    this.baseFit = Math.min((LW * 0.98) / nw, (LH * 0.98) / nh);
    var rs = 1;
    if (meta.render_scale != null && String(meta.render_scale).trim() !== "") {
      rs = Number(meta.render_scale);
      if (isNaN(rs)) rs = 1;
      rs = clamp(rs, 0.25, 3);
    }
    img.scale(this.baseFit * rs);
    this.fabricImg = img;
    if (meta.crop_norm && meta.crop_norm.length === 4) {
      this._appliedNorm = meta.crop_norm.map(Number);
    } else {
      this._appliedNorm = [0, 0, 1, 1];
    }
    img.on("scaling", function () {
      var m = Math.max(img.scaleX || 0, img.scaleY || 0);
      img.set({ scaleX: m, scaleY: m });
    });
    img.on("modified", function () {
      if (typeof self.options.onUserScale === "function") {
        self.options.onUserScale(self.getUserScale());
      }
    });
    img.on("selected", function () {
      if (typeof self.options.onFocus === "function") {
        self.options.onFocus(self);
      }
    });
    img.on("mousedown", function () {
      if (typeof self.options.onFocus === "function") {
        self.options.onFocus(self);
      }
    });
    this._refreshClipPath();
    this.canvas.add(img);
    this.canvas.setActiveObject(img);
    if (typeof this.options.onFocus === "function") {
      this.options.onFocus(this);
    }
    this._bindDblClick();
    this._bindEsc();
    this.canvas.renderAll();
    if (typeof this.options.onReady === "function") {
      this.options.onReady(this);
    }
  };

  FabricLetterEditor.prototype._bindDblClick = function () {
    var self = this;
    this._dblHandler = function (opt) {
      var t = opt.target;
      if (!t) return;
      if (self.cropMode) {
        if (t._cropHandleRole || t === self.fabricImg) {
          self.applyCrop();
        }
        return;
      }
      if (t === self.fabricImg) {
        self.enterCropMode();
      }
    };
    this.canvas.on("mouse:dblclick", this._dblHandler);
  };

  FabricLetterEditor.prototype._bindEsc = function () {
    var self = this;
    if (this._keydownHandler) return;
    this._keydownHandler = function (ev) {
      if (ev.key === "Escape" && self.cropMode) {
        ev.preventDefault();
        self.cancelCrop();
      }
    };
    document.addEventListener("keydown", this._keydownHandler);
  };

  FabricLetterEditor.prototype.mount = function (imageUrl, meta) {
    var self = this;
    this.dispose();
    this._pendingMeta = meta || {};
    this.hostEl.innerHTML = "";
    var canvasEl = document.createElement("canvas");
    this.hostEl.appendChild(canvasEl);
    if (!global.fabric) {
      this.hostEl.textContent = "No se cargó Fabric.js.";
      return;
    }
    this.canvas = new fabric.Canvas(canvasEl, {
      width: LW,
      height: LH,
      backgroundColor: "#ffffff",
      selection: false,
      preserveObjectStacking: true,
    });
    fabric.Image.fromURL(imageUrl, function (img) {
      if (!self.canvas) return;
      self._onImageReady(img);
    });
  };

  global.CarrierFabricLetter = {
    LETTER_W: LW,
    LETTER_H: LH,
    create: function (hostEl, options) {
      return new FabricLetterEditor(hostEl, options);
    },
  };
})(typeof window !== "undefined" ? window : this);
