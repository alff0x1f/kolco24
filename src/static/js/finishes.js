// Страница финиша гонки — /race/<slug>/finishes/
// Опрашивает JSON (race_finishes_data) раз в 20 с. Состояния команд и прогноз
// считает сервер; здесь только плитки, фильтр, таблица прогноза и списки.
// Опрос, ошибки связи и часы — копия из starts.js (страницы живут отдельно).
(function () {
  "use strict";

  var POLL_MS = 20000;

  var configEl = document.getElementById("raceFinishesConfig");
  if (!configEl) return;
  var config = JSON.parse(configEl.textContent);

  var el = function (id) { return document.getElementById(id); };
  var status = el("rfStatus");
  var searchInput = el("rfSearch");
  var onCourseList = el("rfOnCourseList");
  var finishedList = el("rfFinishedList");

  var data = null;
  var category = null;
  var knownFinished = null;
  var newIds = new Set();
  var timer = null;
  var stopped = false;
  var clockBase = null;

  function make(tag, cls, text) {
    var node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function isFinished(team) { return team.state === "finished"; }
  function isOut(team) { return team.state === "on_course" || team.state === "overdue"; }

  function pad2(n) { return (n < 10 ? "0" : "") + n; }

  function categoryCode(id) {
    var found = data.categories.find(function (c) { return c.id === id; });
    return found ? found.code : "";
  }

  function visibleTeams() {
    if (category === null) return data.teams;
    return data.teams.filter(function (t) { return t.category_id === category; });
  }

  function people(teams) {
    return teams.reduce(function (sum, t) { return sum + t.people; }, 0);
  }

  function setStatus(text, fatal) {
    status.hidden = !text;
    status.textContent = text || "";
    status.classList.toggle("is-fatal", !!fatal);
  }

  // "1:05" before the КВ, "+12 мин" / "+1:20" after it.
  function untilDeadline(team) {
    var minutes = Math.floor((team.deadline_ms - data.server_time_ms) / 60000);
    if (minutes >= 0) return Math.floor(minutes / 60) + ":" + pad2(minutes % 60);
    var late = -minutes;
    return late < 60 ? "+" + late + " мин" : "+" + Math.floor(late / 60) + ":" + pad2(late % 60);
  }

  // ── Плитки ────────────────────────────────────────────
  function renderTiles(teams) {
    var finished = teams.filter(isFinished);
    var out = teams.filter(isOut);
    var overdue = teams.filter(function (t) { return t.state === "overdue"; });
    var long = overdue.filter(function (t) { return t.overdue_long; });
    var noControl = teams.filter(function (t) { return t.state === "no_control"; });

    el("rfFinished").textContent = finished.length + " из " + teams.length;
    el("rfFinishedSub").textContent = teams.length
      ? Math.round((finished.length / teams.length) * 100) + "%" : "";
    el("rfOnCourse").textContent = String(out.length);
    el("rfOnCourseSub").textContent = people(out) + " чел.";
    el("rfOverdue").textContent = String(overdue.length);
    el("rfOverdueSub").textContent = long.length ? "из них давно: " + long.length : "";
    el("rfNoControlTile").hidden = !noControl.length;
    el("rfNoControl").textContent = String(noControl.length);
  }

  // ── Категории ─────────────────────────────────────────
  function renderCategories() {
    var box = el("rfCats");
    box.replaceChildren();
    var counts = {};
    data.teams.forEach(function (t) {
      var c = counts[t.category_id] || (counts[t.category_id] = { total: 0, done: 0 });
      c.total += 1;
      if (isFinished(t)) c.done += 1;
    });
    var shown = data.categories.filter(function (c) { return counts[c.id]; });
    if (shown.length < 2 || (category !== null && !counts[category])) category = null;
    if (shown.length < 2) return;

    function chip(label, value) {
      var button = make("button", "rf-cat" + (category === value ? " is-active" : ""), label);
      button.type = "button";
      button.addEventListener("click", function () {
        category = category === value ? null : value;
        render();
      });
      box.appendChild(button);
    }
    chip("Все", null);
    shown.forEach(function (c) {
      chip(c.code + " " + counts[c.id].done + "/" + counts[c.id].total, c.id);
    });
  }

  // ── Приход по часам ───────────────────────────────────
  function renderTimeline() {
    var box = el("rfForecast");
    box.replaceChildren();
    var rows = data.timeline[category === null ? "all" : String(category)] || [];
    var arrived = 0, expected = 0;
    rows.forEach(function (r) {
      arrived += r.arrived || 0;
      expected += r.expected || 0;
    });
    if (!arrived && expected < 0.05) {
      box.appendChild(make("p", "rf-empty", "Пока никого"));
      return;
    }
    var max = Math.max.apply(null, rows.map(function (r) { return (r.arrived || 0) + (r.expected || 0); }));

    var head = make("div", "rf-tl-row rf-tl-head");
    ["Час", "Пришло", "Ожидается", ""].forEach(function (text) { head.appendChild(make("span", "", text)); });
    box.appendChild(head);

    rows.forEach(function (r) {
      var line = make("div", "rf-tl-row" + (r.now ? " is-now" : ""));
      line.appendChild(make("span", "rf-tl-hour", r.from ? r.from + "–" + r.to : "до " + r.to));
      line.appendChild(make("span", "rf-tl-num", r.arrived === null ? "" : String(r.arrived)));
      line.appendChild(make("span", "rf-tl-num", r.expected === null ? "" : "≈ " + Math.round(r.expected)));
      var bar = make("div", "rf-tl-bar");
      [["rf-tl-arrived", r.arrived || 0], ["rf-tl-expected", r.expected || 0]].forEach(function (part) {
        var seg = make("div", part[0]);
        seg.style.width = (max ? (part[1] / max) * 100 : 0) + "%";
        bar.appendChild(seg);
      });
      line.appendChild(bar);
      box.appendChild(line);
    });

    var sum = make("div", "rf-tl-row rf-tl-total");
    sum.appendChild(make("span", "rf-tl-hour", "Всего"));
    sum.appendChild(make("span", "rf-tl-num", String(arrived)));
    sum.appendChild(make("span", "rf-tl-num", "≈ " + Math.round(Math.round(expected * 10) / 10)));
    box.appendChild(sum);
  }

  function updatePrintLink() {
    el("rfPrint").href = config.printUrl + (category === null ? "" : "?category=" + category);
  }

  // ── Списки ────────────────────────────────────────────
  function row(cls, cells) {
    var node = make("div", cls);
    cells.forEach(function (cell) { node.appendChild(make("span", cell[0], cell[1])); });
    return node;
  }

  function fillList(list, nodes, emptyText) {
    var scroll = list.scrollTop;
    list.replaceChildren();
    if (!nodes.length) list.appendChild(make("p", "rf-empty", emptyText));
    nodes.forEach(function (node) { list.appendChild(node); });
    list.scrollTop = scroll;
  }

  // Overdue teams have the earliest КВ, so they sort to the top; the long
  // overdue ones (probably dropped out) go to the bottom.
  function byDeadline(a, b) {
    return (a.overdue_long - b.overdue_long) || (a.deadline_ms - b.deadline_ms) || (a.id - b.id);
  }

  function renderLists(teams) {
    var query = searchInput.value.trim().toLowerCase();
    var out = teams.filter(isOut);
    var finished = teams.filter(isFinished).sort(function (a, b) {
      return b.finish_time_ms - a.finish_time_ms || b.id - a.id;
    });
    el("rfOnCourseCount").textContent = String(out.length);
    el("rfFinishedCount").textContent = String(finished.length);

    var matching = out.filter(function (t) {
      if (!query) return true;
      return t.start_number.toLowerCase().indexOf(query) !== -1 || t.name.toLowerCase().indexOf(query) !== -1;
    }).sort(byDeadline);

    fillList(onCourseList, matching.map(function (t) {
      var cls = "rf-row rf-row-wide";
      if (t.state === "overdue") cls += t.overdue_long ? " is-long" : " is-overdue";
      return row(cls, [
        ["rf-row-num", "№" + t.start_number],
        ["rf-row-cat", categoryCode(t.category_id)],
        ["rf-row-name", t.name],
        ["rf-row-cat", t.people + " чел."],
        ["rf-row-left", "КВ " + t.deadline + " · " + (t.overdue_long ? "давно" : untilDeadline(t))],
      ]);
    }), query ? "Ничего не найдено" : "На дистанции никого");

    fillList(finishedList, finished.map(function (t) {
      return row("rf-row" + (newIds.has(t.id) ? " is-new" : ""), [
        ["rf-row-time", t.finish_time],
        ["rf-row-num", "№" + t.start_number],
        ["rf-row-name", t.name],
        ["rf-row-cat", categoryCode(t.category_id)],
      ]);
    }), "Финишей пока нет");
  }

  function render() {
    if (!data) return;
    renderCategories();
    var teams = visibleTeams();
    renderTiles(teams);
    renderTimeline();
    updatePrintLink();
    renderLists(teams);
    newIds = new Set();
  }

  // ── Часы ──────────────────────────────────────────────
  // Server wall time advanced by the browser's monotonic clock: no time-zone
  // logic here, and a wrong laptop clock doesn't matter.
  function tickClock() {
    if (!clockBase) return;
    var secs = clockBase.secs + Math.floor((performance.now() - clockBase.at) / 1000);
    secs = ((secs % 86400) + 86400) % 86400;
    el("rfClock").textContent =
      pad2(Math.floor(secs / 3600)) + ":" + pad2(Math.floor(secs / 60) % 60) + ":" + pad2(secs % 60);
  }

  // ── Опрос ─────────────────────────────────────────────
  // The next request is scheduled only after the previous one settles, so a
  // slow stale response can never land after a newer one.
  function stop(text) {
    stopped = true;
    clearTimeout(timer);
    setStatus(text, true);
  }

  function poll() {
    fetch(config.dataUrl, { credentials: "same-origin", headers: { Accept: "application/json" } })
      .then(function (resp) {
        if (resp.redirected || resp.status === 403 || resp.status === 404) {
          stop("Сессия истекла или гонка недоступна — перезагрузите страницу");
          return;
        }
        if (!resp.ok) throw new Error("HTTP " + resp.status);
        return resp.json().then(accept, function () {
          stop("Сервер вернул не то — перезагрузите страницу");
        });
      })
      .catch(function () {
        if (!stopped) setStatus("Нет связи с сервером, данные на " + (data ? data.server_time : "—"));
      })
      .then(function () {
        if (!stopped) timer = setTimeout(poll, POLL_MS);
      });
  }

  function accept(payload) {
    var ids = new Set(payload.teams.filter(isFinished).map(function (t) { return t.id; }));
    if (knownFinished !== null) {
      ids.forEach(function (id) { if (!knownFinished.has(id)) newIds.add(id); });
    }
    knownFinished = ids;
    data = payload;
    var parts = payload.server_time.split(":").map(Number);
    clockBase = { secs: parts[0] * 3600 + parts[1] * 60 + parts[2], at: performance.now() };
    tickClock();
    setStatus("");
    render();
  }

  searchInput.addEventListener("input", function () {
    if (data) renderLists(visibleTeams());
  });

  setInterval(tickClock, 1000);
  poll();
})();
