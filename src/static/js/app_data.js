/* App data pages: client-side team search (overview) and event-kind
   filters (team timeline). Server renders everything; this only hides rows. */
(function () {
  "use strict";

  // Overview: filter team rows by start number / name substring.
  var search = document.getElementById("teamSearch");
  var table = document.getElementById("teamsTable");
  if (search && table) {
    search.addEventListener("input", function () {
      var query = search.value.trim().toLowerCase();
      var rows = table.querySelectorAll("tbody tr[data-search]");
      rows.forEach(function (row) {
        var haystack = row.getAttribute("data-search") || "";
        row.hidden = query !== "" && haystack.indexOf(query) === -1;
      });
    });
  }

  // Team timeline: show/hide events by kind.
  var filters = document.getElementById("eventFilters");
  if (filters) {
    filters.addEventListener("change", function () {
      filters.querySelectorAll("input[data-kind]").forEach(function (box) {
        var kind = box.getAttribute("data-kind");
        document.querySelectorAll(".ev-" + kind).forEach(function (row) {
          row.hidden = !box.checked;
        });
      });
    });
  }
})();
