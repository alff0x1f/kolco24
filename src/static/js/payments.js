/* Страница платежей гонки — фильтры, сортировка, итоги.
 *
 * Читает три JSON-острова, отрисованных RacePaymentsView:
 *   #payments-data — строки реестра (apps/race/finance.py:payment_rows)
 *   #extras-data   — каталог доп-услуг гонки, в порядке отображения
 *   #payments-config — {teamUrlTemplate}
 *
 * Все даты приходят готовыми строками: группировать по дням в браузере нельзя
 * — у него своя таймзона, и суммы по дням разошлись бы с CSV.
 */
(function () {
  "use strict";

  var pageEl = document.querySelector(".race-payments");
  var dataEl = document.getElementById("payments-data");
  var extrasEl = document.getElementById("extras-data");
  var configEl = document.getElementById("payments-config");
  if (!pageEl || !dataEl || !extrasEl || !configEl) return;

  var ROWS = JSON.parse(dataEl.textContent);
  var EXTRAS = JSON.parse(extrasEl.textContent);
  var CONFIG = JSON.parse(configEl.textContent);

  var rowsEl = document.getElementById("payRows");
  var tilesEl = document.getElementById("payTiles");
  var breakdownEl = document.getElementById("payBreakdown");
  var dailyEl = document.getElementById("payDaily");
  var countEl = document.getElementById("payCount");
  var searchEl = document.getElementById("paySearch");
  var statusEl = document.getElementById("payStatus");
  var exportEl = document.getElementById("payExport");

  var status = "done";
  var query = "";
  var sortKey = "paid_sort";
  var sortDir = -1;

  function esc(value) {
    return String(value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function money(value) {
    var rounded = Math.round(value * 100) / 100;
    return rounded.toLocaleString("ru-RU", { maximumFractionDigits: 2 }) + " ₽";
  }

  function num(value) {
    return (Math.round(value * 100) / 100).toLocaleString("ru-RU");
  }

  function teamUrl(id) {
    return CONFIG.teamUrlTemplate.replace("{team_id}", id);
  }

  function visible() {
    var list = ROWS.filter(function (row) {
      return status === "all" || row.status === status;
    });
    if (query) {
      var q = query.toLowerCase();
      list = list.filter(function (row) {
        return (
          (row.team_name + " " + row.order_id).toLowerCase().indexOf(q) !== -1
        );
      });
    }
    return list.sort(function (a, b) {
      var x = a[sortKey];
      var y = b[sortKey];
      if (typeof x === "string" || typeof y === "string") {
        return String(x).localeCompare(String(y), "ru") * sortDir;
      }
      return (x - y) * sortDir;
    });
  }

  function rowHtml(row) {
    return (
      '<tr class="st-' + row.status + '">' +
      "<td>" + esc(row.paid_at) + "</td>" +
      '<td><a href="' + teamUrl(row.team_id) + '">' + esc(row.team_name) + "</a></td>" +
      "<td>" + esc(row.category) + "</td>" +
      '<td><span class="pay-status">' + esc(row.status_label) + "</span></td>" +
      '<td class="num">' + num(row.paid_for) + "</td>" +
      '<td class="num">' + num(row.cost_per_person) + "</td>" +
      "<td>" + esc(row.extras_label) + "</td>" +
      "<td>" + (row.promo ? esc(row.promo) : "—") + "</td>" +
      '<td class="num">' + (row.discount ? num(row.discount) : "—") + "</td>" +
      '<td class="num strong">' + num(row.amount) + "</td>" +
      '<td class="order">' + esc(row.order_id) + "</td>" +
      "</tr>"
    );
  }

  function tile(label, value, mod) {
    return (
      '<div class="pay-tile' + (mod ? " " + mod : "") + '">' +
      '<div class="pay-tile-value">' + value + "</div>" +
      '<div class="pay-tile-label">' + label + "</div>" +
      "</div>"
    );
  }

  function renderTiles(list) {
    var done = list.filter(function (row) {
      return row.status === "done";
    });
    var refunded = list.filter(function (row) {
      return row.status === "cancel";
    });
    // Возврат переводит платёж done → cancel (settlement.py:refund_payment),
    // поэтому «осталось» — это уже сумма по done, а не done минус возвраты.
    // Брутто восстанавливается обратным сложением: cancel — всегда деньги,
    // которые когда-то пришли.
    var kept = sum(done, "amount");
    var back = sum(refunded, "amount");
    tilesEl.innerHTML =
      tile("Поступило", money(kept + back)) +
      tile("Возвращено", back ? "−" + money(back) : money(0), "is-back") +
      tile("Осталось", money(kept), "is-total") +
      tile("Платежей", num(done.length)) +
      tile("Участников оплачено", num(sum(done, "paid_for"))) +
      tile("Скидок", money(sum(done, "discount"))) +
      tile("Средний чек", money(done.length ? kept / done.length : 0));
  }

  function sum(list, key) {
    return list.reduce(function (acc, row) {
      return acc + row[key];
    }, 0);
  }

  function breakdownRow(label, value, mod) {
    return (
      '<tr class="' + (mod || "") + '">' +
      "<th>" + esc(label) + "</th>" +
      '<td class="num">' + value + "</td>" +
      "</tr>"
    );
  }

  function renderBreakdown(list) {
    var done = list.filter(function (row) {
      return row.status === "done";
    });
    var html = breakdownRow("Участие", money(sum(done, "fee_sum")));
    EXTRAS.forEach(function (extra) {
      var count = 0;
      done.forEach(function (row) {
        count += row.extras[extra.code] || 0;
      });
      var revenue = 0;
      done.forEach(function (row) {
        revenue += row.extras_money[extra.code] || 0;
      });
      html += breakdownRow(
        extra.name + (count ? " ×" + num(count) : ""),
        money(revenue)
      );
    });
    var discount = sum(done, "discount");
    if (discount) {
      html += breakdownRow("Скидка", "−" + money(discount), "is-minus");
    }
    html += breakdownRow("Итого", money(sum(done, "amount")), "is-total");
    breakdownEl.innerHTML = html;
  }

  function renderDaily(list) {
    var byDate = {};
    list
      .filter(function (row) {
        return row.status === "done" && row.paid_date;
      })
      .forEach(function (row) {
        var day = byDate[row.paid_date] || { sum: 0, count: 0 };
        day.sum += row.amount;
        day.count += 1;
        byDate[row.paid_date] = day;
      });
    var days = Object.keys(byDate).sort();
    if (!days.length) {
      dailyEl.innerHTML = '<p class="pay-empty">Нет оплаченных платежей.</p>';
      return;
    }
    var max = days.reduce(function (acc, day) {
      return Math.max(acc, byDate[day].sum);
    }, 0);
    // Дней может быть сотня (регистрация открыта месяцами), поэтому панель
    // ограничена по высоте в CSS и прокручивается к последним дням — там
    // идёт актуальная активность.
    dailyEl.innerHTML = days
      .map(function (day) {
        var item = byDate[day];
        var width = max ? (item.sum / max) * 100 : 0;
        return (
          '<div class="pay-day">' +
          '<span class="pay-day-date">' + esc(dayLabel(day)) + "</span>" +
          '<span class="pay-day-bar"><i style="width:' + width.toFixed(1) + '%"></i></span>' +
          '<span class="pay-day-sum">' + money(item.sum) + "</span>" +
          '<span class="pay-day-count">' + num(item.count) + "</span>" +
          "</div>"
        );
      })
      .join("");
    dailyEl.scrollTop = dailyEl.scrollHeight;
  }

  function dayLabel(iso) {
    var parts = iso.split("-");
    return parts[2] + "." + parts[1] + "." + parts[0].slice(2);
  }

  function render() {
    var list = visible();
    rowsEl.innerHTML = list.length
      ? list.map(rowHtml).join("")
      : '<tr class="pay-none"><td colspan="11">Ничего не найдено.</td></tr>';
    countEl.textContent = "Показано: " + num(list.length) + " из " + num(ROWS.length);
    renderTiles(list);
    renderBreakdown(list);
    renderDaily(list);
  }

  statusEl.addEventListener("click", function (event) {
    var button = event.target.closest(".pay-chip");
    if (!button) return;
    status = button.dataset.status;
    statusEl.querySelectorAll(".pay-chip").forEach(function (chip) {
      chip.classList.toggle("is-on", chip === button);
    });
    // Поиск и сортировка в выгрузку не переносятся — только фильтр статуса.
    exportEl.href = exportEl.href.replace(/status=[a-z]+/, "status=" + status);
    render();
  });

  var debounce;
  searchEl.addEventListener("input", function (event) {
    var value = event.target.value;
    clearTimeout(debounce);
    debounce = setTimeout(function () {
      query = value.trim();
      render();
    }, 120);
  });

  pageEl.querySelectorAll("th.sortable").forEach(function (th) {
    th.addEventListener("click", function () {
      var key = th.dataset.sort;
      if (sortKey === key) {
        sortDir *= -1;
      } else {
        sortKey = key;
        sortDir = key === "paid_sort" ? -1 : 1;
      }
      pageEl.querySelectorAll("th.sortable").forEach(function (other) {
        other.classList.toggle("is-sorted", other === th);
        other.dataset.dir = other === th ? (sortDir === 1 ? "up" : "down") : "";
      });
      render();
    });
  });

  render();
})();
