(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.BanorteBeneficiaryGrid = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  const FIELDS = ["employee", "name", "account"];

  function digits(value) {
    return String(value == null ? "" : value).replace(/\D/g, "");
  }

  function blankRow(key) {
    return {
      client_row_key: key,
      employee_number: "",
      nombre: "",
      account: "",
      use_account_as_employee_number: false,
      errors: {},
    };
  }

  function effectiveEmployee(row) {
    return row.use_account_as_employee_number ? digits(row.account) : digits(row.employee_number);
  }

  function normalizeRow(raw, key) {
    return {
      client_row_key: String(raw.client_row_key || key),
      employee_number: digits(raw.employee_number),
      nombre: String(raw.nombre || "").trim(),
      account: digits(raw.account == null ? raw.cuenta : raw.account),
      use_account_as_employee_number: !!raw.use_account_as_employee_number,
      errors: {},
    };
  }

  function validateRow(row) {
    const errors = {};
    const effective = effectiveEmployee(row);
    if (effective.length !== 10 || effective === "0000000000") {
      errors.employee_number = "El número debe tener exactamente 10 dígitos.";
    }
    if (!String(row.nombre || "").trim()) errors.nombre = "Nombre obligatorio.";
    if (!digits(row.account)) errors.account = "Cuenta obligatoria.";
    else if (digits(row.account).length > 18) errors.account = "La cuenta no puede exceder 18 dígitos.";
    if (row.use_account_as_employee_number && digits(row.account).length !== 10) {
      errors.account = "La cuenta debe tener exactamente 10 dígitos para usarla como número.";
    }
    row.errors = errors;
    return errors;
  }

  function resolveEntryKey(key, field, shiftKey) {
    if (key === "Tab") {
      const index = FIELDS.indexOf(field);
      if (shiftKey) {
        return { action: "focus", field: FIELDS[Math.max(0, index - 1)] };
      }
      if (field === "account") return { action: "focus_add" };
      return { action: "focus", field: FIELDS[index + 1] };
    }
    if (key === "Enter") {
      if (field === "employee") return { action: "focus", field: "name" };
      if (field === "name") return { action: "focus", field: "account" };
      if (field === "account") return { action: "add" };
    }
    return { action: "none" };
  }

  function createLocalModel(options) {
    const opts = options || {};
    let sequence = 0;
    const makeKey = typeof opts.keyFactory === "function"
      ? opts.keyFactory
      : function () { sequence += 1; return "local-" + sequence; };
    const model = {
      entry: blankRow(makeKey()),
      pendingRows: [],
      selectedPendingKey: null,
      entryCount: function () { return 1; },
      snapshot: function () {
        return {
          entry: JSON.parse(JSON.stringify(this.entry)),
          pendingRows: JSON.parse(JSON.stringify(this.pendingRows)),
          selectedPendingKey: this.selectedPendingKey,
        };
      },
      hydrate: function (rows) {
        this.pendingRows = (rows || []).map(function (row) {
          return normalizeRow(row, makeKey());
        });
        this.entry = blankRow(makeKey());
        this.selectedPendingKey = null;
      },
      entryHasAnyValue: function () {
        return !!(
          digits(this.entry.employee_number) ||
          String(this.entry.nombre || "").trim() ||
          digits(this.entry.account) ||
          this.entry.use_account_as_employee_number
        );
      },
      addEntry: function () {
        const errors = validateRow(this.entry);
        if (Object.keys(errors).length) return { ok: false, code: "entry_invalid", errors: errors };
        this.pendingRows.push(normalizeRow(this.entry, this.entry.client_row_key));
        this.entry = blankRow(makeKey());
        this.selectedPendingKey = null;
        return { ok: true };
      },
      removePending: function (key) {
        this.pendingRows = this.pendingRows.filter(function (row) {
          return row.client_row_key !== key;
        });
        if (this.selectedPendingKey === key) this.selectedPendingKey = null;
      },
      selectPending: function (key) {
        this.selectedPendingKey = this.pendingRows.some(function (row) {
          return row.client_row_key === key;
        }) ? key : null;
      },
      applyAvailableNumber: function (number) {
        const normalized = digits(number);
        const selected = this.pendingRows.find(function (row) {
          return row.client_row_key === model.selectedPendingKey;
        });
        const target = selected || this.entry;
        target.employee_number = normalized;
        target.use_account_as_employee_number = false;
        if (target.errors) delete target.errors.employee_number;
        return target.client_row_key;
      },
      locallyUsedEffectiveEmployees: function () {
        const used = new Set();
        this.pendingRows.concat([this.entry]).forEach(function (row) {
          const value = effectiveEmployee(row);
          if (value) used.add(value);
        });
        return used;
      },
      getPendingPayload: function () {
        return this.pendingRows.map(function (row) {
          return {
            client_row_key: row.client_row_key,
            employee_number: digits(row.employee_number),
            nombre: String(row.nombre || "").trim(),
            account: digits(row.account),
            use_account_as_employee_number: !!row.use_account_as_employee_number,
          };
        });
      },
      applyPaste: function (text, context) {
        const before = this.snapshot();
        const lines = String(text || "").replace(/\r/g, "").split("\n").filter(function (line) {
          return line.length > 0;
        });
        if (!lines.length) return { ok: true };
        const matrix = lines.map(function (line) { return line.split("\t"); });
        if (matrix.some(function (columns) { return columns.length > 3; })) {
          return { ok: false, code: "paste_too_many_columns" };
        }
        const width = Math.max.apply(null, matrix.map(function (columns) { return columns.length; }));
        if (width !== 1 && width !== 3) {
          return { ok: false, code: "paste_column_count_invalid" };
        }
        try {
          if (lines.length === 1) {
            if (width === 3) {
              this.entry.employee_number = digits(matrix[0][0]);
              this.entry.nombre = String(matrix[0][1] || "").trim();
              this.entry.account = digits(matrix[0][2]);
            } else {
              const field = (context || {}).field || "employee";
              if (field === "employee") this.entry.employee_number = digits(matrix[0][0]);
              else if (field === "name") this.entry.nombre = String(matrix[0][0] || "").trim();
              else this.entry.account = digits(matrix[0][0]);
            }
          } else {
            matrix.forEach(function (columns) {
              const row = blankRow(makeKey());
              if (width === 3) {
                row.employee_number = digits(columns[0]);
                row.nombre = String(columns[1] || "").trim();
                row.account = digits(columns[2]);
              } else {
                const field = (context || {}).field || "employee";
                if (field === "employee") row.employee_number = digits(columns[0]);
                else if (field === "name") row.nombre = String(columns[0] || "").trim();
                else row.account = digits(columns[0]);
              }
              model.pendingRows.push(row);
            });
            this.entry = blankRow(makeKey());
          }
        } catch (error) {
          this.entry = before.entry;
          this.pendingRows = before.pendingRows;
          this.selectedPendingKey = before.selectedPendingKey;
          throw error;
        }
        return { ok: true };
      },
    };
    return model;
  }

  function mount(options) {
    const opts = options || {};
    const root = opts.root;
    if (!root) throw new Error("beneficiary_grid_root_required");
    const pendingBody = root.querySelector("#banorte-beneficiary-pending-rows");
    const entryRow = root.querySelector("#banorte-beneficiary-entry-row");
    const addButton = root.querySelector("#banorte-beneficiary-add");
    const model = createLocalModel();
    let onChange = typeof opts.onChange === "function" ? opts.onChange : function () {};

    function inputMarkup(row, field, pending) {
      const key = row.client_row_key;
      const employee = field === "employee";
      const name = field === "name";
      const value = employee ? row.employee_number : (name ? row.nombre : row.account);
      const dataField = employee ? "employee_number" : (name ? "nombre" : "account");
      const error = row.errors && row.errors[dataField];
      let html = '<input type="text" autocomplete="off" data-grid-field="' + field +
        '" data-row-key="' + key + '" value="' + escapeHtml(value) + '"' +
        (employee || !name ? ' inputmode="numeric"' : "") +
        (error ? ' aria-invalid="true" title="' + escapeHtml(error) + '"' : "") + ">";
      if (employee) {
        html += '<label class="banorte-beneficiary-use-account"><input type="checkbox" data-use-account="' +
          key + '"' + (row.use_account_as_employee_number ? " checked" : "") +
          "> Usar cuenta como número</label>";
      }
      if (name && pending) html += '<span class="banorte-beneficiary-pending-label">Pendiente</span>';
      return html;
    }

    function escapeHtml(value) {
      return String(value == null ? "" : value)
        .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
    }

    function render() {
      pendingBody.innerHTML = "";
      model.pendingRows.forEach(function (row) {
        const tr = document.createElement("tr");
        tr.className = "banorte-beneficiary-pending-row" +
          (model.selectedPendingKey === row.client_row_key ? " is-selected" : "");
        tr.dataset.pendingKey = row.client_row_key;
        tr.innerHTML = "<td>" + inputMarkup(row, "employee", true) + "</td>" +
          "<td>" + inputMarkup(row, "name", true) + "</td>" +
          "<td>" + inputMarkup(row, "account", true) +
          '<button type="button" class="banorte-beneficiary-remove" data-remove-pending="' +
          row.client_row_key + '" aria-label="Eliminar beneficiario pendiente">×</button></td>';
        pendingBody.appendChild(tr);
      });
      entryRow.dataset.rowKey = model.entry.client_row_key;
      entryRow.innerHTML = "<td>" + inputMarkup(model.entry, "employee", false) + "</td>" +
        "<td>" + inputMarkup(model.entry, "name", false) + "</td>" +
        "<td>" + inputMarkup(model.entry, "account", false) + "</td>";
      onChange(model);
    }

    function rowForKey(key) {
      if (model.entry.client_row_key === key) return model.entry;
      return model.pendingRows.find(function (row) { return row.client_row_key === key; });
    }

    function focusEntry(field) {
      const el = entryRow.querySelector('[data-grid-field="' + field + '"]');
      if (el) el.focus();
    }

    function addEntryFromUi() {
      const result = model.addEntry();
      render();
      if (!result.ok) {
        const first = ["employee_number", "nombre", "account"].find(function (field) {
          return result.errors[field];
        });
        focusEntry(first === "employee_number" ? "employee" : (first === "nombre" ? "name" : "account"));
        return result;
      }
      focusEntry("employee");
      return result;
    }

    root.addEventListener("input", function (event) {
      const input = event.target.closest("[data-grid-field]");
      if (!input) return;
      const row = rowForKey(input.dataset.rowKey);
      if (!row) return;
      const field = input.dataset.gridField;
      if (field === "employee") row.employee_number = digits(input.value);
      else if (field === "name") row.nombre = input.value;
      else {
        row.account = digits(input.value);
        if (row.use_account_as_employee_number) row.employee_number = row.account;
      }
      row.errors = {};
      onChange(model);
    });
    root.addEventListener("change", function (event) {
      const checkbox = event.target.closest("[data-use-account]");
      if (!checkbox) return;
      const row = rowForKey(checkbox.dataset.useAccount);
      if (!row) return;
      if (checkbox.checked && digits(row.account).length !== 10) {
        checkbox.checked = false;
        row.use_account_as_employee_number = false;
        row.errors.account = "La cuenta debe tener exactamente 10 dígitos para usarla como número.";
        render();
        const account = root.querySelector('[data-row-key="' + row.client_row_key + '"][data-grid-field="account"]');
        if (account) account.focus();
        return;
      }
      row.use_account_as_employee_number = checkbox.checked;
      if (checkbox.checked) row.employee_number = digits(row.account);
      row.errors = {};
      render();
    });
    root.addEventListener("click", function (event) {
      const remove = event.target.closest("[data-remove-pending]");
      if (remove) {
        model.removePending(remove.dataset.removePending);
        render();
        return;
      }
      const pending = event.target.closest("[data-pending-key]");
      if (pending) {
        model.selectPending(pending.dataset.pendingKey);
        pendingBody.querySelectorAll("[data-pending-key]").forEach(function (row) {
          row.classList.toggle("is-selected", row.dataset.pendingKey === model.selectedPendingKey);
        });
      }
    });
    root.addEventListener("paste", function (event) {
      const input = event.target.closest("[data-grid-field]");
      if (!input) return;
      event.preventDefault();
      const result = model.applyPaste(event.clipboardData.getData("text/plain"), {
        field: input.dataset.gridField,
      });
      if (!result.ok && typeof opts.onError === "function") opts.onError(result.code);
      render();
    });
    entryRow.addEventListener("keydown", function (event) {
      const input = event.target.closest("[data-grid-field]");
      if (!input || (event.key !== "Tab" && event.key !== "Enter")) return;
      if (event.key === "Tab" && event.shiftKey && input.dataset.gridField === "employee") return;
      event.preventDefault();
      const action = resolveEntryKey(event.key, input.dataset.gridField, event.shiftKey);
      if (action.action === "focus") focusEntry(action.field);
      else if (action.action === "focus_add") addButton.focus();
      else if (action.action === "add") addEntryFromUi();
    });
    addButton.addEventListener("click", addEntryFromUi);

    model.hydrate(opts.initialRows || []);
    render();
    return {
      getPendingPayload: function () { return model.getPendingPayload(); },
      entryHasAnyValue: function () { return model.entryHasAnyValue(); },
      locallyUsedEffectiveEmployees: function () { return model.locallyUsedEffectiveEmployees(); },
      applyAvailableNumber: function (number) {
        const key = model.applyAvailableNumber(number);
        render();
        const target = root.querySelector('[data-row-key="' + key + '"][data-grid-field="employee"]');
        if (target) target.focus();
      },
      setErrors: function (errors) {
        (errors || []).forEach(function (error) {
          const row = rowForKey(String(error.client_row_key || ""));
          if (!row) return;
          row.errors[error.field === "employee_number" ? "employee_number" : error.field] = error.message;
        });
        render();
      },
      hydrate: function (rows) { model.hydrate(rows); render(); },
      clear: function () { model.hydrate([]); render(); },
      focusEntry: focusEntry,
      model: model,
    };
  }

  return {
    createLocalModel: createLocalModel,
    resolveEntryKey: resolveEntryKey,
    mount: mount,
  };
});
