import assert from "node:assert/strict";
import test from "node:test";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const {
  createIndexedDbHandleStore,
  createSaver,
} = require("../../static/nomina/banorte_pag_save.js");

const bytes = new TextEncoder().encode("HISTORICAL-PAG-BYTES");
const blob = new Blob([bytes], { type: "application/octet-stream" });
const sha = "a".repeat(64);

function base(overrides = {}) {
  const downloads = [];
  const writes = [];
  const file = { name: "NI6705907.pag", size: blob.size, arrayBuffer: () => blob.arrayBuffer() };
  const fileHandle = {
    async createWritable() {
      return {
        async write(value) { writes.push(value); },
        async close() {},
        async abort() {},
      };
    },
    async getFile() { return file; },
  };
  const directory = {
    async queryPermission() { return "granted"; },
    async requestPermission() { return "granted"; },
    async getFileHandle(_name, options = {}) {
      if (!options.create) {
        const error = new Error("missing");
        error.name = "NotFoundError";
        throw error;
      }
      return fileHandle;
    },
  };
  const env = {
    async fetch(url) {
      if (url.endsWith("/metadata")) {
        return {
          ok: true,
          async json() {
            return {
              ok: true,
              export_id: 7,
              filename: "NI6705907.pag",
              size_bytes: blob.size,
              sha256: sha,
              raw_url: "/raw",
              zip_url: "/zip",
            };
          },
        };
      }
      if (url === "/raw") return { ok: true, async blob() { return blob; } };
      throw new Error("unexpected fetch " + url);
    },
    async sha256Hex() { return sha; },
    async showDirectoryPicker() { return directory; },
    async loadDirectoryHandle() { return null; },
    async saveDirectoryHandle(handle) { assert.equal(handle, directory); },
    async clearDirectoryHandle() {},
    confirm() { return true; },
    navigator: {},
    async navigateDownload(url) { downloads.push(url); return true; },
    ...overrides,
  };
  return { saver: createSaver(env), env, directory, fileHandle, writes, downloads };
}

test("File System Access writes exact name and verifies pre/post hash", async () => {
  const ctx = base();
  const result = await ctx.saver.saveExport({ exportId: 7, filename: "NI6705907.pag", sha256: sha });
  assert.equal(result.method, "file-system-access");
  assert.equal(result.status, "saved");
  assert.equal(ctx.writes.length, 1);
  assert.equal(ctx.writes[0], blob);
});

test("existing file cancellation happens before createWritable", async () => {
  let writableCalls = 0;
  const ctx = base({ confirm: () => false });
  ctx.fileHandle.createWritable = async () => { writableCalls += 1; throw new Error("must not run"); };
  ctx.directory.getFileHandle = async () => ctx.fileHandle;
  const result = await ctx.saver.saveExport({ exportId: 7 });
  assert.equal(result.status, "cancelled");
  assert.equal(writableCalls, 0);
});

test("confirmed replacement creates writable only after confirmation", async () => {
  const order = [];
  const ctx = base({ confirm: () => { order.push("confirm"); return true; } });
  ctx.directory.getFileHandle = async () => ctx.fileHandle;
  const original = ctx.fileHandle.createWritable;
  ctx.fileHandle.createWritable = async () => { order.push("writable"); return original(); };
  const result = await ctx.saver.saveExport({ exportId: 7 });
  assert.equal(result.status, "saved");
  assert.deepEqual(order, ["confirm", "writable"]);
});

test("stale stored handle is cleared and replaced", async () => {
  let cleared = 0;
  let saved = 0;
  const stale = { async queryPermission() { throw new Error("stale"); } };
  const ctx = base({
    async loadDirectoryHandle() { return stale; },
    async clearDirectoryHandle() { cleared += 1; },
    async saveDirectoryHandle(handle) { assert.equal(handle, ctx.directory); saved += 1; },
  });
  const result = await ctx.saver.saveExport({ exportId: 7 });
  assert.equal(result.status, "saved");
  assert.equal(cleared, 1);
  assert.equal(saved, 1);
});

