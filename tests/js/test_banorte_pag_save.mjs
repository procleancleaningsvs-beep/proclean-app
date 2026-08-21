import assert from "node:assert/strict";
import test from "node:test";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const { createSaver } = require("../../static/nomina/banorte_pag_save.js");

const bytes = new TextEncoder().encode("HISTORICAL-PAG-BYTES");
const blob = new Blob([bytes], { type: "application/octet-stream" });
const sha = "a".repeat(64);

class FakeFile extends Blob {
  constructor(parts, name, options) {
    super(parts, options);
    this.name = name;
  }
}

function base(options = {}) {
  const expectedFilename = options.expectedFilename || "PAYROLL_DYNAMIC_07.pag";
  const metadataFilename = options.metadataFilename || expectedFilename;
  const fileName = options.fileName || expectedFilename;
  const downloads = [];
  const writes = [];
  const events = [];
  const pickerOptions = [];
  const writtenFile = {
    name: fileName,
    size: blob.size,
    arrayBuffer: () => blob.arrayBuffer(),
  };
  const fileHandle = {
    name: fileName,
    async createWritable() {
      events.push("createWritable");
      return {
        async write(value) { events.push("write"); writes.push(value); },
        async close() { events.push("close"); },
        async abort() { events.push("abort"); },
      };
    },
    async getFile() { events.push("getFile"); return writtenFile; },
  };
  const env = {
    async fetch(url) {
      events.push("fetch:" + url);
      if (url.endsWith("/metadata")) {
        return {
          ok: true,
          async json() {
            return {
              ok: true,
              export_id: 7,
              filename: metadataFilename,
              size_bytes: blob.size,
              sha256: sha,
              raw_url: "/raw",
            };
          },
        };
      }
      if (url === "/raw") return { ok: true, async blob() { return blob; } };
      throw new Error("unexpected fetch " + url);
    },
    async sha256Hex() { return sha; },
    async showSaveFilePicker(picker) {
      events.push("picker");
      pickerOptions.push(picker);
      return fileHandle;
    },
    navigator: {},
    File: FakeFile,
    async navigateDownload(url) { downloads.push(url); return true; },
    ...(options.env || {}),
  };
  return {
    saver: createSaver(env),
    env,
    fileHandle,
    writes,
    downloads,
    events,
    pickerOptions,
    expectedFilename,
  };
}

test("save picker is invoked before fetch with the dynamic backend filename", async () => {
  const ctx = base({ expectedFilename: "BANK_EXPORT_93.pag" });
  const result = await ctx.saver.saveExport({
    exportId: 7,
    filename: ctx.expectedFilename,
    sha256: sha,
  });

  assert.equal(result.method, "save-file-picker");
  assert.equal(result.status, "saved");
  assert.equal(ctx.events[0], "picker");
  assert.match(ctx.events[1], /metadata$/);
  assert.equal(ctx.pickerOptions[0].suggestedName, "BANK_EXPORT_93.pag");
  assert.deepEqual(
    ctx.pickerOptions[0].types[0].accept["application/octet-stream"],
    [".pag"],
  );
  assert.equal(ctx.writes.length, 1);
  assert.equal(ctx.writes[0], blob);
});

test("different dynamic filenames are passed through without reconstruction", async () => {
  for (const filename of ["CUSTOM_BANK_A1.pag", "OTHER_EXPORT_99.pag"]) {
    const ctx = base({ expectedFilename: filename });
    const result = await ctx.saver.saveExport({ exportId: 7, filename });
    assert.equal(ctx.pickerOptions[0].suggestedName, filename);
    assert.equal(result.filename, filename);
  }
});

test("save picker cancellation performs no fetch, share, or download", async () => {
  const error = new Error("cancelled");
  error.name = "AbortError";
  const ctx = base({
    env: {
      async showSaveFilePicker() { throw error; },
      navigator: {
        canShare() { throw new Error("must not share"); },
        async share() { throw new Error("must not share"); },
      },
    },
  });
  const result = await ctx.saver.saveExport({ exportId: 7, filename: ctx.expectedFilename });
  assert.equal(result.status, "cancelled");
  assert.equal(ctx.events.some((value) => value.startsWith("fetch:")), false);
  assert.deepEqual(ctx.downloads, []);
});

