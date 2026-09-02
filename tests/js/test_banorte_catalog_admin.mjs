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

test("C3a check 5: JS owns safe UX state but never authority or mutating catalog actions", () => {
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
  assert.doesNotMatch(source, /\/activate|\/rollback|reconciliations\/manual/);
  assert.doesNotMatch(source, /can_apply|CATALOG_BOUND|authority_kind|match_method/);
});