test("prompt permission is requested in readwrite mode", async () => {
  const permissionCalls = [];
  const ctx = base();
  ctx.directory.queryPermission = async (descriptor) => {
    permissionCalls.push(["query", descriptor]);
    return "prompt";
  };
  ctx.directory.requestPermission = async (descriptor) => {
    permissionCalls.push(["request", descriptor]);
    return "granted";
  };
  const result = await ctx.saver.saveExport({ exportId: 7 });
  assert.equal(result.status, "saved");
  assert.deepEqual(permissionCalls, [
    ["query", { mode: "readwrite" }],
    ["request", { mode: "readwrite" }],
  ]);
});

test("denied permission never creates a writable and falls back to ZIP", async () => {
  let writableCalls = 0;
  const ctx = base();
  ctx.directory.queryPermission = async () => "denied";
  ctx.fileHandle.createWritable = async () => { writableCalls += 1; throw new Error("must not run"); };
  const result = await ctx.saver.saveExport({ exportId: 7 });
  assert.equal(result.method, "zip");
  assert.equal(writableCalls, 0);
});

test("IndexedDB stores only the directory handle value", async () => {
  const puts = [];
  const db = {
    objectStoreNames: { contains: () => true },
    transaction() {
      return {
        objectStore() {
          return {
            put(value, key) {
              const request = {};
              queueMicrotask(() => {
                puts.push({ value, key });
                request.result = key;
                request.onsuccess();
              });
              return request;
            },
          };
        },
      };
    },
    close() {},
  };
  const indexedDB = {
    open() {
      const request = {};
      queueMicrotask(() => {
        request.result = db;
        request.onsuccess();
      });
      return request;
    },
  };
  const handle = { kind: "directory" };
  await createIndexedDbHandleStore(indexedDB).save(handle);
  assert.equal(puts.length, 1);
  assert.equal(puts[0].value, handle);
  assert.equal(puts[0].key, "banorte_pag_directory");
  assert.deepEqual(Object.keys(puts[0]), ["value", "key"]);
});

test("picker cancellation does not start an unsolicited fallback", async () => {
  const error = new Error("cancelled");
  error.name = "AbortError";
  const ctx = base({ async showDirectoryPicker() { throw error; } });
  const result = await ctx.saver.saveExport({ exportId: 7 });
  assert.equal(result.status, "cancelled");
  assert.deepEqual(ctx.downloads, []);
});

test("pre-write hash mismatch blocks every save path", async () => {
  let picker = 0;
  const ctx = base({
    async sha256Hex() { return "b".repeat(64); },
    async showDirectoryPicker() { picker += 1; return ctx.directory; },
  });
  await assert.rejects(() => ctx.saver.saveExport({ exportId: 7 }), /SHA-256/);
  assert.equal(picker, 0);
  assert.deepEqual(ctx.downloads, []);
});

test("write failure falls back to ZIP", async () => {
  const ctx = base();
  ctx.fileHandle.createWritable = async () => ({
    async write() { throw new Error("disk full"); },
    async abort() {},
  });
  const result = await ctx.saver.saveExport({ exportId: 7 });
  assert.equal(result.method, "zip");
  assert.deepEqual(ctx.downloads, ["/zip"]);
});

test("post-write hash mismatch is not reported as File System Access success", async () => {
  let hashCalls = 0;
  const ctx = base({
    async sha256Hex() {
      hashCalls += 1;
      return hashCalls === 1 ? sha : "b".repeat(64);
    },
  });
  const result = await ctx.saver.saveExport({ exportId: 7 });
  assert.notEqual(result.method, "file-system-access");
  assert.equal(result.method, "zip");
});

test("Web Share receives a File with the exact bank filename", async () => {
  let shared;
  const ctx = base({
    showDirectoryPicker: undefined,
    navigator: {
      canShare: ({ files }) => files.length === 1,
      async share(payload) { shared = payload; },
    },
  });
  const result = await ctx.saver.saveExport({ exportId: 7 });
  assert.equal(result.method, "web-share");
  assert.equal(shared.files[0].name, "NI6705907.pag");
});

test("ZIP and then raw are ordered fallbacks", async () => {
  const ctx = base({
    showDirectoryPicker: undefined,
    navigator: {},
    async navigateDownload(url) {
      ctx.downloads.push(url);
      return url !== "/zip";
    },
  });
  const result = await ctx.saver.saveExport({ exportId: 7 });
  assert.equal(result.method, "raw");
  assert.deepEqual(ctx.downloads, ["/zip", "/raw"]);
});