test("metadata filename mismatch blocks createWritable and fallback", async () => {
  const ctx = base({
    expectedFilename: "EXPECTED_01.pag",
    metadataFilename: "DIFFERENT_01.pag",
  });
  await assert.rejects(
    () => ctx.saver.saveExport({ exportId: 7, filename: ctx.expectedFilename }),
    (error) => error && error.code === "integrity_mismatch",
  );
  assert.equal(ctx.events.includes("createWritable"), false);
  assert.deepEqual(ctx.downloads, []);
});

test("file handle filename mismatch blocks createWritable and fallback", async () => {
  const ctx = base({
    expectedFilename: "EXPECTED_02.pag",
    fileName: "RENAMED_BY_USER.pag",
  });
  await assert.rejects(
    () => ctx.saver.saveExport({ exportId: 7, filename: ctx.expectedFilename }),
    (error) => error && error.code === "integrity_mismatch",
  );
  assert.equal(ctx.events.includes("createWritable"), false);
  assert.deepEqual(ctx.downloads, []);
});

test("pre-write SHA mismatch blocks every write and fallback", async () => {
  const ctx = base({ env: { async sha256Hex() { return "b".repeat(64); } } });
  await assert.rejects(
    () => ctx.saver.saveExport({ exportId: 7, filename: ctx.expectedFilename }),
    (error) => error && error.code === "integrity_mismatch",
  );
  assert.equal(ctx.events.includes("createWritable"), false);
  assert.deepEqual(ctx.downloads, []);
});

test("post-write SHA mismatch is an integrity failure, not success or fallback", async () => {
  let hashCalls = 0;
  const ctx = base({
    env: {
      async sha256Hex() {
        hashCalls += 1;
        return hashCalls === 1 ? sha : "b".repeat(64);
      },
    },
  });
  await assert.rejects(
    () => ctx.saver.saveExport({ exportId: 7, filename: ctx.expectedFilename }),
    (error) => error && error.code === "post_write_mismatch",
  );
  assert.deepEqual(ctx.downloads, []);
});

test("unsupported save picker uses Web Share with the exact filename", async () => {
  let shared;
  const ctx = base({
    env: {
      showSaveFilePicker: undefined,
      navigator: {
        canShare: ({ files }) => files.length === 1,
        async share(payload) { shared = payload; },
      },
    },
  });
  const result = await ctx.saver.saveExport({ exportId: 7, filename: ctx.expectedFilename });
  assert.equal(result.method, "web-share");
  assert.equal(shared.files[0].name, ctx.expectedFilename);
  assert.deepEqual(ctx.downloads, []);
});

test("Web Share cancellation does not start conventional download", async () => {
  const error = new Error("cancelled");
  error.name = "AbortError";
  const ctx = base({
    env: {
      showSaveFilePicker: undefined,
      navigator: {
        canShare: () => true,
        async share() { throw error; },
      },
    },
  });
  const result = await ctx.saver.saveExport({ exportId: 7, filename: ctx.expectedFilename });
  assert.equal(result.status, "cancelled");
  assert.deepEqual(ctx.downloads, []);
});

test("browser without picker or share downloads the historical raw pag", async () => {
  const ctx = base({ env: { showSaveFilePicker: undefined, navigator: {} } });
  const result = await ctx.saver.saveExport({ exportId: 7, filename: ctx.expectedFilename });
  assert.equal(result.method, "raw");
  assert.deepEqual(ctx.downloads, ["/raw"]);
});

test("technical picker failure falls back to raw pag", async () => {
  const ctx = base({
    env: {
      async showSaveFilePicker() { throw new Error("picker unavailable"); },
      navigator: {},
    },
  });
  const result = await ctx.saver.saveExport({ exportId: 7, filename: ctx.expectedFilename });
  assert.equal(result.method, "raw");
  assert.deepEqual(ctx.downloads, ["/raw"]);
});

test("technical write failure falls back to raw pag", async () => {
  const ctx = base();
  ctx.fileHandle.createWritable = async () => ({
    async write() { throw new Error("disk full"); },
    async abort() {},
  });
  const result = await ctx.saver.saveExport({ exportId: 7, filename: ctx.expectedFilename });
  assert.equal(result.method, "raw");
  assert.deepEqual(ctx.downloads, ["/raw"]);
});
