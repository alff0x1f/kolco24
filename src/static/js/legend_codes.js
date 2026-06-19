// Legend codes page — /race/<slug>/legend/codes/.
// Read-only: builds a CSV (header nfc_uid,number,code) from the rendered table
// and copies it to the clipboard. Mirrors `manage.py export_legend_codes`.
(function () {
  "use strict";

  var btn = document.getElementById("copyCsv");
  var table = document.getElementById("codesTable");
  if (!btn || !table) return;

  // RFC-4180 quoting: wrap in quotes and double inner quotes when the value
  // holds a comma, quote, or newline.
  function csvCell(value) {
    if (/[",\n\r]/.test(value)) {
      return '"' + value.replace(/"/g, '""') + '"';
    }
    return value;
  }

  function buildCsv() {
    var lines = ["nfc_uid,number,code"];
    var rows = table.querySelectorAll("tbody tr");
    rows.forEach(function (tr) {
      if (tr.classList.contains("empty")) return;
      var cells = tr.querySelectorAll("td");
      if (cells.length < 3) return;
      lines.push(
        [
          csvCell(cells[0].textContent.trim()),
          csvCell(cells[1].textContent.trim()),
          csvCell(cells[2].textContent.trim()),
        ].join(",")
      );
    });
    return lines.join("\r\n");
  }

  function flash(text) {
    var prev = btn.textContent;
    btn.textContent = text;
    btn.disabled = true;
    setTimeout(function () {
      btn.textContent = prev;
      btn.disabled = false;
    }, 1500);
  }

  btn.addEventListener("click", function () {
    var csv = buildCsv();
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(csv).then(
        function () {
          flash("Скопировано ✓");
        },
        function () {
          flash("Не удалось");
        }
      );
    } else {
      // Fallback for non-secure contexts.
      var ta = document.createElement("textarea");
      ta.value = csv;
      ta.style.position = "fixed";
      ta.style.opacity = "0";
      document.body.appendChild(ta);
      ta.select();
      try {
        document.execCommand("copy");
        flash("Скопировано ✓");
      } catch (e) {
        flash("Не удалось");
      }
      document.body.removeChild(ta);
    }
  });
})();
