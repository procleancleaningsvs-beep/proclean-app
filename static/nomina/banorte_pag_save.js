(function (root, factory) {
  "use strict";
  const api = factory(root);
  if (typeof module === "object" && module.exports) module.exports = api;
  root.BanortePagSave = api;
})(typeof window !== "undefined" ? window : globalThis, function (root) {
  "use strict";

  const DB_NAME = "proclean-banorte-pag-save";
  const DB_VERSION = 1;
  const STORE_NAME = "directory_handles";
  const HANDLE_KEY = "banorte_pag_directory";

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

  function createIndexedDbHandleStore(indexedDB) {
    function open() {
      if (!indexedDB) return Promise.reject(new SaveError("IndexedDB no disponible.", "indexeddb_unavailable"));
      return new Promise(function (resolve, reject) {
        const request = indexedDB.open(DB_NAME, DB_VERSION);
        request.onupgradeneeded = function () {
          const db = request.result;
          if (!db.objectStoreNames.contains(STORE_NAME)) db.createObjectStore(STORE_NAME);
        };
        request.onsuccess = function () { resolve(request.result); };
        request.onerror = function () { reject(request.error || new SaveError("No se pudo abrir IndexedDB.")); };
      });
    }

    async function operation(mode, callback) {
      const db = await open();
      try {
        return await new Promise(function (resolve, reject) {
          const tx = db.transaction(STORE_NAME, mode);
          const request = callback(tx.objectStore(STORE_NAME));
          request.onsuccess = function () { resolve(request.result); };
          request.onerror = function () { reject(request.error || new SaveError("Falló IndexedDB.")); };
          tx.onabort = function () { reject(tx.error || new SaveError("IndexedDB abortó la operación.")); };
        });
      } finally {
        db.close();
      }
    }

    return {
      load: function () { return operation("readonly", function (store) { return store.get(HANDLE_KEY); }); },
      save: function (handle) {
        // El valor persistido es exclusivamente el FileSystemDirectoryHandle.
        return operation("readwrite", function (store) { return store.put(handle, HANDLE_KEY); });
      },
      clear: function () { return operation("readwrite", function (store) { return store.delete(HANDLE_KEY); }); },
    };
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
    const store = createIndexedDbHandleStore(root.indexedDB);
    return {
      fetch: root.fetch.bind(root),
      crypto: root.crypto,
      navigator: root.navigator || {},
      File: root.File,
      showDirectoryPicker: typeof root.showDirectoryPicker === "function"
        ? root.showDirectoryPicker.bind(root)
        : undefined,
      loadDirectoryHandle: store.load,
      saveDirectoryHandle: store.save,
      clearDirectoryHandle: store.clear,
      confirm: root.confirm.bind(root),
      navigateDownload: defaultNavigateDownload,
    };
  }

  function createSaver(environment) {
    const env = environment;

    async function sha256Hex(value) {
      if (typeof env.sha256Hex === "function") return String(await env.sha256Hex(value)).toLowerCase();
      if (!env.crypto || !env.crypto.subtle) throw new SaveError("Web Crypto no disponible.", "crypto_unavailable");
      const buffer = await value.arrayBuffer();
      const digest = await env.crypto.subtle.digest("SHA-256", buffer);
      return Array.from(new Uint8Array(digest), function (byte) {
        return byte.toString(16).padStart(2, "0");
      }).join("");
    }

    function metadataUrl(exportId) {
      return "/nomina/exportaciones/banorte/historial/" + encodeURIComponent(String(exportId)) + "/metadata";
    }

    async function loadExport(options) {
      const response = await env.fetch(metadataUrl(options.exportId), {
        credentials: "same-origin",
        cache: "no-store",
        headers: { Accept: "application/json" },
      });
      const metadata = await response.json().catch(function () { return {}; });
      if (!response.ok || !metadata.ok) {
        throw new SaveError("No fue posible obtener metadata autenticada.", metadata.code || "metadata_failed");
      }
      if (!/^[^/\\\x00]+\.pag$/i.test(metadata.filename || "")) {
        throw new SaveError("El nombre histórico no es seguro.", "filename_unsafe");
      }
      if (options.filename && options.filename !== metadata.filename) {
        throw new SaveError("El filename histórico no coincide.", "integrity_mismatch");
      }
      if (options.sha256 && String(options.sha256).toLowerCase() !== metadata.sha256) {
        throw new SaveError("El SHA-256 histórico no coincide.", "integrity_mismatch");
      }
      const raw = await env.fetch(metadata.raw_url, { credentials: "same-origin", cache: "no-store" });
      if (!raw.ok) throw new SaveError("No fue posible recuperar el BLOB histórico.", "raw_failed");
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

    async function permissionGranted(handle) {
      const descriptor = { mode: "readwrite" };
      if (typeof handle.queryPermission === "function") {
        const current = await handle.queryPermission(descriptor);
        if (current === "granted") return true;
        if (current === "denied") return false;
      }
      if (typeof handle.requestPermission === "function") {
        return (await handle.requestPermission(descriptor)) === "granted";
      }
      return false;
    }

    async function acquireDirectoryHandle() {
      let handle = null;
      try {
        handle = await env.loadDirectoryHandle();
      } catch (_error) {
        handle = null;
      }
      if (handle) {
        try {
          if (await permissionGranted(handle)) return handle;
        } catch (_error) {
          // A structured-clone/stale handle is discarded and selected again.
        }
        try { await env.clearDirectoryHandle(); } catch (_error) {}
      }
      if (typeof env.showDirectoryPicker !== "function") {
        throw new SaveError("File System Access no disponible.", "filesystem_unavailable");
      }
      try {
        handle = await env.showDirectoryPicker({ id: "proclean-banorte-pag", mode: "readwrite" });
      } catch (error) {
        if (error && error.name === "AbortError") throw new SaveCancelled("Selección de carpeta cancelada.");
        throw error;
      }
      if (!(await permissionGranted(handle))) {
        throw new SaveError("Permiso de escritura denegado.", "permission_denied");
      }
      await env.saveDirectoryHandle(handle);
      return handle;
    }

    async function findExistingFile(directory, filename) {
      try {
        return await directory.getFileHandle(filename);
      } catch (error) {
        if (error && error.name === "NotFoundError") return null;
        throw error;
      }
    }

    async function saveWithFileSystem(loaded) {
      const directory = await acquireDirectoryHandle();
      let fileHandle = await findExistingFile(directory, loaded.metadata.filename);
      if (fileHandle && !env.confirm("El archivo " + loaded.metadata.filename + " ya existe. ¿Desea reemplazarlo?")) {
        throw new SaveCancelled("Reemplazo cancelado.");
      }
      if (!fileHandle) {
        fileHandle = await directory.getFileHandle(loaded.metadata.filename, { create: true });
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
        throw new SaveError("La verificación SHA-256 posterior a la escritura falló.", "post_write_mismatch");
      }
      return {
        status: "saved",
        method: "file-system-access",
        filename: loaded.metadata.filename,
        sizeBytes: written.size,
        sha256Before: loaded.beforeSha256,
        sha256After: afterSha256,
      };
    }

    async function shareFile(loaded) {
      const nav = env.navigator || {};
      const FileCtor = env.File || root.File;
      if (typeof FileCtor !== "function" || typeof nav.share !== "function" || typeof nav.canShare !== "function") {
        return null;
      }
      const file = new FileCtor([loaded.blob], loaded.metadata.filename, { type: "application/octet-stream" });
      const payload = { files: [file], title: loaded.metadata.filename };
      if (!nav.canShare(payload)) return null;
      try {
        await nav.share(payload);
      } catch (error) {
        if (error && error.name === "AbortError") throw new SaveCancelled("Compartir cancelado.");
        throw error;
      }
      return { status: "shared", method: "web-share", filename: loaded.metadata.filename };
    }

    async function downloadFallbacks(loaded) {
      try {
        if (await env.navigateDownload(loaded.metadata.zip_url)) {
          return { status: "download-started", method: "zip", filename: loaded.metadata.filename };
        }
      } catch (_zipError) {}
      try {
        if (await env.navigateDownload(loaded.metadata.raw_url)) {
          return { status: "download-started", method: "raw", filename: loaded.metadata.filename };
        }
      } catch (_rawError) {}
      throw new SaveError("No fue posible iniciar ZIP ni descarga raw.", "fallback_failed");
    }

    async function saveExport(options) {
      const loaded = await loadExport(options || {});
      if (typeof env.showDirectoryPicker === "function") {
        try {
          return await saveWithFileSystem(loaded);
        } catch (error) {
          if (error instanceof SaveCancelled || (error && error.code === "cancelled")) {
            return { status: "cancelled", method: "file-system-access", filename: loaded.metadata.filename };
          }
        }
      }
      try {
        const shared = await shareFile(loaded);
        if (shared) return shared;
      } catch (error) {
        if (error instanceof SaveCancelled || (error && error.code === "cancelled")) {
          return { status: "cancelled", method: "web-share", filename: loaded.metadata.filename };
        }
      }
      return downloadFallbacks(loaded);
    }

    return { saveExport: saveExport, loadExport: loadExport };
  }

  function describeResult(result, filename) {
    if (!result) return "Generado " + filename + ".";
    if (result.status === "saved") return "Guardado y verificado " + filename + " (SHA-256 confirmado).";
    if (result.status === "shared") return "Compartido " + filename + ".";
    if (result.status === "cancelled") return "Generado " + filename + "; guardado cancelado. Disponible en el historial.";
    if (result.method === "zip") return "Generado " + filename + "; descarga ZIP iniciada.";
    if (result.method === "raw") return "Generado " + filename + "; descarga directa iniciada.";
    return "Generado " + filename + ".";
  }

  let defaultSaver = null;
  function getDefaultSaver() {
    if (!defaultSaver) defaultSaver = createSaver(defaultEnvironment());
    return defaultSaver;
  }

  function bindSaveTriggers(container) {
    const host = container || root.document;
    if (!host || typeof host.querySelectorAll !== "function") return;
    host.querySelectorAll("[data-banorte-pag-save]").forEach(function (anchor) {
      if (anchor.dataset.banortePagBound === "1") return;
      anchor.dataset.banortePagBound = "1";
      anchor.addEventListener("click", async function (event) {
        event.preventDefault();
        const originalText = anchor.textContent;
        anchor.setAttribute("aria-disabled", "true");
        anchor.textContent = "Preparando…";
        try {
          const result = await getDefaultSaver().saveExport({
            exportId: anchor.dataset.exportId,
            filename: anchor.dataset.filename || undefined,
            sha256: anchor.dataset.sha256 || undefined,
          });
          anchor.textContent = describeResult(result, anchor.dataset.filename || "archivo .pag");
        } catch (error) {
          anchor.textContent = "No se pudo guardar";
          if (!error || error.code !== "integrity_mismatch") {
            await defaultNavigateDownload(anchor.href);
          }
        } finally {
          anchor.removeAttribute("aria-disabled");
          root.setTimeout(function () { anchor.textContent = originalText; }, 5000);
        }
      });
    });
  }

  if (root.document) {
    if (root.document.readyState === "loading") {
      root.document.addEventListener("DOMContentLoaded", function () { bindSaveTriggers(root.document); });
    } else {
      bindSaveTriggers(root.document);
    }
  }

  return {
    createSaver: createSaver,
    createIndexedDbHandleStore: createIndexedDbHandleStore,
    saveExport: function (options) { return getDefaultSaver().saveExport(options); },
    bindSaveTriggers: bindSaveTriggers,
    describeResult: describeResult,
  };
});
