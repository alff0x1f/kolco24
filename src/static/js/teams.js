/* Teams list page — client-side search / category filter / column sort.
 *
 * Reads two embedded JSON blocks rendered by RaceTeamsView.build_context:
 *   #teams-data       — [{num, name, city, parts, cnt, catId, mine, edit?}]
 *   #categories-data  — [{id, label, count, colorIdx}]  (in display order)
 *
 * Counts shown in chips are derived from the actual team rows so they always
 * agree with what the filter renders; categories-data supplies display order,
 * labels and colours (via colorIdx).
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

  // Keep column headings below the search panel when its contents wrap.
  var headEl = pageEl.querySelector(".teams-head");
  if (headEl) {
    function updateHeadHeight() {
      pageEl.style.setProperty(
        "--teams-head-height", headEl.getBoundingClientRect().height + "px"
      );
    }
    updateHeadHeight();
    if (typeof ResizeObserver !== "undefined") {
      new ResizeObserver(updateHeadHeight).observe(headEl);
    } else {
      window.addEventListener("resize", updateHeadHeight);
    }
  }

  var TEAMS = JSON.parse(teamsEl.textContent);
  var CATS = JSON.parse(catsEl.textContent);

  // colorIdx -> colour, single source for chip dots and table badges.
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
  var isAuthenticated = pageEl.getAttribute("data-authenticated") === "true";
  var hasActions = pageEl.getAttribute("data-has-actions") === "true";

  var initial = pageEl.getAttribute("data-initial") || "all";
  var activeCat = initial; // 'all' | 'mine' | '<catId>'
  var query = "";
  var sortKey = "num";
  var sortDir = 1;

  var rowsEl = document.getElementById("teamRows");
  var emptyEl = document.getElementById("emptyState");
  var footEl = document.getElementById("footCount");
  var chipsEl = document.getElementById("catChips");
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
    if (isAuthenticated) {
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

  function setCat(c) {
    activeCat = c;
    syncActive();
    render();
  }

  function syncActive() {
    document.querySelectorAll(".chip").forEach(function (ch) {
      ch.classList.toggle("is-active", ch.dataset.cat === activeCat);
    });
  }

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
    if (t.mine) nameCell += ' <span class="mine-marker">Ваша</span>';
    nameCell += "</div>";
    if (t.parts) {
      nameCell +=
        '<div class="t-parts" title="' + esc(t.parts) + '">' + hl(t.parts) + "</div>";
    }
    var actionCell = "";
    if (hasActions) {
      actionCell = '<td class="col-action">';
      if (t.edit) {
        actionCell +=
          '<a class="team-action" href="' +
          esc(t.edit) +
          '">' +
          esc(t.action || "Редактировать") +
          "</a>";
      }
      actionCell += "</td>";
    }
    return (
      '<tr class="' + (t.mine ? "is-mine" : "") + '">' +
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
      actionCell +
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

  function updateSortIndicators(activeBtn) {
    document.querySelectorAll(".th-sort").forEach(function (b) {
      var th = b.closest("th");
      var arr = b.querySelector(".arr");
      if (b === activeBtn) {
        b.classList.add("sorted");
        if (arr) arr.textContent = sortDir === 1 ? "▼" : "▲";
        if (th) {
          th.setAttribute(
            "aria-sort",
            sortDir === 1 ? "ascending" : "descending"
          );
        }
      } else {
        b.classList.remove("sorted");
        if (arr) arr.textContent = "▼";
        if (th) th.setAttribute("aria-sort", "none");
      }
    });
  }

  document.querySelectorAll(".th-sort").forEach(function (btn) {
    btn.addEventListener("click", function () {
      var k = btn.dataset.sort;
      if (sortKey === k) {
        sortDir *= -1;
      } else {
        sortKey = k;
        sortDir = 1;
      }
      updateSortIndicators(btn);
      render();
    });
  });

  buildChips();
  render();
})();
