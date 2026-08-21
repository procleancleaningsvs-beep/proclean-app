(function (root, factory) {
  "use strict";
  const api = factory(root);
  if (typeof module === "object" && module.exports) module.exports = api;
  root.BanortePagSave = api;
})(typeof window !== "undefined" ? window : globalThis, function (root) {
  "use strict";

  class SaveError extends Error {
    constructor(message, code) {
      super(message);
      this.name = "SaveError";
      this.code = code || "save_error";
    }
  }

  class SaveCancelled extends SaveError {
    constructor(message) {
      super(message || "Guardado cancelado.", "cancelled");
      this.name = "SaveCancelled";
    }
  }

  const SAFE_PAG_FILENAME = /^[^/\\\x00]+\.pag$/i;

  function isCancelled(error) {
    return error instanceof SaveCancelled || (error && error.code === "cancelled");
  }

  function isIntegrityError(error) {
    return !!error && (
      error.code === "integrity_mismatch" ||
      error.code === "post_write_mismatch" ||
      error.code === "filename_unsafe"
    );
  }

  function requireSafeFilename(filename) {
    const value = String(filename || "");
    if (!SAFE_PAG_FILENAME.test(value) || value !== value.trim()) {
      throw new SaveError("El nombre bancario esperado no es válido.", "filename_unsafe");
    }
    return value;
  }

  function defaultNavigateDownload(url) {
    try {
      const anchor = root.document.createElement("a");
      anchor.href = url;
      anchor.rel = "noopener";
      anchor.style.display = "none";
      root.document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      return Promise.resolve(true);
    } catch (_error) {
      return Promise.resolve(false);
    }
  }

  function defaultEnvironment() {
    const savePickerSupported = "showSaveFilePicker" in root &&
      typeof root.showSaveFilePicker === "function";
    return {
      fetch: root.fetch.bind(root),
      crypto: root.crypto,
      navigator: root.navigator || {},
      File: root.File,
      showSaveFilePicker: savePickerSupported
        ? root.showSaveFilePicker.bind(root)
        : undefined,
      navigateDownload: defaultNavigateDownload,
    };
  }

  function createSaver(environment) {
    const env = environment;

    async function sha256Hex(value) {
      if (typeof env.sha256Hex === "function") return String(await env.sha256Hex(value)).toLowerCase();
      if (!env.crypto || !env.crypto.subtle) {
        throw new SaveError("Web Crypto no disponible.", "crypto_unavailable");
      }
      const buffer = await value.arrayBuffer();
      const digest = await env.crypto.subtle.digest("SHA-256", buffer);
      return Array.from(new Uint8Array(digest), function (byte) {
        return byte.toString(16).padStart(2, "0");
      }).join("");
    }

    function metadataUrl(exportId) {
      return "/nomina/exportaciones/banorte/historial/" +
        encodeURIComponent(String(exportId)) + "/metadata";
    }

    async function loadExport(options) {
      const response = await env.fetch(metadataUrl(options.exportId), {
        credentials: "same-origin",
        cache: "no-store",
        headers: { Accept: "application/json" },
      });
      const metadata = await response.json().catch(function () { return {}; });
      if (!response.ok || !metadata.ok) {
        throw new SaveError(
          "No fue posible obtener metadata autenticada.",
          metadata.code || "metadata_failed",
        );
      }
      const metadataFilename = requireSafeFilename(metadata.filename);
      if (options.filename && options.filename !== metadataFilename) {
        throw new SaveError("El filename histórico no coincide.", "integrity_mismatch");
      }
      if (!Number.isSafeInteger(metadata.size_bytes) || metadata.size_bytes < 0) {
        throw new SaveError("El tamaño histórico no es válido.", "integrity_mismatch");
      }
      if (!/^[0-9a-f]{64}$/i.test(String(metadata.sha256 || ""))) {
        throw new SaveError("El SHA-256 histórico no es válido.", "integrity_mismatch");
      }
      metadata.sha256 = String(metadata.sha256).toLowerCase();
      if (options.sha256 && String(options.sha256).toLowerCase() !== metadata.sha256) {
        throw new SaveError("El SHA-256 histórico no coincide.", "integrity_mismatch");
      }
      const raw = await env.fetch(metadata.raw_url, {
        credentials: "same-origin",
        cache: "no-store",
      });
      if (!raw.ok) {
        throw new SaveError("No fue posible recuperar el BLOB histórico.", "raw_failed");
      }
      const historicalBlob = await raw.blob();
      if (historicalBlob.size !== metadata.size_bytes) {
        throw new SaveError("El tamaño del BLOB histórico no coincide.", "integrity_mismatch");
      }
      const beforeSha256 = await sha256Hex(historicalBlob);
      if (beforeSha256 !== metadata.sha256) {
        throw new SaveError("El SHA-256 previo a la escritura no coincide.", "integrity_mismatch");
      }
      return { metadata: metadata, blob: historicalBlob, beforeSha256: beforeSha256 };
    }

    async function acquireFileHandle(expectedFilename) {
      try {
        return await env.showSaveFilePicker({
          id: "proclean-banorte-pag",
          suggestedName: expectedFilename,
          types: [{
            description: "Archivo Banorte (.pag)",
            accept: { "application/octet-stream": [".pag"] },
          }],
          excludeAcceptAllOption: false,
        });
      } catch (error) {
        if (error && error.name === "AbortError") {
          throw new SaveCancelled("Selección de archivo cancelada.");
        }
        throw error;
      }
    }

    async function saveWithFileHandle(fileHandle, loaded, expectedFilename) {
      if (!fileHandle || String(fileHandle.name || "") !== loaded.metadata.filename) {
        throw new SaveError(
          "El nombre elegido no coincide con el filename bancario exacto " + expectedFilename + ".",
          "integrity_mismatch",
        );
      }
      let writable = null;
      try {
        writable = await fileHandle.createWritable({ keepExistingData: false });
        await writable.write(loaded.blob);
        await writable.close();
      } catch (error) {
        if (writable && typeof writable.abort === "function") {
          try { await writable.abort(); } catch (_abortError) {}
        }
        throw error;
      }
      const written = await fileHandle.getFile();
      const afterSha256 = await sha256Hex(written);
      if (written.size !== loaded.metadata.size_bytes || afterSha256 !== loaded.beforeSha256) {
        throw new SaveError(
          "La verificación posterior a la escritura falló.",
          "post_write_mismatch",
        );
      }
      return {
        status: "saved",
        method: "save-file-picker",
        filename: loaded.metadata.filename,
        sizeBytes: written.size,
        sha256Before: loaded.beforeSha256,
        sha256After: afterSha256,
      };
    }

    async function shareFile(loaded) {
      const nav = env.navigator || {};
      const FileCtor = env.File || root.File;
      if (
        typeof FileCtor !== "function" ||
        typeof nav.share !== "function" ||
        typeof nav.canShare !== "function"
      ) {
        return null;
      }
      const file = new FileCtor(
        [loaded.blob],
        loaded.metadata.filename,
        { type: "application/octet-stream" },
      );
      const payload = { files: [file], title: loaded.metadata.filename };
      if (!nav.canShare(payload)) return null;
      try {
        await nav.share(payload);
      } catch (error) {
        if (error && error.name === "AbortError") {
          throw new SaveCancelled("Compartir cancelado.");
        }
        throw error;
      }
      return { status: "shared", method: "web-share", filename: loaded.metadata.filename };
    }

    async function downloadRaw(loaded) {
      if (await env.navigateDownload(loaded.metadata.raw_url)) {
        return {
          status: "download-started",
          method: "raw",
          filename: loaded.metadata.filename,
        };
      }
      throw new SaveError("No fue posible iniciar la descarga .pag.", "fallback_failed");
    }

    async function saveExport(options) {
      const saveOptions = options || {};
      let loaded = null;

      if (typeof env.showSaveFilePicker === "function") {
        const expectedFilename = requireSafeFilename(saveOptions.filename);
        let fileHandle = null;
        try {
          // Esta debe ser la primera llamada asíncrona para conservar user activation.
          fileHandle = await acquireFileHandle(expectedFilename);
        } catch (error) {
          if (isCancelled(error)) {
            return { status: "cancelled", method: "save-file-picker", filename: expectedFilename };
          }
          // Un fallo técnico del picker permite continuar al fallback verificado.
        }

        if (fileHandle) {
          loaded = await loadExport(saveOptions);
          try {
            return await saveWithFileHandle(fileHandle, loaded, expectedFilename);
          } catch (error) {
            if (isIntegrityError(error)) throw error;
            // Fallos técnicos de escritura continúan con el BLOB ya verificado.
          }
        }
      }

      if (!loaded) loaded = await loadExport(saveOptions);
      try {
        const shared = await shareFile(loaded);
        if (shared) return shared;
      } catch (error) {
        if (isCancelled(error)) {
          return {
            status: "cancelled",
            method: "web-share",
            filename: loaded.metadata.filename,
          };
        }
      }
      return downloadRaw(loaded);
    }

    return { saveExport: saveExport, loadExport: loadExport };
  }

  function describeResult(result, filename) {
    if (!result) return "Generado " + filename + ".";
    if (result.status === "saved") {
      return "Guardado y verificado " + filename + " (SHA-256 confirmado).";
    }
    if (result.status === "shared") return "Compartido " + filename + ".";
    if (result.status === "cancelled") {
      return "Guardado cancelado. " + filename + " sigue disponible en el historial.";
    }
    if (result.method === "raw") return "Descarga .pag iniciada para " + filename + ".";
    return "Generado " + filename + ".";
  }

  let defaultSaver = null;
  function getDefaultSaver() {
    if (!defaultSaver) defaultSaver = createSaver(defaultEnvironment());
    return defaultSaver;
  }

  function feedbackFor(anchor) {
    const targetId = anchor && anchor.dataset && anchor.dataset.feedbackTarget;
    const document = (anchor && anchor.ownerDocument) || root.document;
    if (!targetId || !document || typeof document.getElementById !== "function") return null;
    return document.getElementById(targetId);
  }

  function showFeedback(feedback, message, schedule) {
    if (!feedback) return;
    const sequence = Number(feedback._banorteFeedbackSequence || 0) + 1;
    feedback._banorteFeedbackSequence = sequence;
    feedback.textContent = message;
    feedback.hidden = false;
    schedule(function () {
      if (feedback._banorteFeedbackSequence !== sequence) return;
      feedback.hidden = true;
      feedback.textContent = "";
    }, 5000);
  }

  async function handleSaveTrigger(anchor, options) {
    const settings = options || {};
    const saver = settings.saver || getDefaultSaver();
    const navigateDownload = settings.navigateDownload || defaultNavigateDownload;
    const schedule = settings.schedule || root.setTimeout.bind(root);
    const originalText = anchor.textContent;
    const feedback = feedbackFor(anchor);

    anchor.setAttribute("aria-disabled", "true");
    anchor.setAttribute("aria-busy", "true");
    try {
      const result = await saver.saveExport({
        exportId: anchor.dataset.exportId,
        filename: anchor.dataset.filename || undefined,
        sha256: anchor.dataset.sha256 || undefined,
      });
      showFeedback(
        feedback,
        describeResult(result, anchor.dataset.filename || "archivo .pag"),
        schedule,
      );
    } catch (error) {
      if (isIntegrityError(error)) {
        showFeedback(
          feedback,
          "No se guardó: verifique el nombre e integridad del .pag.",
          schedule,
        );
      } else {
        showFeedback(
          feedback,
          "No se pudo guardar; iniciando descarga .pag…",
          schedule,
        );
        await navigateDownload(anchor.href);
      }
    } finally {
      anchor.textContent = originalText;
      anchor.removeAttribute("aria-disabled");
      anchor.removeAttribute("aria-busy");
    }
  }

  function bindSaveTriggers(container) {
    const host = container || root.document;
    if (!host || typeof host.querySelectorAll !== "function") return;
    host.querySelectorAll("[data-banorte-pag-save]").forEach(function (anchor) {
      if (anchor.dataset.banortePagBound === "1") return;
      anchor.dataset.banortePagBound = "1";
      anchor.addEventListener("click", async function (event) {
        event.preventDefault();
        if (anchor.getAttribute("aria-disabled") === "true") return;
        await handleSaveTrigger(anchor);
      });
    });
  }

  if (root.document) {
    if (root.document.readyState === "loading") {
      root.document.addEventListener("DOMContentLoaded", function () {
        bindSaveTriggers(root.document);
      });
    } else {
      bindSaveTriggers(root.document);
    }
  }

  return {
    SaveError: SaveError,
    createSaver: createSaver,
    saveExport: function (options) { return getDefaultSaver().saveExport(options); },
    handleSaveTrigger: handleSaveTrigger,
    bindSaveTriggers: bindSaveTriggers,
    describeResult: describeResult,
  };
});
