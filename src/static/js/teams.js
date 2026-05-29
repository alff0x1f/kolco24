/* Teams list page — client-side search / category filter / column sort.
 *
 * Reads two embedded JSON blocks rendered by RaceTeamsView.build_context:
 *   #teams-data       — [{num, name, city, parts, cnt, catId, mine, edit?}]
 *   #categories-data  — [{id, label, count, colorIdx}]  (in display order)
 *
 * Counts shown in chips / breakdown are derived from the actual team rows so
 * they always agree with what the filter renders; categories-data supplies the
 * display order, labels and colours (via colorIdx).
 *
 * Category ids need explicit String() coercion: data-initial is a string while
 * JSON catId is an int, so "7" === 7 would be false.
 */
(function () {
  "use strict";

  var pageEl = document.querySelector(".teams-page");
  var teamsEl = document.getElementById("teams-data");
  var catsEl = document.getElementById("categories-data");
  if (!pageEl || !teamsEl || !catsEl) return;

  var TEAMS = JSON.parse(teamsEl.textContent);
  var CATS = JSON.parse(catsEl.textContent);

  // colorIdx -> colour, single source for chip dots and breakdown bars.
  var CAT_COLORS = [
    "#2a5288", "#d99a2b", "#2a8fb0", "#c2589a",
    "#4582EC", "#02B875", "#7c4ddb", "#d4633f",
  ];

  // catId (as String) -> {label, colorIdx, order}.
  var catMeta = {};
  CATS.forEach(function (c, i) {
    catMeta[String(c.id)] = { label: c.label, colorIdx: c.colorIdx, order: i };
  });

  // Live counts from the rendered rows.
  var counts = {};
  TEAMS.forEach(function (t) {
    var k = String(t.catId);
    counts[k] = (counts[k] || 0) + 1;
  });
  var total = TEAMS.length;
  var hasMine = TEAMS.some(function (t) {
    return t.mine === true;
  });
  var maxCount = 0;
  CATS.forEach(function (c) {
    var n = counts[String(c.id)] || 0;
    if (n > maxCount) maxCount = n;
  });

  var initial = pageEl.getAttribute("data-initial") || "all";
  var activeCat = initial; // 'all' | 'mine' | '<catId>'
  var query = "";
  var sortKey = "num";
  var sortDir = 1;

  var rowsEl = document.getElementById("teamRows");
  var emptyEl = document.getElementById("emptyState");
  var footEl = document.getElementById("footCount");
  var chipsEl = document.getElementById("catChips");
  var brkEl = document.getElementById("brk");
  var tableEl = document.querySelector(".teams-table");
  var searchEl = document.getElementById("searchInput");

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"]/g, function (m) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[m];
    });
  }

  // Highlight query matches (operates on already-escaped text).
  function hl(s) {
    s = esc(s);
    if (!query) return s;
    var lc = s.toLowerCase();
    var q = query.toLowerCase();
    var out = "";
    var i = 0;
    var idx;
    while ((idx = lc.indexOf(q, i)) !== -1) {
      out += s.slice(i, idx) + "<mark>" + s.slice(idx, idx + q.length) + "</mark>";
      i = idx + q.length;
    }
    return out + s.slice(i);
  }

  function plural(n) {
    var a = Math.abs(n) % 100;
    var b = n % 10;
    if (a > 10 && a < 20) return n + " команд";
    if (b > 1 && b < 5) return n + " команды";
    if (b === 1) return n + " команда";
    return n + " команд";
  }

  function chip(label, val, count, dotColor) {
    var b = document.createElement("button");
    b.type = "button";
    b.className = "chip" + (val === activeCat ? " is-active" : "");
    b.dataset.cat = val;
    var html = "";
    if (dotColor) {
      html += '<span class="dot" style="background:' + dotColor + '"></span>';
    }
    html += esc(label) + ' <span class="c">' + count + "</span>";
    b.innerHTML = html;
    b.addEventListener("click", function () {
      setCat(val);
    });
    return b;
  }

  function buildChips() {
    chipsEl.innerHTML = "";
    chipsEl.appendChild(chip("Все", "all", total, null));
    if (hasMine) {
      var mineCount = TEAMS.filter(function (t) {
        return t.mine === true;
      }).length;
      chipsEl.appendChild(chip("Мои", "mine", mineCount, null));
    }
    CATS.forEach(function (c) {
      var n = counts[String(c.id)] || 0;
      if (!n) return;
      chipsEl.appendChild(chip(c.label, String(c.id), n, CAT_COLORS[c.colorIdx]));
    });
  }

  function buildBrk() {
    brkEl.innerHTML = "";
    CATS.forEach(function (c) {
      var n = counts[String(c.id)] || 0;
      if (!n) return;
      var val = String(c.id);
      var btn = document.createElement("button");
      btn.type = "button";
      btn.className = "brk-row" + (val === activeCat ? " is-active" : "");
      btn.dataset.cat = val;
      var pct = maxCount ? Math.round((n / maxCount) * 100) : 0;
      // Full category name shown dim next to the short label; skip it when the
      // label already is the full name (no short_name) to avoid duplication.
      var full = c.name && c.name !== c.label ? c.name : "";
      btn.innerHTML =
        '<div class="brk-top"><span class="nm">' +
        esc(c.label) +
        "</span>" +
        (full
          ? '<span class="brk-full" title="' + esc(full) + '">' + esc(full) + "</span>"
          : "") +
        '<span class="vl">' +
        n +
        "</span></div>" +
        '<div class="brk-bar"><i style="width:' +
        pct +
        '%"></i></div>';
      btn.addEventListener("click", function () {
        setCat(activeCat === val ? "all" : val);
      });
      brkEl.appendChild(btn);
    });
  }

  function setCat(c) {
    activeCat = c;
    syncActive();
    render();
  }

  function syncActive() {
    document.querySelectorAll(".chip").forEach(function (ch) {
      ch.classList.toggle("is-active", ch.dataset.cat === activeCat);
    });
    document.querySelectorAll(".brk-row").forEach(function (r) {
      r.classList.toggle("is-active", r.dataset.cat === activeCat);
    });
  }

  var EDIT_SVG =
    '<svg width="13" height="13" viewBox="0 0 20 20" fill="none" ' +
    'stroke="currentColor" stroke-width="1.7" stroke-linecap="round" ' +
    'stroke-linejoin="round"><path d="M4 13.5V16h2.5L15 7.5 12.5 5 4 13.5z"/>' +
    '<path d="m11.5 6 2.5 2.5"/></svg>';

  var CNT_SVG =
    '<svg width="15" height="15" viewBox="0 0 20 20" fill="none" ' +
    'stroke="currentColor" stroke-width="1.7"><circle cx="10" cy="7" r="3"/>' +
    '<path d="M4 17c0-3 2.7-5 6-5s6 2 6 5"/></svg>';

  function matchesCat(t) {
    if (activeCat === "all") return true;
    if (activeCat === "mine") return t.mine === true;
    return String(t.catId) === activeCat;
  }

  function compare(a, b) {
    var r;
    if (sortKey === "num") {
      r = +a.num - +b.num;
    } else if (sortKey === "cnt") {
      r = (parseFloat(a.cnt) || 0) - (parseFloat(b.cnt) || 0);
    } else if (sortKey === "cat") {
      var oa = catMeta[String(a.catId)] ? catMeta[String(a.catId)].order : 0;
      var ob = catMeta[String(b.catId)] ? catMeta[String(b.catId)].order : 0;
      r = oa - ob || +a.num - +b.num;
    } else {
      r = (a[sortKey] || "").localeCompare(b[sortKey] || "", "ru");
    }
    return r * sortDir;
  }

  function rowHtml(t) {
    var meta = catMeta[String(t.catId)];
    var ci = meta ? meta.colorIdx : 0;
    var catLabel = meta ? meta.label : "";
    var nameCell = '<div class="t-name">' + hl(t.name);
    if (t.edit) {
      nameCell +=
        ' <a class="edit" href="' +
        esc(t.edit) +
        '" title="Редактировать">' +
        EDIT_SVG +
        "</a>";
    }
    nameCell += "</div>";
    if (t.parts) {
      nameCell +=
        '<div class="t-parts" title="' + esc(t.parts) + '">' + hl(t.parts) + "</div>";
    }
    return (
      "<tr>" +
      '<td class="col-num"><span class="bib">' +
      esc(t.num) +
      "</span></td>" +
      '<td class="col-team">' +
      nameCell +
      "</td>" +
      '<td class="col-cat"><span class="cat-badge cat-' +
      ci +
      '">' +
      esc(catLabel) +
      "</span></td>" +
      '<td class="col-city t-city">' +
      hl(t.city) +
      "</td>" +
      '<td class="col-cnt"><span class="cnt-badge">' +
      CNT_SVG +
      esc(t.cnt) +
      "</span></td>" +
      "</tr>"
    );
  }

  function render() {
    // Hide the category column only when a single category is selected.
    var singleCat = activeCat !== "all" && activeCat !== "mine";
    tableEl.classList.toggle("hide-cat", singleCat);

    var list = TEAMS.filter(matchesCat);
    if (query) {
      var q = query.toLowerCase();
      list = list.filter(function (t) {
        return (
          (t.name + " " + t.city + " " + t.parts + " " + t.num)
            .toLowerCase()
            .indexOf(q) !== -1
        );
      });
    }
    list.sort(compare);

    if (!list.length) {
      rowsEl.innerHTML = "";
      emptyEl.hidden = false;
    } else {
      emptyEl.hidden = true;
      rowsEl.innerHTML = list.map(rowHtml).join("");
    }

    if (activeCat === "all" && !query) {
      footEl.textContent = "Показаны все " + plural(total);
    } else {
      footEl.textContent = "Найдено: " + plural(list.length);
    }
  }

  if (searchEl) {
    var deb;
    searchEl.addEventListener("input", function (e) {
      clearTimeout(deb);
      var val = e.target.value;
      deb = setTimeout(function () {
        query = val.trim();
        render();
      }, 120);
    });
  }

  var resetEl = document.getElementById("resetCat");
  if (resetEl) {
    resetEl.addEventListener("click", function (e) {
      e.preventDefault();
      query = "";
      if (searchEl) searchEl.value = "";
      setCat("all");
    });
  }

  document.querySelectorAll("th[data-sort]").forEach(function (th) {
    th.addEventListener("click", function () {
      var k = th.dataset.sort;
      if (sortKey === k) {
        sortDir *= -1;
      } else {
        sortKey = k;
        sortDir = 1;
      }
      document.querySelectorAll("th[data-sort]").forEach(function (h) {
        h.classList.remove("sorted");
        var arr = h.querySelector(".arr");
        if (arr) arr.textContent = "▼";
      });
      th.classList.add("sorted");
      var arrEl = th.querySelector(".arr");
      if (arrEl) arrEl.textContent = sortDir === 1 ? "▼" : "▲";
      render();
    });
  });

  buildChips();
  buildBrk();
  render();
})();
