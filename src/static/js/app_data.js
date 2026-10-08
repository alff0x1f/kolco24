/* App data pages: client-side team search (overview) and event-kind/device
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

  // Team timeline: show/hide events by kind and by device. A row without a
  // device (judge scan, start/finish) follows only the kind filter.
  var filters = document.getElementById("eventFilters");
  if (filters) {
    var checkedValues = function (attr) {
      var values = {};
      filters.querySelectorAll("input[" + attr + "]").forEach(function (box) {
        values[box.getAttribute(attr)] = box.checked;
      });
      return values;
    };
    filters.addEventListener("change", function () {
      var kinds = checkedValues("data-kind");
      var devices = checkedValues("data-device");
      document.querySelectorAll("tr.ev").forEach(function (row) {
        var device = row.getAttribute("data-device");
        var deviceOn = !device || devices[device] !== false;
        row.hidden = !(kinds[row.getAttribute("data-kind")] && deviceOn);
      });
    });
  }
})();
