/**
 * Hoja carta (612×792) con Fabric.js: colocación con manijas, escala compartida vía callback,
 * y modo recorte separado (marco independiente del selector de colocación).
 */
(function (global) {
  var LW = 612;
  var LH = 792;

  function clamp(x, a, b) {
    return Math.max(a, Math.min(b, x));
  }

  function FabricLetterEditor(hostEl, options) {
    this.hostEl = hostEl;
    this.options = options || {};
    this.canvas = null;
    this.fabricImg = null;
    this.cropRect = null;
    this.cropMode = false;
    this.baseFit = 1;
    this._appliedNorm = [0, 0, 1, 1];
    this._normBeforeCrop = [0, 0, 1, 1];
    this._pendingMeta = null;
    this._dblHandler = null;
    this._keydownHandler = null;
    this._onCropModifyBound = this._onCropModify.bind(this);
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
      this.canvas.off("object:moving", this._onCropModifyBound);
      this.canvas.off("object:scaling", this._onCropModifyBound);
      try {
        this.canvas.dispose();
      } catch (e) {
        /* ignore */
      }
      this.canvas = null;
    }
    this.fabricImg = null;
    this.cropRect = null;
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
      width: Math.max(16, (x1 - x0) * br.width),
      height: Math.max(16, (y1 - y0) * br.height),
    };
  };

  FabricLetterEditor.prototype._clampCropRectToImage = function () {
    if (!this.cropRect || !this.fabricImg) return;
    var br = this.fabricImg.getBoundingRect(true);
    var c = this.cropRect;
    c.set({ scaleX: 1, scaleY: 1 });
    var w = Math.max(16, Math.min(c.width, br.width));
    var h = Math.max(16, Math.min(c.height, br.height));
    var left = clamp(c.left, br.left, br.left + br.width - w);
    var top = clamp(c.top, br.top, br.top + br.height - h);
    c.set({ left: left, top: top, width: w, height: h });
    c.setCoords();
  };

  FabricLetterEditor.prototype._onCropModify = function () {
    this._clampCropRectToImage();
  };

  FabricLetterEditor.prototype.enterCropMode = function () {
    if (!this.fabricImg || this.cropMode) return;
    this.fabricImg.set("clipPath", null);
    this._normBeforeCrop = this._appliedNorm.slice();
    this.cropMode = true;
    var r = this._normToCropRectCanvas();
    this.cropRect = new fabric.Rect({
      left: r.left,
      top: r.top,
      width: r.width,
      height: r.height,
      fill: "rgba(0, 100, 200, 0.14)",
      stroke: "#006edc",
      strokeWidth: 2,
      cornerColor: "#ffffff",
      cornerStrokeColor: "#006edc",
      transparentCorners: false,
      hasRotatingPoint: false,
      lockRotation: true,
      strokeUniform: true,
    });
    this.cropRect.setControlsVisibility({ mtr: false });
    this.fabricImg.selectable = false;
    this.canvas.discardActiveObject();
    this.canvas.add(this.cropRect);
    this.canvas.setActiveObject(this.cropRect);
    this._clampCropRectToImage();
    this.canvas.off("object:moving", this._onCropModifyBound);
    this.canvas.off("object:scaling", this._onCropModifyBound);
    this.canvas.on("object:moving", this._onCropModifyBound);
    this.canvas.on("object:scaling", this._onCropModifyBound);
    if (typeof this.options.onCropMode === "function") {
      this.options.onCropMode(true, this);
    }
    this.canvas.requestRenderAll();
  };

  FabricLetterEditor.prototype._teardownCropUI = function () {
    if (this.canvas) {
      this.canvas.off("object:moving", this._onCropModifyBound);
      this.canvas.off("object:scaling", this._onCropModifyBound);
    }
    if (this.cropRect && this.canvas) {
      this.canvas.remove(this.cropRect);
    }
    this.cropRect = null;
    this.cropMode = false;
    if (this.fabricImg) {
      this.fabricImg.selectable = true;
      this.fabricImg.evented = true;
    }
  };

  FabricLetterEditor.prototype.applyCrop = function () {
    if (!this.cropMode || !this.cropRect || !this.fabricImg) return;
    var br = this.fabricImg.getBoundingRect(true);
    var cbr = this.cropRect.getBoundingRect(true);
    var x0 = clamp((cbr.left - br.left) / br.width, 0, 1);
    var y0 = clamp((cbr.top - br.top) / br.height, 0, 1);
    var x1 = clamp((cbr.left + cbr.width - br.left) / br.width, 0, 1);
    var y1 = clamp((cbr.top + cbr.height - br.top) / br.height, 0, 1);
    if (x1 - x0 < 0.02 || y1 - y0 < 0.02) {
      this.cancelCrop();
      return;
    }
    this._appliedNorm = [x0, y0, x1, y1];
    this._teardownCropUI();
    this._refreshClipPath();
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
    this._refreshClipPath();
    this.canvas.add(img);
    this.canvas.setActiveObject(img);
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
        if (t === self.cropRect || t === self.fabricImg) {
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
