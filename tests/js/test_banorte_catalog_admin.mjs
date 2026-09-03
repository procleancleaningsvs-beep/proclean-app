import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const admin = require("../../static/nomina/banorte_catalog_admin.js");
const source = fs.readFileSync(
  new URL("../../static/nomina/banorte_catalog_admin.js", import.meta.url),
  "utf8"
);
const template = fs.readFileSync(
  new URL("../../templates/nomina/exportaciones_banorte_catalogo.html", import.meta.url),
  "utf8"
);

test("C3a base contract: JS keeps search private and renders masked comparison safely", () => {
  const url = admin.buildPageUrl("/catalog/comparison", 2, "conflict", 25);
  assert.equal(url, "/catalog/comparison?page=2&page_size=25&filter=conflict");
  assert.equal(url.includes("search="), false);

  const html = admin.renderComparisonRows([
    {
      row_key: "target-1",
      classification_label: "Nueva persona en Banorte",
      business_reason: "Nueva persona en Banorte",
      target_person: {
        name: "<script>alert(1)</script>",
        employee: "1001",
        account_masked: "******4321",
      },
      current_person: null,
      lineage_status: "UNCONFIRMED",
      lineage_label: "Relación histórica no confirmada",
      operational_conflict: false,
    },
  ]);
  assert.match(html, /&lt;script&gt;alert\(1\)&lt;\/script&gt;/);
  assert.doesNotMatch(html, /<script>/);
  assert.match(html, /\*\*\*\*\*\*4321/);
  assert.match(html, /Relación histórica no confirmada/);

  assert.match(source, /uploadBusy/);
  assert.match(source, /X-CSRF-Token/);
  assert.match(source, /X-Catalog-Search/);
  assert.match(source, /Analizando el archivo/);
  assert.match(source, /No se pudo cargar la comparación/);
  assert.doesNotMatch(source, /\/rollback/);
  assert.doesNotMatch(source, /can_apply|CATALOG_BOUND|authority_kind|match_method/);
});

test("C3b check 5: apply and lineage UX are backend-gated, single-flight, masked and accessible", () => {
  assert.equal(admin.shouldEnableApply(true, true, false), true);
  assert.equal(admin.shouldEnableApply(true, false, false), false);
  assert.equal(admin.shouldEnableApply(false, true, false), false);
  assert.equal(admin.shouldEnableApply(true, true, true), false);

  const payload = admin.buildActivationPayload("preview-from-backend", "csrf-token", true);
  assert.deepEqual(payload, {
    preview_fingerprint: "preview-from-backend",
    csrf_token: "csrf-token",
    acknowledge_impact: "yes",
  });

  const reviewHtml = admin.renderComparisonRows([
    {
      row_key: "target-22",
      classification_label: "Nueva persona en Banorte",
      business_reason: "Nueva persona en Banorte",
      target_person: {
        name: "PERSONA SEGURA",
        employee: "0022",
        account_masked: "******4422",
      },
      current_person: null,
      lineage_status: "UNCONFIRMED",
      lineage_label: "Relación histórica no confirmada",
      operational_conflict: false,
      resolution_available: true,
    },
  ]);
  assert.match(reviewHtml, /Revisar relación/);
  assert.match(reviewHtml, /\*\*\*\*\*\*4422/);

  assert.match(source, /applyBusy/);
  assert.match(source, /manualBusy/);
  assert.match(source, /Aplicando catálogo…/);
  assert.match(source, /preview_fingerprint/);
  assert.match(source, /acknowledge_impact/);
  assert.match(source, /X-CSRF-Token/);
  assert.match(source, /aria-busy/);
  assert.match(source, /Revisar relación/);
  assert.match(template, /Confirmar misma persona/);
  assert.match(template, /Mantener sin relación confirmada/);
  assert.match(source, /keydown/);
  assert.match(source, /Escape/);
  assert.doesNotMatch(source, /localStorage|sessionStorage|console\./);
  assert.doesNotMatch(source, /confirm-distinct|manual-distinct|Aplicar de todos modos|Forzar/);
  assert.doesNotMatch(source, /operational_conflict_count\s*[<>=]|lineage_unconfirmed_count\s*[<>=]/);
});

test("catalog conflict detail is actionable, masked, and contains no internal reason codes", () => {
  const item = {
    row_key: "conflict-projection-7",
    classification_label: "Conflicto que requiere atención",
    business_reason: "La persona figura en el nuevo archivo con estatus “Capturado”, no “Aplicado”.",
    conflict_reason: "La persona figura en el nuevo archivo con estatus “Capturado”, no “Aplicado”.",
    recommended_action: "Corrige el estado de esta persona en Empleados.txt y vuelve a analizarlo.",
    target_person: {
      name: "PERSONA SEGURA",
      employee: "0000000443",
      account_masked: "******5180",
      rfc: "PEND900101AA1",
      birth_date: "1990-01-01",
    },
    current_person: null,
    lineage_status: null,
    operational_conflict: true,
    resolution_available: false,
  };
  const rowHtml = admin.renderComparisonRows([item]);
  const detailHtml = admin.renderDetail(item);

  assert.match(rowHtml, /PERSONA SEGURA/);
  assert.match(rowHtml, /\*\*\*\*\*\*5180/);
  assert.match(detailHtml, /NUEVO ARCHIVO/);
  assert.match(detailHtml, /Acción recomendada/);
  assert.match(detailHtml, /vuelve a analizarlo/);
  assert.doesNotMatch(detailHtml, /9876545180/);
  assert.doesNotMatch(detailHtml, /PROJECTION_BLOCKERS|NO_ELIGIBLE_ROW|STATUS_NOT_APLICADO/);
});
