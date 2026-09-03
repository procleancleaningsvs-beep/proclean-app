(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  if (root) root.BanorteCatalogAdmin = api;
  if (root && root.document) {
    root.document.addEventListener("DOMContentLoaded", function () {
      api.install(root.document, root);
    });
  }
})(typeof window !== "undefined" ? window : globalThis, function () {
  "use strict";

  const PAGE_SIZE = 25;
  const SEARCH_DELAY_MS = 300;

  function escapeHtml(value) {
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#039;");
  }

  function buildPageUrl(base, page, filter, pageSize) {
    const separator = base.indexOf("?") === -1 ? "?" : "&";
    return (
      base + separator +
      "page=" + encodeURIComponent(String(page)) +
      "&page_size=" + encodeURIComponent(String(pageSize || PAGE_SIZE)) +
      "&filter=" + encodeURIComponent(filter || "all")
    );
  }

  function shouldEnableApply(backendAuthorized, acknowledged, busy) {
    return backendAuthorized === true && acknowledged === true && busy !== true;
  }

  function buildActivationPayload(previewFingerprint, csrfToken, acknowledged) {
    return {
      preview_fingerprint: String(previewFingerprint || ""),
      csrf_token: String(csrfToken || ""),
      acknowledge_impact: acknowledged === true ? "yes" : "",
    };
  }

  function personName(item) {
    const person = item.target_person || item.current_person || {};
    return person.name || "—";
  }

  function personField(item, field) {
    const person = item.target_person || item.current_person || {};
    return person[field] || "—";
  }

  function renderComparisonRows(items) {
    if (!items || !items.length) {
      return '<tr><td colspan="6" class="catalog-admin-table__message">No hay resultados para este filtro.</td></tr>';
    }
    return items.map(function (item) {
      const conflictClass = item.operational_conflict ? " catalog-admin-row--conflict" : "";
      const lineageClass = item.lineage_status === "UNCONFIRMED" ? " catalog-admin-lineage--unconfirmed" : "";
      const relation = item.lineage_label || (item.operational_conflict ? "Requiere atención" : "—");
      const detailButton = '<button class="btn btn-secondary btn-sm" type="button" data-row-detail="' +
        escapeHtml(item.row_key) + '">Ver detalle</button>';
      const lineageButton = item.resolution_available
        ? '<button class="btn btn-secondary btn-sm" type="button" data-row-lineage="' +
          escapeHtml(item.row_key) + '">Revisar relación</button>'
        : "";
      return (
        '<tr class="' + conflictClass.trim() + '">' +
          "<td><strong>" + escapeHtml(item.classification_label) + "</strong>" +
            (item.business_reason !== item.classification_label ? "<small>" + escapeHtml(item.business_reason) + "</small>" : "") +
          "</td>" +
          "<td>" + escapeHtml(personName(item)) + "</td>" +
          "<td>" + escapeHtml(personField(item, "employee")) + "</td>" +
          "<td>" + escapeHtml(personField(item, "account_masked")) + "</td>" +
          '<td class="' + lineageClass.trim() + '">' + escapeHtml(relation) + "</td>" +
          '<td><div class="catalog-admin-row-actions">' + detailButton + lineageButton + "</div></td>" +
        "</tr>"
      );
    }).join("");
  }

  function evidenceLabel(value) {
    return value ? "Coincide" : "Difiere";
  }

  function renderLineageCandidates(items) {
    if (!items || !items.length) {
      return '<p class="catalog-admin-muted">No se encontraron relaciones anteriores con esa búsqueda.</p>';
    }
    const labels = {
      name: "Nombre",
      employee: "Número de empleado",
      account: "Cuenta",
      rfc: "RFC",
      birth_date: "Fecha de nacimiento",
    };
    return items.map(function (item) {
      const person = item.person || {};
      const evidence = item.evidence || {};
      const signals = Object.keys(labels).map(function (key) {
        const matches = evidence[key] === true;
        return '<li class="catalog-admin-signal catalog-admin-signal--' + (matches ? "match" : "diff") + '">' +
          "<span>" + escapeHtml(labels[key]) + "</span><strong>" + evidenceLabel(matches) + "</strong></li>";
      }).join("");
      return (
        '<label class="catalog-admin-candidate">' +
          '<input type="radio" name="lineage_candidate" value="' + escapeHtml(item.candidate_id) + '">' +
          '<span class="catalog-admin-candidate__body">' +
            "<strong>" + escapeHtml(person.name || "—") + "</strong>" +
            '<span>Número: ' + escapeHtml(person.employee || "—") + "</span>" +
            '<span>Cuenta: ' + escapeHtml(person.account_masked || "—") + "</span>" +
            '<span>RFC: ' + escapeHtml(person.rfc || "—") + "</span>" +
            '<ul class="catalog-admin-signals">' + signals + "</ul>" +
          "</span>" +
        "</label>"
      );
    }).join("");
  }

  function personCard(title, person) {
    if (!person) {
      return (
        '<section class="catalog-admin-person-card"><h3>' + escapeHtml(title) +
        '</h3><p>No disponible</p></section>'
      );
    }
    const fields = [
      ["Nombre", person.name],
      ["Número de empleado", person.employee],
      ["Cuenta", person.account_masked],
      ["RFC", person.rfc],
      ["Fecha de nacimiento", person.birth_date],
    ];
    return (
      '<section class="catalog-admin-person-card"><h3>' + escapeHtml(title) + "</h3><dl>" +
      fields.map(function (field) {
        return "<dt>" + escapeHtml(field[0]) + "</dt><dd>" + escapeHtml(field[1] || "—") + "</dd>";
      }).join("") +
      "</dl></section>"
    );
  }

  function renderDetail(item) {
    let note = item.business_reason || "";
    if (item.lineage_detail) note += (note ? " " : "") + item.lineage_detail;
    if (item.lineage_status === "CONFIRMED" && item.changed_fields && item.changed_fields.length) {
      note += " Los datos del nuevo archivo serán los vigentes.";
    }
    if (item.conflict_reason) note = item.conflict_reason;
    const recommendedAction = item.recommended_action
      ? '<p class="catalog-admin-detail-action"><strong>Acción recomendada:</strong> ' +
        escapeHtml(item.recommended_action) + "</p>"
      : "";
    return (
      '<div class="catalog-admin-before-after">' +
        personCard("CATÁLOGO VIGENTE", item.current_person) +
        personCard("NUEVO ARCHIVO", item.target_person) +
      "</div>" +
      (note ? '<p class="catalog-admin-detail-note">' + escapeHtml(note) + "</p>" : "") +
      recommendedAction
    );
  }

  function renderHistoryRows(items) {
    if (!items || !items.length) {
      return '<tr><td colspan="5" class="catalog-admin-table__message">Todavía no hay versiones para mostrar.</td></tr>';
    }
    return items.map(function (version) {
      const action = version.status_label === "Pendiente" || version.status_label === "Requiere atención" ? "Continuar" : "Ver";
      const url = "?version_id=" + encodeURIComponent(String(version.id));
      return (
        "<tr>" +
          "<td>" + escapeHtml(version.report_date || "—") + "</td>" +
          "<td>" + escapeHtml(version.person_count == null ? "—" : version.person_count) + "</td>" +
          '<td><span class="catalog-admin-badge catalog-admin-badge--' + escapeHtml(version.status_tone) + '">' +
            escapeHtml(version.status_label) + "</span>" +
            (version.status_subtext ? "<small>" + escapeHtml(version.status_subtext) + "</small>" : "") +
          "</td>" +
          "<td>" + escapeHtml(version.activated_at || "—") + "</td>" +
          '<td><a class="btn btn-secondary btn-sm" href="' + url + '">' + action + "</a></td>" +
        "</tr>"
      );
    }).join("");
  }

  function install(documentRef, windowRef) {
    const root = documentRef.getElementById("banorte-catalog-admin");
    if (!root || root.dataset.installed === "true") return null;
    root.dataset.installed = "true";

    let csrf = root.dataset.csrf || "";
    let uploadBusy = false;
    let comparisonPage = 1;
    let comparisonRequest = 0;
    let searchTimer = null;
    let historyPage = 1;
    let applyBusy = false;
    let applyPreviewInvalidated = false;
    let manualBusy = false;
    let lineageRequest = 0;
    let lineageSearchTimer = null;
    let activeLineageRow = "";

    const uploadForm = root.querySelector("[data-catalog-upload-form]");
    const fileInput = root.querySelector("[data-catalog-file]");
    const fileName = root.querySelector("[data-file-name]");
    const analyzeButton = root.querySelector("[data-analyze-button]");
    const operationStatus = root.querySelector("[data-operation-status]");
    const retryButton = root.querySelector("[data-retry-analysis]");
    const comparisonRegion = root.querySelector("[data-comparison-region]");
    const comparisonBody = root.querySelector("[data-comparison-rows]");
    const comparisonFilter = root.querySelector("[data-comparison-filter]");
    const comparisonSearch = root.querySelector("[data-comparison-search]");
    const comparisonPagination = root.querySelector("[data-comparison-pagination]");
    const previousButton = root.querySelector("[data-page-previous]");
    const nextButton = root.querySelector("[data-page-next]");
    const pageStatus = root.querySelector("[data-page-status]");
    const detailDialog = root.querySelector("[data-detail-dialog]");
    const detailTitle = root.querySelector("[data-detail-title]");
    const detailContent = root.querySelector("[data-detail-content]");
    const detailClose = root.querySelector("[data-detail-close]");
    const historyPagination = root.querySelector("[data-history-pagination]");
    const historyBody = root.querySelector("[data-history-rows]");
    const historyPrevious = root.querySelector("[data-history-previous]");
    const historyNext = root.querySelector("[data-history-next]");
    const historyStatus = root.querySelector("[data-history-status]");
    const applyForm = root.querySelector("[data-catalog-apply-form]");
    const applyAcknowledgement = root.querySelector(
      '[data-apply-acknowledgement][name="acknowledge_impact"]'
    );
    const applyButton = root.querySelector("[data-apply-button]");
    const applyStatus = root.querySelector("[data-apply-status]");
    const refreshPreviewButton = root.querySelector("[data-refresh-preview]");
    const lineageDialog = root.querySelector("[data-lineage-dialog]");
    const lineageRegion = root.querySelector("[data-lineage-region]");
    const lineageTarget = root.querySelector("[data-lineage-target]");
    const lineageSearch = root.querySelector("[data-lineage-search]");
    const lineageCandidates = root.querySelector("[data-lineage-candidates]");
    const lineageClose = root.querySelector("[data-lineage-close]");
    const lineageKeepSeparate = root.querySelector("[data-lineage-keep-separate]");
    const continuityForm = root.querySelector("[data-continuity-form]");
    const continuityReason = root.querySelector("[data-continuity-reason]");
    const continuityConfirm = root.querySelector("[data-continuity-confirm]");
    const lineageStatus = root.querySelector("[data-lineage-status]");

    function updateCsrf(token) {
      if (!token) return;
      csrf = token;
      root.dataset.csrf = token;
      if (uploadForm) {
        const field = uploadForm.querySelector('input[name="csrf_token"]');
        if (field) field.value = token;
      }
      if (applyForm) {
        const field = applyForm.querySelector('input[name="csrf_token"]');
        if (field) field.value = token;
      }
    }

    function showOperation(message, tone) {
      if (!operationStatus) return;
      operationStatus.hidden = false;
      operationStatus.dataset.tone = tone || "neutral";
      operationStatus.textContent = message;
    }

    async function readJson(response) {
      const data = await response.json().catch(function () { return {}; });
      updateCsrf(data.csrf_token);
      return data;
    }

    async function submitAnalysis(url, formData) {
      if (uploadBusy) return null;
      uploadBusy = true;
      if (analyzeButton) analyzeButton.disabled = true;
      if (retryButton) retryButton.disabled = true;
      showOperation("Analizando el archivo y preparando la comparación…", "busy");
      try {
        const response = await windowRef.fetch(url, {
          method: "POST",
          headers: {
            "Accept": "application/json",
            "X-Requested-With": "XMLHttpRequest",
            "X-CSRF-Token": csrf,
          },
          body: formData,
        });
        const data = await readJson(response);
        if (!response.ok || !data.ok) {
          throw new Error(data.message || "No se pudo completar el análisis. El catálogo vigente no fue modificado.");
        }
        showOperation("Análisis completo. Abriendo la comparación…", "success");
        windowRef.location.assign(data.redirect_url);
        return data;
      } catch (error) {
        showOperation(
          error && error.message ? error.message : "No se pudo completar el análisis. El catálogo vigente no fue modificado.",
          "error"
        );
        return null;
      } finally {
        uploadBusy = false;
        if (analyzeButton) analyzeButton.disabled = false;
        if (retryButton) retryButton.disabled = false;
      }
    }

    async function loadComparison(page) {
      if (!root.dataset.comparisonUrl || !comparisonBody) return null;
      const requestId = ++comparisonRequest;
      comparisonPage = page;
      comparisonRegion.setAttribute("aria-busy", "true");
      comparisonBody.innerHTML = '<tr><td colspan="6" class="catalog-admin-table__message">Cargando comparación…</td></tr>';
      try {
        const url = buildPageUrl(
          root.dataset.comparisonUrl,
          page,
          comparisonFilter ? comparisonFilter.value : "all",
          PAGE_SIZE
        );
        const response = await windowRef.fetch(url, {
          method: "GET",
          headers: {
            "Accept": "application/json",
            "X-Catalog-Search": comparisonSearch ? comparisonSearch.value.trim() : "",
          },
          cache: "no-store",
        });
        const data = await readJson(response);
        if (requestId !== comparisonRequest) return null;
        if (!response.ok || !data.ok) throw new Error("comparison_failed");
        comparisonBody.innerHTML = renderComparisonRows(data.items);
        if (comparisonPagination) {
          comparisonPagination.hidden = data.total_pages <= 1;
          if (previousButton) previousButton.disabled = !data.has_previous;
          if (nextButton) nextButton.disabled = !data.has_next;
          if (pageStatus) pageStatus.textContent = "Página " + data.page + " de " + data.total_pages + " · " + data.total + " resultados";
        }
        return data;
      } catch (_error) {
        if (requestId === comparisonRequest) {
          comparisonBody.innerHTML = '<tr><td colspan="6" class="catalog-admin-table__message">No se pudo cargar la comparación. Intenta nuevamente.</td></tr>';
        }
        return null;
      } finally {
        if (requestId === comparisonRequest) comparisonRegion.setAttribute("aria-busy", "false");
      }
    }

    async function openDetail(rowKey) {
      if (!detailDialog || !detailContent || !root.dataset.detailUrlTemplate) return;
      detailTitle.textContent = "Detalle del cambio";
      detailContent.textContent = "Cargando detalle…";
      if (typeof detailDialog.showModal === "function") detailDialog.showModal();
      else detailDialog.setAttribute("open", "");
      const url = root.dataset.detailUrlTemplate.replace("__ROW__", encodeURIComponent(rowKey));
      try {
        const response = await windowRef.fetch(url, {
          method: "GET",
          headers: { "Accept": "application/json" },
          cache: "no-store",
        });
        const data = await readJson(response);
        if (!response.ok || !data.ok) throw new Error("detail_failed");
        detailTitle.textContent = data.item.classification_label || "Detalle del cambio";
        detailContent.innerHTML = renderDetail(data.item);
      } catch (_error) {
        detailContent.textContent = "No se pudo cargar el detalle. Intenta nuevamente.";
      }
    }

    async function loadHistory(page) {
      if (!historyPagination || !historyBody || !root.dataset.historyUrl) return null;
      const url = buildPageUrl(root.dataset.historyUrl, page, "all", 20).replace("&filter=all", "");
      try {
        const response = await windowRef.fetch(url, {
          method: "GET",
          headers: { "Accept": "application/json" },
          cache: "no-store",
        });
        const data = await readJson(response);
        if (!response.ok || !data.ok) throw new Error("history_failed");
        historyPage = data.page;
        historyBody.innerHTML = renderHistoryRows(data.items);
        historyPrevious.disabled = !data.has_previous;
        historyNext.disabled = !data.has_next;
        historyStatus.textContent = "Página " + data.page + " de " + data.total_pages;
        return data;
      } catch (_error) {
        return null;
      }
    }

    function setApplyStatus(message, tone) {
      if (!applyStatus) return;
      applyStatus.textContent = message || "";
      applyStatus.dataset.tone = tone || "neutral";
    }

    function syncApplyState() {
      if (!applyForm || !applyButton) return;
      const backendAuthorized = applyForm.dataset.applyAuthorized === "true";
      const acknowledged = Boolean(
        applyAcknowledgement && applyAcknowledgement.checked
      );
      applyButton.disabled = !shouldEnableApply(
        backendAuthorized,
        acknowledged,
        applyBusy || applyPreviewInvalidated
      );
    }

    async function checkActivationAfterUnknown() {
      if (!root.dataset.activationCheckUrl) return false;
      const fingerprintField = applyForm
        ? applyForm.querySelector('input[name="preview_fingerprint"]')
        : null;
      try {
        const response = await windowRef.fetch(root.dataset.activationCheckUrl, {
          method: "GET",
          headers: {
            "Accept": "application/json",
            "X-Catalog-Preview-Fingerprint": fingerprintField
              ? fingerprintField.value
              : "",
          },
          cache: "no-store",
        });
        const data = await readJson(response);
        if (!response.ok || !data.ok) throw new Error("activation_check_failed");
        if (data.is_active) {
          setApplyStatus("El nuevo catálogo Banorte quedó vigente.", "success");
          windowRef.location.assign(root.dataset.indexUrl);
          return true;
        }
        if (data.retry_allowed) {
          applyPreviewInvalidated = false;
          setApplyStatus(
            "El catálogo no fue aplicado. Puedes revisar y volver a intentarlo.",
            "warning"
          );
        } else {
          applyPreviewInvalidated = true;
          setApplyStatus(
            data.message || "El catálogo no fue aplicado. Vuelve a revisar la comparación.",
            "error"
          );
          if (refreshPreviewButton) refreshPreviewButton.hidden = false;
        }
        return false;
      } catch (_error) {
        applyPreviewInvalidated = true;
        setApplyStatus(
          "No fue posible confirmar el resultado. Vuelve a cargar la comparación antes de intentar nuevamente.",
          "error"
        );
        if (refreshPreviewButton) refreshPreviewButton.hidden = false;
        return false;
      }
    }

    async function submitApply() {
      if (!applyForm || !applyButton || applyButton.disabled || applyBusy) return null;
      const fingerprintField = applyForm.querySelector('input[name="preview_fingerprint"]');
      applyBusy = true;
      applyPreviewInvalidated = false;
      applyForm.setAttribute("aria-busy", "true");
      applyButton.disabled = true;
      if (applyAcknowledgement) applyAcknowledgement.disabled = true;
      setApplyStatus("Aplicando catálogo…", "busy");
      let responseKnown = false;
      let navigating = false;
      try {
        const response = await windowRef.fetch(root.dataset.activationUrl, {
          method: "POST",
          headers: {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "X-Requested-With": "XMLHttpRequest",
            "X-CSRF-Token": csrf,
          },
          body: JSON.stringify(
            buildActivationPayload(
              fingerprintField ? fingerprintField.value : "",
              csrf,
              Boolean(applyAcknowledgement && applyAcknowledgement.checked)
            )
          ),
        });
        responseKnown = true;
        const data = await readJson(response);
        if (!response.ok || !data.ok) {
          applyPreviewInvalidated = data.preview_invalidated !== false;
          setApplyStatus(
            data.message || "No fue posible aplicar el catálogo.",
            "error"
          );
          if (applyPreviewInvalidated && refreshPreviewButton) {
            refreshPreviewButton.hidden = false;
          }
          return null;
        }
        navigating = true;
        setApplyStatus("El nuevo catálogo Banorte quedó vigente.", "success");
        windowRef.location.assign(data.redirect_url || root.dataset.indexUrl);
        return data;
      } catch (_error) {
        if (!responseKnown) {
          navigating = await checkActivationAfterUnknown();
        }
        return null;
      } finally {
        if (!navigating) {
          applyBusy = false;
          applyForm.setAttribute("aria-busy", "false");
          if (applyAcknowledgement && applyForm.dataset.applyAuthorized === "true") {
            applyAcknowledgement.disabled = applyPreviewInvalidated;
          }
          syncApplyState();
        }
      }
    }

    function setLineageStatus(message, tone) {
      if (!lineageStatus) return;
      lineageStatus.textContent = message || "";
      lineageStatus.dataset.tone = tone || "neutral";
    }

    function selectedCandidateId() {
      if (!lineageCandidates) return "";
      const selected = lineageCandidates.querySelector(
        'input[name="lineage_candidate"]:checked'
      );
      return selected ? selected.value : "";
    }

    function syncContinuityState() {
      if (!continuityConfirm) return;
      continuityConfirm.disabled = !(
        selectedCandidateId() &&
        continuityReason &&
        continuityReason.value.trim() &&
        !manualBusy
      );
    }

    function closeLineageReview() {
      if (!lineageDialog || manualBusy) return;
      activeLineageRow = "";
      if (typeof lineageDialog.close === "function") lineageDialog.close();
      else lineageDialog.removeAttribute("open");
    }

    async function loadLineageCandidates(page) {
      if (!activeLineageRow || !root.dataset.lineageUrlTemplate || !lineageCandidates) {
        return null;
      }
      const requestId = ++lineageRequest;
      const base = root.dataset.lineageUrlTemplate.replace(
        "__ROW__",
        encodeURIComponent(activeLineageRow)
      );
      const url = base + "?page=" + encodeURIComponent(String(page || 1)) + "&page_size=20";
      if (lineageRegion) lineageRegion.setAttribute("aria-busy", "true");
      lineageCandidates.innerHTML = '<p class="catalog-admin-muted">Buscando relaciones anteriores…</p>';
      try {
        const response = await windowRef.fetch(url, {
          method: "GET",
          headers: {
            "Accept": "application/json",
            "X-Catalog-Search": lineageSearch ? lineageSearch.value.trim() : "",
          },
          cache: "no-store",
        });
        const data = await readJson(response);
        if (requestId !== lineageRequest) return null;
        if (!response.ok || !data.ok) {
          throw new Error(data.message || "No se pudo revisar la relación.");
        }
        if (lineageTarget) {
          lineageTarget.innerHTML = personCard("Datos del nuevo archivo", data.target_person);
        }
        lineageCandidates.innerHTML = renderLineageCandidates(data.items);
        syncContinuityState();
        return data;
      } catch (error) {
        if (requestId === lineageRequest) {
          lineageCandidates.textContent = error && error.message
            ? error.message
            : "No se pudo revisar la relación.";
        }
        return null;
      } finally {
        if (requestId === lineageRequest && lineageRegion) {
          lineageRegion.setAttribute("aria-busy", "false");
        }
      }
    }

    function openLineageReview(rowKey) {
      if (!lineageDialog || !rowKey) return;
      activeLineageRow = rowKey;
      if (lineageSearch) lineageSearch.value = "";
      if (continuityReason) continuityReason.value = "";
      if (lineageTarget) lineageTarget.textContent = "Cargando persona…";
      if (lineageCandidates) lineageCandidates.textContent = "Buscando relaciones anteriores…";
      setLineageStatus("", "neutral");
      syncContinuityState();
      if (typeof lineageDialog.showModal === "function") lineageDialog.showModal();
      else lineageDialog.setAttribute("open", "");
      loadLineageCandidates(1).then(function () {
        if (lineageSearch) lineageSearch.focus();
      });
    }

    async function submitContinuity() {
      const candidateId = selectedCandidateId();
      const reason = continuityReason ? continuityReason.value.trim() : "";
      if (!activeLineageRow || !candidateId || !reason || manualBusy) return null;
      manualBusy = true;
      if (lineageRegion) lineageRegion.setAttribute("aria-busy", "true");
      if (continuityConfirm) continuityConfirm.disabled = true;
      if (lineageClose) lineageClose.disabled = true;
      if (lineageKeepSeparate) lineageKeepSeparate.disabled = true;
      setLineageStatus("Confirmando relación…", "busy");
      const url = root.dataset.continuityUrlTemplate.replace(
        "__ROW__",
        encodeURIComponent(activeLineageRow)
      );
      try {
        const response = await windowRef.fetch(url, {
          method: "POST",
          headers: {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "X-Requested-With": "XMLHttpRequest",
            "X-CSRF-Token": csrf,
          },
          body: JSON.stringify({
            candidate_id: Number(candidateId),
            reason: reason,
            csrf_token: csrf,
          }),
        });
        const data = await readJson(response);
        if (!response.ok || !data.ok) {
          throw new Error(
            data.message || "No fue posible confirmar esta relación."
          );
        }
        setLineageStatus(
          "Relación confirmada. Los datos del nuevo archivo serán los vigentes.",
          "success"
        );
        windowRef.location.assign(data.redirect_url);
        return data;
      } catch (error) {
        setLineageStatus(
          error && error.message
            ? error.message
            : "No fue posible confirmar esta relación.",
          "error"
        );
        return null;
      } finally {
        manualBusy = false;
        if (lineageRegion) lineageRegion.setAttribute("aria-busy", "false");
        if (lineageClose) lineageClose.disabled = false;
        if (lineageKeepSeparate) lineageKeepSeparate.disabled = false;
        syncContinuityState();
      }
    }

    if (fileInput && fileName) {
      fileInput.addEventListener("change", function () {
        fileName.textContent = fileInput.files && fileInput.files[0]
          ? fileInput.files[0].name
          : "Ningún archivo seleccionado";
      });
    }

    if (uploadForm) {
      uploadForm.addEventListener("submit", function (event) {
        event.preventDefault();
        if (uploadBusy) return;
        const formData = new windowRef.FormData(uploadForm);
        formData.set("csrf_token", csrf);
        submitAnalysis(root.dataset.uploadUrl, formData);
      });
    }

    if (retryButton) {
      retryButton.addEventListener("click", function () {
        if (uploadBusy) return;
        const formData = new windowRef.FormData();
        formData.set("csrf_token", csrf);
        submitAnalysis(root.dataset.retryUrl, formData);
      });
    }

    if (comparisonFilter) {
      comparisonFilter.addEventListener("change", function () { loadComparison(1); });
    }
    if (comparisonSearch) {
      comparisonSearch.addEventListener("input", function () {
        if (searchTimer) windowRef.clearTimeout(searchTimer);
        searchTimer = windowRef.setTimeout(function () { loadComparison(1); }, SEARCH_DELAY_MS);
      });
    }
    if (previousButton) previousButton.addEventListener("click", function () { if (comparisonPage > 1) loadComparison(comparisonPage - 1); });
    if (nextButton) nextButton.addEventListener("click", function () { loadComparison(comparisonPage + 1); });
    if (comparisonBody) {
      comparisonBody.addEventListener("click", function (event) {
        const detailButton = event.target.closest("[data-row-detail]");
        const lineageButton = event.target.closest("[data-row-lineage]");
        if (detailButton) openDetail(detailButton.dataset.rowDetail);
        if (lineageButton) openLineageReview(lineageButton.dataset.rowLineage);
      });
    }
    if (detailClose) {
      detailClose.addEventListener("click", function () {
        if (typeof detailDialog.close === "function") detailDialog.close();
        else detailDialog.removeAttribute("open");
      });
    }
    if (historyPrevious) historyPrevious.addEventListener("click", function () { if (historyPage > 1) loadHistory(historyPage - 1); });
    if (historyNext) historyNext.addEventListener("click", function () { loadHistory(historyPage + 1); });

    if (applyAcknowledgement) {
      applyAcknowledgement.addEventListener("change", syncApplyState);
    }
    if (applyForm) {
      applyForm.setAttribute("aria-busy", "false");
      applyForm.addEventListener("submit", function (event) {
        event.preventDefault();
        if (applyBusy) return;
        submitApply();
      });
    }
    if (refreshPreviewButton) {
      refreshPreviewButton.addEventListener("click", function () {
        const target = root.dataset.indexUrl + "?version_id=" + encodeURIComponent(root.dataset.versionId || "");
        windowRef.location.assign(target);
      });
    }
    if (lineageSearch) {
      lineageSearch.addEventListener("input", function () {
        if (lineageSearchTimer) windowRef.clearTimeout(lineageSearchTimer);
        lineageSearchTimer = windowRef.setTimeout(function () {
          loadLineageCandidates(1);
        }, SEARCH_DELAY_MS);
      });
    }
    if (lineageCandidates) {
      lineageCandidates.addEventListener("change", syncContinuityState);
    }
    if (continuityReason) {
      continuityReason.addEventListener("input", syncContinuityState);
    }
    if (continuityForm) {
      continuityForm.addEventListener("submit", function (event) {
        event.preventDefault();
        if (manualBusy) return;
        submitContinuity();
      });
    }
    if (lineageClose) lineageClose.addEventListener("click", closeLineageReview);
    if (lineageKeepSeparate) {
      lineageKeepSeparate.addEventListener("click", closeLineageReview);
    }
    if (lineageDialog) {
      lineageDialog.addEventListener("cancel", function (event) {
        if (manualBusy) event.preventDefault();
      });
      lineageDialog.addEventListener("keydown", function (event) {
        if (event.key === "Escape" && !manualBusy) closeLineageReview();
      });
    }

    if (root.dataset.comparisonUrl) loadComparison(1);
    syncApplyState();
    return {
      loadComparison: loadComparison,
      loadHistory: loadHistory,
      submitAnalysis: submitAnalysis,
      submitApply: submitApply,
      submitContinuity: submitContinuity,
      isUploadBusy: function () { return uploadBusy; },
      isApplyBusy: function () { return applyBusy; },
      isManualBusy: function () { return manualBusy; },
    };
  }

  return {
    PAGE_SIZE: PAGE_SIZE,
    SEARCH_DELAY_MS: SEARCH_DELAY_MS,
    escapeHtml: escapeHtml,
    buildPageUrl: buildPageUrl,
    shouldEnableApply: shouldEnableApply,
    buildActivationPayload: buildActivationPayload,
    renderComparisonRows: renderComparisonRows,
    renderLineageCandidates: renderLineageCandidates,
    renderDetail: renderDetail,
    renderHistoryRows: renderHistoryRows,
    install: install,
  };
});
