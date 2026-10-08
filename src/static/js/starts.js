// Страница стартов гонки — /race/<slug>/starts/
// Опрашивает JSON (race_starts_data) раз в 20 с и всё считает здесь же:
// плитки, фильтр по категории, график, списки «Ждём» / «Стартовали».
(function () {
  "use strict";

  var POLL_MS = 20000;
  var PACE_WINDOW_MS = 15 * 60 * 1000;
  var CHART_HEIGHT = 220;
  var SVG_NS = "http://www.w3.org/2000/svg";

  var configEl = document.getElementById("raceStartsConfig");
  if (!configEl) return;
  var config = JSON.parse(configEl.textContent);

  var el = function (id) { return document.getElementById(id); };
  var status = el("rsStatus");
  var searchInput = el("rsSearch");
  var waitingList = el("rsWaitingList");
  var startedList = el("rsStartedList");
  var chart = el("rsChart");

  var data = null;
  var category = null;
  var knownStarted = null;
  var newIds = new Set();
  var timer = null;

  function make(tag, cls, text) {
    var node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function svg(tag, attrs, text) {
    var node = document.createElementNS(SVG_NS, tag);
    Object.keys(attrs).forEach(function (key) { node.setAttribute(key, attrs[key]); });
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function started(team) { return team.start_time_ms !== null; }

  function startedInOrder(teams) {
    return teams.filter(started).sort(function (a, b) {
      return a.start_time_ms - b.start_time_ms || a.id - b.id;
    });
  }

  function plural(n, one, few, many) {
    var mod10 = n % 10, mod100 = n % 100;
    if (mod10 === 1 && mod100 !== 11) return one;
    if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) return few;
    return many;
  }

  function ago(ms) {
    var minutes = Math.max(0, Math.floor(ms / 60000));
    if (minutes < 60) return minutes + " мин назад";
    return Math.floor(minutes / 60) + " ч " + (minutes % 60) + " мин назад";
  }

  function categoryCode(id) {
    var found = data.categories.find(function (c) { return c.id === id; });
    return found ? found.code : "";
  }

  function visibleTeams() {
    if (category === null) return data.teams;
    return data.teams.filter(function (t) { return t.category_id === category; });
  }

  function setStatus(text, fatal) {
    status.hidden = !text;
    status.textContent = text || "";
    status.classList.toggle("is-fatal", !!fatal);
  }

  // ── Плитки ────────────────────────────────────────────
  function renderTiles(teams, done) {
    var total = teams.length;
    var now = data.server_time_ms;
    el("rsStarted").textContent = done.length + " из " + total;
    el("rsStartedSub").textContent = total ? Math.round((done.length / total) * 100) + "%" : "";
    el("rsWaiting").textContent = String(total - done.length);

    var last = done.length ? done[done.length - 1] : null;
    el("rsLast").textContent = last ? ago(now - last.start_time_ms) : "—";
    el("rsLastSub").textContent = last ? last.start_time + " · №" + last.start_number : "";

    var pace = done.filter(function (t) { return t.start_time_ms >= now - PACE_WINDOW_MS; }).length;
    el("rsPace").textContent = pace + " " + plural(pace, "команда", "команды", "команд");
  }

  // ── Категории ─────────────────────────────────────────
  function renderCategories() {
    var box = el("rsCats");
    box.replaceChildren();
    var counts = {};
    data.teams.forEach(function (t) {
      var c = counts[t.category_id] || (counts[t.category_id] = { total: 0, done: 0 });
      c.total += 1;
      if (started(t)) c.done += 1;
    });
    var shown = data.categories.filter(function (c) { return counts[c.id]; });
    if (category !== null && !counts[category]) category = null;
    if (shown.length < 2) return;

    function chip(label, value) {
      var button = make("button", "rs-cat" + (category === value ? " is-active" : ""), label);
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

  // ── График ────────────────────────────────────────────
  function renderChart(total, done) {
    chart.replaceChildren();
    if (!done.length) {
      chart.appendChild(make("p", "rs-empty", "Стартов пока нет"));
      return;
    }
    var width = Math.max(chart.clientWidth, 280);
    var pad = { left: 36, right: 12, top: 12, bottom: 24 };
    var t0 = done[0].start_time_ms;
    var t1 = Math.max(done[done.length - 1].start_time_ms, data.server_time_ms);
    if (t1 <= t0) t1 = t0 + 60000;
    var yMax = Math.max(total, 1);
    var x = function (t) { return pad.left + ((t - t0) / (t1 - t0)) * (width - pad.left - pad.right); };
    var y = function (n) { return pad.top + (1 - n / yMax) * (CHART_HEIGHT - pad.top - pad.bottom); };

    var path = "M" + x(t0) + "," + y(0);
    done.forEach(function (t, i) {
      path += " H" + x(t.start_time_ms) + " V" + y(i + 1);
    });
    path += " H" + x(t1);

    var root = svg("svg", {
      viewBox: "0 0 " + width + " " + CHART_HEIGHT,
      role: "img",
      "aria-label": "Стартовало команд по времени",
    });
    root.appendChild(svg("line", { class: "rs-axis", x1: pad.left, x2: width - pad.right, y1: y(0), y2: y(0) }));
    root.appendChild(svg("line", { class: "rs-goal", x1: pad.left, x2: width - pad.right, y1: y(total), y2: y(total) }));
    root.appendChild(svg("path", { class: "rs-area", d: path + " V" + y(0) + " Z" }));
    root.appendChild(svg("path", { class: "rs-line", d: path }));
    root.appendChild(svg("text", { x: pad.left - 6, y: y(0) + 4, "text-anchor": "end" }, "0"));
    root.appendChild(svg("text", { x: pad.left - 6, y: y(total) + 4, "text-anchor": "end" }, String(total)));
    root.appendChild(svg("text", { x: pad.left, y: CHART_HEIGHT - 6 }, done[0].start_time.slice(0, 5)));
    root.appendChild(svg("text", { x: width - pad.right, y: CHART_HEIGHT - 6, "text-anchor": "end" }, data.server_time));
    chart.appendChild(root);
  }

  // ── Списки ────────────────────────────────────────────
  function row(cells, isNew) {
    var node = make("div", "rs-row" + (isNew ? " is-new" : ""));
    cells.forEach(function (cell) { node.appendChild(make("span", cell[0], cell[1])); });
    return node;
  }

  function fillList(list, nodes, emptyText) {
    var scroll = list.scrollTop;
    list.replaceChildren();
    if (!nodes.length) list.appendChild(make("p", "rs-empty", emptyText));
    nodes.forEach(function (node) { list.appendChild(node); });
    list.scrollTop = scroll;
  }

  function renderLists(teams, done) {
    var query = searchInput.value.trim().toLowerCase();
    var waiting = teams.filter(function (t) {
      if (started(t)) return false;
      if (!query) return true;
      return t.start_number.toLowerCase().indexOf(query) !== -1 || t.name.toLowerCase().indexOf(query) !== -1;
    });
    el("rsWaitingCount").textContent = String(teams.length - done.length);
    el("rsStartedCount").textContent = String(done.length);

    fillList(waitingList, waiting.map(function (t) {
      return row([
        ["rs-row-num", "№" + t.start_number],
        ["rs-row-cat", categoryCode(t.category_id)],
        ["rs-row-name", t.name],
        ["rs-row-cat", t.paid_people + " чел."],
      ]);
    }), query ? "Ничего не найдено" : "Все стартовали");

    fillList(startedList, done.slice().reverse().map(function (t) {
      return row([
        ["rs-row-time", t.start_time],
        ["rs-row-num", "№" + t.start_number],
        ["rs-row-name", t.name],
        ["rs-row-cat", categoryCode(t.category_id)],
      ], newIds.has(t.id));
    }), "Стартов пока нет");
  }

  function render() {
    if (!data) return;
    renderCategories();
    var teams = visibleTeams();
    var done = startedInOrder(teams);
    renderTiles(teams, done);
    renderChart(teams.length, done);
    renderLists(teams, done);
    newIds = new Set();
  }

  // ── Опрос ─────────────────────────────────────────────
  function stop(text) {
    clearInterval(timer);
    timer = null;
    setStatus(text, true);
  }

  function clock() {
    return new Date().toLocaleTimeString("ru-RU");
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
        if (timer !== null) setStatus("Нет связи с сервером, данные на " + (data ? data.receivedAt : "—"));
      });
  }

  function accept(payload) {
    var ids = new Set(payload.teams.filter(started).map(function (t) { return t.id; }));
    if (knownStarted !== null) {
      ids.forEach(function (id) { if (!knownStarted.has(id)) newIds.add(id); });
    }
    knownStarted = ids;
    payload.receivedAt = clock();
    data = payload;
    setStatus("");
    render();
  }

  searchInput.addEventListener("input", function () {
    if (!data) return;
    var teams = visibleTeams();
    renderLists(teams, startedInOrder(teams));
  });

  var resizeTimer = null;
  window.addEventListener("resize", function () {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(function () {
      if (!data) return;
      var teams = visibleTeams();
      renderChart(teams.length, startedInOrder(teams));
    }, 150);
  });

  timer = setInterval(poll, POLL_MS);
  poll();
})();
