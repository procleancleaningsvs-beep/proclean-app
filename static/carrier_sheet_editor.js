/**
 * Hoja carta (612×792) con Fabric.js:
 * - Modo normal: mover + redimensionar con manijas.
 * - Modo recorte: rectángulo de recorte movible sobre imagen fija; UV ↔ canvas vía matriz de transformación;
 *   al aplicar se hornea el recorte (cropX/cropY/width/height) para que el bbox coincida con lo visible.
 */
(function (global) {
  var LW = 612;
  var LH = 792;
  var MIN_CROP_NORM = 0.001;
  var MIN_CROP_PX = 3;

  function clamp(x, a, b) {
    return Math.max(a, Math.min(b, x));
  }

  function FabricLetterEditor(hostEl, options) {
    this.hostEl = hostEl;
    this.options = options || {};
    this.canvas = null;
    this.fabricImg = null;
    this.cropRectObj = null;
    this.cropShadows = [];
    this.cropMode = false;
    this._cropBaked = false;
    this.baseFit = 1;
    this._appliedNorm = [0, 0, 1, 1];
    this._normBeforeCrop = [0, 0, 1, 1];
    this._pendingMeta = null;
    this._dblHandler = null;
    this._keydownHandler = null;
    this._onCropChangeBound = this._onCropChange.bind(this);
    this._bakedBeforeCropEdit = false;
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
      this.canvas.off("object:moving", this._onCropChangeBound);
      this.canvas.off("object:scaling", this._onCropChangeBound);
      this.canvas.off("object:modified", this._onCropChangeBound);
      try {
        this.canvas.dispose();
      } catch (e) {
        /* ignore */
      }
      this.canvas = null;
    }
    this.fabricImg = null;
    this.cropRectObj = null;
    this.cropShadows = [];
    this.cropMode = false;
    if (this.hostEl) this.hostEl.innerHTML = "";
  };

  FabricLetterEditor.prototype._naturalSize = function () {
    var el = this.fabricImg.getElement();
    return {
      nw: el.naturalWidth || this.fabricImg.width || 1,
      nh: el.naturalHeight || this.fabricImg.height || 1,
    };
  };

  /** Local space (origin center, y down): bitmap u,v in [0,1] → fabric.Point canvas */
  FabricLetterEditor.prototype._uvToCanvasPoint = function (u, v) {
    var sz = this._naturalSize();
    var ew = sz.nw;
    var eh = sz.nh;
    var lx = -ew / 2 + u * ew;
    var ly = -eh / 2 + v * eh;
    var m = this.fabricImg.calcTransformMatrix();
    return fabric.util.transformPoint(new fabric.Point(lx, ly), m);
  };

  /** Canvas pixel → normalized u,v en el bitmap fuente (misma base que clipPath / PDF). */
  FabricLetterEditor.prototype._canvasPointToNorm = function (cx, cy) {
    var sz = this._naturalSize();
    var ew = sz.nw;
    var eh = sz.nh;
    var inv = fabric.util.invertTransform(this.fabricImg.calcTransformMatrix());
    var p = fabric.util.transformPoint(new fabric.Point(cx, cy), inv);
    var u = (p.x + ew / 2) / ew;
    var v = (p.y + eh / 2) / eh;
    return { u: clamp(u, 0, 1), v: clamp(v, 0, 1) };
  };

  /** AABB en canvas del rectángulo UV [u0,v0]-[u1,v1] en espacio bitmap. */
  FabricLetterEditor.prototype._normUvToCanvasAabb = function (u0, v0, u1, v1) {
    var pts = [
      this._uvToCanvasPoint(u0, v0),
      this._uvToCanvasPoint(u1, v0),
      this._uvToCanvasPoint(u0, v1),
      this._uvToCanvasPoint(u1, v1),
    ];
    var xs = pts.map(function (p) {
      return p.x;
    });
    var ys = pts.map(function (p) {
      return p.y;
    });
    var l = Math.min.apply(null, xs);
    var r = Math.max.apply(null, xs);
    var t = Math.min.apply(null, ys);
    var b = Math.max.apply(null, ys);
    return { left: l, top: t, width: Math.max(MIN_CROP_PX, r - l), height: Math.max(MIN_CROP_PX, b - t) };
  };

  FabricLetterEditor.prototype._intersectCanvasRects = function (a, b) {
    var l = Math.max(a.left, b.left);
    var t = Math.max(a.top, b.top);
    var r = Math.min(a.left + a.width, b.left + b.width);
    var bot = Math.min(a.top + a.height, b.top + b.height);
    if (r - l < MIN_CROP_PX || bot - t < MIN_CROP_PX) return null;
    return { left: l, top: t, width: r - l, height: bot - t };
  };

  /** AABB canvas del recuadro → crop_norm [u0,v0,u1,v1] (min/max de las 4 esquinas en UV). */
  FabricLetterEditor.prototype._canvasRectToNorm = function (rect) {
    var corners = [
      { x: rect.left, y: rect.top },
      { x: rect.left + rect.width, y: rect.top },
      { x: rect.left, y: rect.top + rect.height },
      { x: rect.left + rect.width, y: rect.top + rect.height },
    ];
    var umin = 1;
    var umax = 0;
    var vmin = 1;
    var vmax = 0;
    for (var i = 0; i < corners.length; i++) {
      var uv = this._canvasPointToNorm(corners[i].x, corners[i].y);
      umin = Math.min(umin, uv.u);
      umax = Math.max(umax, uv.u);
      vmin = Math.min(vmin, uv.v);
      vmax = Math.max(vmax, uv.v);
    }
    if (umax - umin < MIN_CROP_NORM || vmax - vmin < MIN_CROP_NORM) return null;
    return [umin, vmin, umax, vmax];
  };

  FabricLetterEditor.prototype._refreshClipPath = function () {
    if (!this.fabricImg || this.cropMode) return;
    if (this._cropBaked) {
      this.fabricImg.set("clipPath", null);
      return;
    }
    var x0 = this._appliedNorm[0];
    var y0 = this._appliedNorm[1];
    var x1 = this._appliedNorm[2];
    var y1 = this._appliedNorm[3];
    var full = x0 <= 1e-5 && y0 <= 1e-5 && x1 >= 1 - 1e-5 && y1 >= 1 - 1e-5;
    if (full) {
      this.fabricImg.set("clipPath", null);
      return;
    }
    var sz = this._naturalSize();
    var ew = sz.nw;
    var eh = sz.nh;
    var clip = new fabric.Rect({
      left: -ew / 2 + x0 * ew,
      top: -eh / 2 + y0 * eh,
      width: (x1 - x0) * ew,
      height: (y1 - y0) * eh,
      absolutePositioned: false,
    });
    this.fabricImg.set("clipPath", clip);
  };

  /** Hornea recorte en el objeto Image (misma fuente; PDF sigue usando crop_norm sobre el archivo original). */
  FabricLetterEditor.prototype._applyBakedCropFromNorm = function () {
    if (!this.fabricImg) return;
    var n = this._appliedNorm;
    var sz = this._naturalSize();
    var nw = sz.nw;
    var nh = sz.nh;
    var cx0 = n[0] * nw;
    var cy0 = n[1] * nh;
    var cw = (n[2] - n[0]) * nw;
    var ch = (n[3] - n[1]) * nh;
    this.fabricImg.set({
      clipPath: null,
      cropX: cx0,
      cropY: cy0,
      width: cw,
      height: ch,
    });
    this._cropBaked = true;
    this.fabricImg.setCoords();
  };

  FabricLetterEditor.prototype._clearBakedCrop = function () {
    if (!this.fabricImg || !this._cropBaked) return;
    var sz = this._naturalSize();
    var nw = sz.nw;
    var nh = sz.nh;
    this.fabricImg.set({
      cropX: 0,
      cropY: 0,
      width: nw,
      height: nh,
    });
    this._cropBaked = false;
    this.fabricImg.setCoords();
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

  FabricLetterEditor.prototype._createCropRect = function () {
    return new fabric.Rect({
      fill: "rgba(255,255,255,0.01)",
      stroke: "#0284c7",
      strokeWidth: 2,
      strokeUniform: true,
      selectable: true,
      evented: true,
      hasRotatingPoint: false,
      lockRotation: true,
      transparentCorners: false,
      cornerColor: "#0076b8",
      borderColor: "#0076b8",
      cornerSize: 10,
      borderScaleFactor: 2,
      excludeFromExport: true,
      hoverCursor: "move",
      _isCarrierCropRect: true,
    });
  };

  FabricLetterEditor.prototype._clearCropUiObjects = function () {
    var self = this;
    if (this.canvas) {
      this.canvas.off("object:moving", this._onCropChangeBound);
      this.canvas.off("object:scaling", this._onCropChangeBound);
      this.canvas.off("object:modified", this._onCropChangeBound);
    }
    if (this.cropRectObj && this.canvas) {
      this.canvas.remove(this.cropRectObj);
    }
    this.cropShadows.forEach(function (o) {
      if (self.canvas) self.canvas.remove(o);
    });
    this.cropRectObj = null;
    this.cropShadows = [];
  };

  FabricLetterEditor.prototype._normalizeCropRectScale = function () {
    var r = this.cropRectObj;
    if (!r || (r.scaleX === 1 && r.scaleY === 1)) return;
    var w = r.width * r.scaleX;
    var h = r.height * r.scaleY;
    r.set({ width: w, height: h, scaleX: 1, scaleY: 1 });
    r.setCoords();
  };

  FabricLetterEditor.prototype._clampCropRectToImage = function () {
    if (!this.fabricImg || !this.cropRectObj) return;
    this._normalizeCropRectScale();
    var ibr = this.fabricImg.getBoundingRect(true);
    var bb = this.cropRectObj.getBoundingRect(true);
    var inter = this._intersectCanvasRects(
      { left: bb.left, top: bb.top, width: bb.width, height: bb.height },
      { left: ibr.left, top: ibr.top, width: ibr.width, height: ibr.height }
    );
    if (!inter) return;
    this.cropRectObj.set({
      left: inter.left,
      top: inter.top,
      width: inter.width,
      height: inter.height,
      scaleX: 1,
      scaleY: 1,
    });
    this.cropRectObj.setCoords();
  };

  FabricLetterEditor.prototype._syncNormFromCropRect = function () {
    if (!this.cropRectObj) return;
    this._normalizeCropRectScale();
    var ibr = this.fabricImg.getBoundingRect(true);
    var bb = this.cropRectObj.getBoundingRect(true);
    var rect = { left: bb.left, top: bb.top, width: bb.width, height: bb.height };
    var inter = this._intersectCanvasRects(rect, { left: ibr.left, top: ibr.top, width: ibr.width, height: ibr.height });
    if (!inter) return;
    var n = this._canvasRectToNorm(inter);
    if (n) this._appliedNorm = n;
  };

  FabricLetterEditor.prototype._updateShadowsFromCropRect = function () {
    if (!this.cropRectObj || this.cropShadows.length !== 4) return;
    var bb = this.cropRectObj.getBoundingRect(true);
    var x0 = bb.left;
    var y0 = bb.top;
    var x1 = bb.left + bb.width;
    var y1 = bb.top + bb.height;
    var cw = this.canvas ? this.canvas.getWidth() : LW;
    var ch = this.canvas ? this.canvas.getHeight() : LH;
    this.cropShadows[0].set({ left: 0, top: 0, width: cw, height: Math.max(0, y0) });
    this.cropShadows[1].set({ left: 0, top: y1, width: cw, height: Math.max(0, ch - y1) });
    this.cropShadows[2].set({ left: 0, top: y0, width: Math.max(0, x0), height: Math.max(0, y1 - y0) });
    this.cropShadows[3].set({ left: x1, top: y0, width: Math.max(0, cw - x1), height: Math.max(0, y1 - y0) });
    this.cropShadows.forEach(function (s) {
      s.setCoords();
    });
  };

  FabricLetterEditor.prototype._onCropChange = function (evt) {
    if (!this.cropMode || !evt || !evt.target || evt.target !== this.cropRectObj) return;
    if (evt.type === "object:modified") {
      this._normalizeCropRectScale();
    }
    this._clampCropRectToImage();
    this._syncNormFromCropRect();
    this._updateShadowsFromCropRect();
    this.canvas.requestRenderAll();
  };

  FabricLetterEditor.prototype._unbakeForCropEdit = function () {
    if (!this.fabricImg) return;
    this._clearBakedCrop();
    var sz = this._naturalSize();
    var nw = sz.nw;
    var nh = sz.nh;
    var u = this.getUserScale();
    this.baseFit = Math.min((LW * 0.98) / nw, (LH * 0.98) / nh);
    this.fabricImg.set({
      left: LW / 2,
      top: LH / 2,
      scaleX: this.baseFit * u,
      scaleY: this.baseFit * u,
    });
    this.fabricImg.setCoords();
  };

  FabricLetterEditor.prototype.enterCropMode = function () {
    if (!this.fabricImg || this.cropMode) return;
    this._normBeforeCrop = this._appliedNorm.slice();
    this._bakedBeforeCropEdit = !!this._cropBaked;
    if (this._cropBaked) {
      this._unbakeForCropEdit();
    }
    this.fabricImg.set("clipPath", null);
    this.cropMode = true;
    this.fabricImg.set({
      selectable: false,
      evented: false,
      hasControls: false,
      lockMovementX: true,
      lockMovementY: true,
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
    for (var si = 0; si < this.cropShadows.length; si++) this.canvas.add(this.cropShadows[si]);

    this.cropRectObj = this._createCropRect();
    var n = this._appliedNorm;
    var aabb = this._normUvToCanvasAabb(n[0], n[1], n[2], n[3]);
    var ibr = this.fabricImg.getBoundingRect(true);
    var inter = this._intersectCanvasRects(aabb, { left: ibr.left, top: ibr.top, width: ibr.width, height: ibr.height });
    if (inter) {
      this.cropRectObj.set({
        left: inter.left,
        top: inter.top,
        width: inter.width,
        height: inter.height,
        scaleX: 1,
        scaleY: 1,
      });
    } else {
      this.cropRectObj.set({
        left: ibr.left,
        top: ibr.top,
        width: ibr.width,
        height: ibr.height,
        scaleX: 1,
        scaleY: 1,
      });
    }
    this.cropRectObj.setCoords();
    this.canvas.add(this.cropRectObj);
    this.canvas.bringToFront(this.cropRectObj);
    this._syncNormFromCropRect();
    this._updateShadowsFromCropRect();
    this.canvas.setActiveObject(this.cropRectObj);

    this.canvas.on("object:moving", this._onCropChangeBound);
    this.canvas.on("object:scaling", this._onCropChangeBound);
    this.canvas.on("object:modified", this._onCropChangeBound);

    if (typeof this.options.onFocus === "function") {
      this.options.onFocus(this);
    }
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
        lockMovementX: false,
        lockMovementY: false,
        lockScalingX: false,
        lockScalingY: false,
        lockRotation: true,
      });
    }
  };

  FabricLetterEditor.prototype._reframeAfterApply = function (uPreserve) {
    if (!this.fabricImg) return;
    var n = this._appliedNorm;
    var sz = this._naturalSize();
    var nw = sz.nw;
    var nh = sz.nh;
    var cw = Math.max(n[2] - n[0], MIN_CROP_NORM) * nw;
    var ch = Math.max(n[3] - n[1], MIN_CROP_NORM) * nh;
    var u = uPreserve != null && !isNaN(uPreserve) ? clamp(Number(uPreserve), 0.25, 3) : this.getUserScale();
    this.baseFit = Math.min((LW * 0.98) / cw, (LH * 0.98) / ch);
    this.fabricImg.set({
      left: LW / 2,
      top: LH / 2,
      scaleX: this.baseFit * u,
      scaleY: this.baseFit * u,
    });
    this.fabricImg.setCoords();
  };

  FabricLetterEditor.prototype.applyCrop = function () {
    if (!this.cropMode || !this.fabricImg) return;
    this._normalizeCropRectScale();
    this._clampCropRectToImage();
    this._syncNormFromCropRect();
    var n = this._appliedNorm;
    if (n[2] - n[0] < MIN_CROP_NORM || n[3] - n[1] < MIN_CROP_NORM) {
      this.cancelCrop();
      return;
    }
    var uKeep = this.getUserScale();
    this._teardownCropUI();
    this._applyBakedCropFromNorm();
    this._reframeAfterApply(uKeep);
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
    var n = this._appliedNorm;
    var full = n[0] <= 1e-5 && n[1] <= 1e-5 && n[2] >= 1 - 1e-5 && n[3] >= 1 - 1e-5;
    var u = this.getUserScale();
    var sz = this._naturalSize();
    var nw0 = sz.nw;
    var nh0 = sz.nh;
    if (full) {
      this._clearBakedCrop();
      this.baseFit = Math.min((LW * 0.98) / nw0, (LH * 0.98) / nh0);
      this.fabricImg.set({
        left: LW / 2,
        top: LH / 2,
        scaleX: this.baseFit * u,
        scaleY: this.baseFit * u,
      });
      this.fabricImg.setCoords();
    } else if (this._bakedBeforeCropEdit) {
      this._applyBakedCropFromNorm();
      this._reframeAfterApply(u);
    } else {
      this._clearBakedCrop();
      this.baseFit = Math.min((LW * 0.98) / nw0, (LH * 0.98) / nh0);
      this.fabricImg.set({
        left: LW / 2,
        top: LH / 2,
        scaleX: this.baseFit * u,
        scaleY: this.baseFit * u,
      });
      this.fabricImg.setCoords();
      this._refreshClipPath();
    }
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
    var cn = this._appliedNorm;
    var isPartial =
      cn[0] > 1e-5 || cn[1] > 1e-5 || cn[2] < 1 - 1e-5 || cn[3] < 1 - 1e-5;
    if (isPartial) {
      this._applyBakedCropFromNorm();
      this._reframeAfterApply(rs);
    } else {
      this._cropBaked = false;
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
        if (t === self.cropRectObj || t._isCarrierCropRect) {
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
