/* Legend (checkpoints) bulk editor — /race/<slug>/legend/edit/.

   A spreadsheet-like grid of checkpoints that accepts paste straight from
   Excel / Google Sheets: paste TSV into a cell (fills from that cell, creating
   rows as needed) or paste a whole block via the modal. Serializes the rows
   into the hidden #checkpointsJson input on submit.

   Reads seed rows from <script id="checkpoints-data"> and the type catalogue
   from <script id="legend-config"> (not a hardcoded enum). */
(function () {
  "use strict";

  /* ── JSON island helpers ─────────────────────────────────── */
  function readJson(id) {
    var el = document.getElementById(id);
    if (!el) return [];
    try {
      var data = JSON.parse(el.textContent || "[]");
      return Array.isArray(data) ? data : [];
    } catch (e) {
      return [];
    }
  }

  function readJsonObj(id) {
    var el = document.getElementById(id);
    if (!el) return {};
    try {
      var data = JSON.parse(el.textContent || "{}");
      return data && typeof data === "object" && !Array.isArray(data) ? data : {};
    } catch (e) {
      return {};
    }
  }

  /* Failsafe: seed the hidden input from the island so a POST still carries
     data even if the submit handler fails to wire (mid-IIFE error). */
  (function () {
    var field = document.getElementById("checkpointsJson");
    var island = document.getElementById("checkpoints-data");
    if (field && island) field.value = island.textContent.trim();
  })();

  var config = readJsonObj("legend-config");
  var TYPES = Array.isArray(config.types) ? config.types : [{ value: "kp", label: "КП" }];
  var DESC_MAX = parseInt(config.descMaxLen, 10) || 200;
  var DEFAULT_TYPE = "kp";

  // value-set + lower(value|label) → value, so paste can give either form.
  var TYPE_VALUES = {};
  var TYPE_LOOKUP = {};
  TYPES.forEach(function (t) {
    TYPE_VALUES[t.value] = true;
    TYPE_LOOKUP[String(t.value).toLowerCase()] = t.value;
    TYPE_LOOKUP[String(t.label).toLowerCase()] = t.value;
  });
  if (!TYPE_VALUES[DEFAULT_TYPE]) DEFAULT_TYPE = TYPES[0].value;

  // Logical column order — must match the server reconcile + the docs banner.
  var COLS = ["number", "type", "cost", "description", "is_legend_locked"];

  function normalizeType(raw) {
    var key = String(raw == null ? "" : raw).trim().toLowerCase();
    if (!key) return DEFAULT_TYPE;
    return TYPE_LOOKUP[key] || DEFAULT_TYPE;
  }

  function normalizeLock(raw) {
    var key = String(raw == null ? "" : raw).trim().toLowerCase();
    return key === "1" || key === "да" || key === "yes" || key === "y" ||
      key === "true" || key === "+" || key === "x" || key === "✓";
  }

  function digitsOrEmpty(raw) {
    var s = String(raw == null ? "" : raw).trim();
    return /^-?\d+$/.test(s) ? s : (s === "" ? "" : s.replace(/[^\d-]/g, ""));
  }

  var grid = document.getElementById("cpGrid");
  var countEl = document.getElementById("cpCount");

  /* ── Row construction ────────────────────────────────────── */
  function makeRow(c) {
    c = c || {};
    var hasTags = c.has_tags === true;
    var row = document.createElement("div");
    row.className = "lg-row" + (c.is_legend_locked ? " is-locked" : "");
    if (c.id != null) row.dataset.id = c.id;
    if (hasTags) row.dataset.hasTags = "1";

    var typeOptions = TYPES.map(function (t) {
      return '<option value="' + t.value + '">' + t.label + "</option>";
    }).join("");

    row.innerHTML =
      '<input class="lg-cell lg-num mono" type="text" inputmode="numeric" data-col="0">' +
      '<select class="lg-cell lg-type" data-col="1">' + typeOptions + "</select>" +
      '<input class="lg-cell lg-cost mono" type="text" inputmode="numeric" data-col="2">' +
      '<input class="lg-cell lg-desc" type="text" maxlength="' + DESC_MAX + '" data-col="3">' +
      '<label class="lg-lockcell"><input class="lg-lock" type="checkbox" data-col="4"></label>' +
      '<button class="lg-del" type="button" title="Удалить КП">' +
      '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6"><path d="M3 4h10M6.5 4V2.8h3V4M5 4l.6 9h4.8L11 4"/></svg>' +
      "</button>";

    row.querySelector(".lg-num").value = c.number != null ? c.number : "";
    var typeSel = row.querySelector(".lg-type");
    typeSel.value = normalizeType(c.type);
    row.querySelector(".lg-cost").value = c.cost != null ? c.cost : "";
    row.querySelector(".lg-desc").value = c.description || "";
    var lock = row.querySelector(".lg-lock");
    lock.checked = !!c.is_legend_locked;

    lock.addEventListener("change", function () {
      row.classList.toggle("is-locked", lock.checked);
    });

    var delBtn = row.querySelector(".lg-del");
    if (hasTags) {
      // КП with provisioned NFC tags can't be deleted here (server refuses too).
      delBtn.disabled = true;
      delBtn.title = "К КП привязаны NFC-теги — удалить нельзя";
    } else {
      delBtn.addEventListener("click", function () {
        row.style.transition = "opacity .12s";
        row.style.opacity = "0";
        setTimeout(function () {
          row.remove();
          refreshCount();
        }, 120);
      });
    }

    wirePaste(row);
    return row;
  }

  function refreshCount() {
    if (!grid) return;
    var n = grid.querySelectorAll(".lg-row").length;
    if (countEl) countEl.textContent = n;
    var empty = grid.querySelector(".lg-empty");
    if (n === 0 && !empty) {
      empty = document.createElement("div");
      empty.className = "lg-empty";
      empty.textContent = "Пока нет КП — добавьте строку или вставьте из таблицы.";
      grid.appendChild(empty);
    } else if (n > 0 && empty) {
      empty.remove();
    }
  }

  function rowList() {
    return Array.prototype.slice.call(grid.querySelectorAll(".lg-row"));
  }

  function controlByCol(row, idx) {
    return row.querySelector('[data-col="' + idx + '"]');
  }

  /* Write a normalized value into one cell of a row by logical column. */
  function setCell(row, colIdx, raw) {
    var col = COLS[colIdx];
    if (col === "number") {
      controlByCol(row, 0).value = digitsOrEmpty(raw);
    } else if (col === "type") {
      controlByCol(row, 1).value = normalizeType(raw);
    } else if (col === "cost") {
      controlByCol(row, 2).value = digitsOrEmpty(raw);
    } else if (col === "description") {
      controlByCol(row, 3).value = String(raw == null ? "" : raw).slice(0, DESC_MAX);
    } else if (col === "is_legend_locked") {
      var lock = controlByCol(row, 4);
      lock.checked = normalizeLock(raw);
      row.classList.toggle("is-locked", lock.checked);
    }
  }

  /* Parse a clipboard / textarea block into a matrix of cells (TSV). */
  function parseMatrix(text) {
    return String(text || "")
      .replace(/\r\n?/g, "\n")
      .replace(/\n+$/, "")
      .split("\n")
      .filter(function (line) { return line.trim() !== ""; })
      .map(function (line) { return line.split("\t"); });
  }

  /* ── Inline spreadsheet paste (fill from the focused cell) ─ */
  function wirePaste(row) {
    row.querySelectorAll(".lg-cell").forEach(function (cell) {
      cell.addEventListener("paste", function (e) {
        var text = (e.clipboardData || window.clipboardData).getData("text");
        // Single value into one cell → let the browser handle it normally.
        if (text.indexOf("\t") === -1 && text.indexOf("\n") === -1) return;
        e.preventDefault();
        var matrix = parseMatrix(text);
        var startCol = parseInt(cell.getAttribute("data-col"), 10) || 0;
        var rows = rowList();
        var startRow = rows.indexOf(row);
        matrix.forEach(function (cells, r) {
          var target = rows[startRow + r];
          if (!target) {
            target = makeRow();
            grid.appendChild(target);
            rows = rowList();
            target = rows[startRow + r];
          }
          cells.forEach(function (val, c) {
            var colIdx = startCol + c;
            if (colIdx < COLS.length) setCell(target, colIdx, val);
          });
        });
        refreshCount();
      });
    });
  }

  /* ── Per-row error highlighting (server round-trip) ──────── */
  function applyRowErrors(errorsById) {
    var rows = rowList();
    var fieldMap = {
      number: ".lg-num", type: ".lg-type", cost: ".lg-cost",
      description: ".lg-desc"
    };
    Object.keys(errorsById).forEach(function (idxStr) {
      var row = rows[parseInt(idxStr, 10)];
      if (!row) return;
      row.classList.add("has-error");
      var fieldErrors = errorsById[idxStr];
      Object.keys(fieldErrors).forEach(function (field) {
        var sel = fieldMap[field];
        var input = sel ? row.querySelector(sel) : null;
        if (input) {
          input.classList.add("has-error");
          input.title = fieldErrors[field];
        }
      });
    });
  }

  /* ── Boot the grid ───────────────────────────────────────── */
  if (grid) {
    readJson("checkpoints-data").forEach(function (c) {
      grid.appendChild(makeRow(c));
    });
    refreshCount();
    applyRowErrors(readJsonObj("checkpoint-errors"));

    var addRow = document.getElementById("addRow");
    if (addRow) {
      addRow.addEventListener("click", function () {
        var row = makeRow();
        grid.appendChild(row);
        refreshCount();
        row.querySelector(".lg-num").focus();
      });
    }
  }

  /* ── Paste-from-spreadsheet modal ────────────────────────── */
  var modal = document.getElementById("pasteModal");
  var pasteArea = document.getElementById("pasteArea");
  var pastePreview = document.getElementById("pastePreview");

  function looksLikeHeader(cells) {
    // First cell of a data row is the КП number; a non-numeric first cell
    // means the user copied the header row too — skip it.
    return cells.length > 0 && !/^\s*-?\d+\s*$/.test(cells[0]);
  }

  function previewRows() {
    var matrix = parseMatrix(pasteArea.value);
    if (matrix.length && looksLikeHeader(matrix[0])) matrix = matrix.slice(1);
    return matrix;
  }

  function updatePreview() {
    if (!pastePreview) return;
    var n = previewRows().length;
    pastePreview.textContent = n ? "Будет добавлено строк: " + n : "";
  }

  function openModal() {
    if (!modal) return;
    modal.hidden = false;
    if (pasteArea) {
      pasteArea.value = "";
      setTimeout(function () { pasteArea.focus(); }, 0);
    }
    updatePreview();
  }

  function closeModal() {
    if (modal) modal.hidden = true;
  }

  var pasteOpen = document.getElementById("pasteOpen");
  if (pasteOpen) pasteOpen.addEventListener("click", openModal);
  if (pasteArea) pasteArea.addEventListener("input", updatePreview);
  if (modal) {
    modal.querySelectorAll("[data-paste-close]").forEach(function (el) {
      el.addEventListener("click", closeModal);
    });
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape" && !modal.hidden) closeModal();
    });
  }

  var pasteApply = document.getElementById("pasteApply");
  if (pasteApply) {
    pasteApply.addEventListener("click", function () {
      var matrix = previewRows();
      if (!matrix.length) { closeModal(); return; }
      var replace = document.getElementById("pasteReplace");
      if (replace && replace.checked) {
        rowList().forEach(function (r) { r.remove(); });
      }
      matrix.forEach(function (cells) {
        var row = makeRow();
        grid.appendChild(row);
        cells.forEach(function (val, c) {
          if (c < COLS.length) setCell(row, c, val);
        });
      });
      refreshCount();
      closeModal();
    });
  }

  /* ── Serialize on submit ─────────────────────────────────── */
  var form = document.getElementById("legendForm");
  if (form && grid) {
    form.addEventListener("submit", function () {
      var rows = rowList().map(function (row) {
        var idAttr = row.dataset.id;
        return {
          id: idAttr != null && idAttr !== "" ? parseInt(idAttr, 10) : null,
          number: parseInt(row.querySelector(".lg-num").value, 10) || 0,
          type: row.querySelector(".lg-type").value,
          cost: parseInt(row.querySelector(".lg-cost").value, 10) || 0,
          description: row.querySelector(".lg-desc").value.trim(),
          is_legend_locked: row.querySelector(".lg-lock").checked
        };
      });
      var field = document.getElementById("checkpointsJson");
      if (field) field.value = JSON.stringify(rows);
    });
  }
})();
