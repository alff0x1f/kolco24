/* Race admin add/edit form — category + price-tier repeaters, char counters,
   slug auto-gen, publish-status toggle, live image preview, drag-reorder, and
   serialize-on-submit into the hidden categories_json / price_tiers_json inputs.

   Reads seed rows from the embedded <script id="categories-data"> and
   <script id="price-tiers-data"> JSON islands (not a hardcoded seed). */
(function () {
  "use strict";

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
      return (data && typeof data === "object" && !Array.isArray(data)) ? data : {};
    } catch (e) {
      return {};
    }
  }

  /* Pre-populate hidden inputs from data islands so that if the submit
     handler fails to wire (mid-IIFE error), the POST still carries data. */
  (function () {
    var catsField = document.getElementById("categoriesJson");
    var tiersField = document.getElementById("priceTiersJson");
    var extrasField = document.getElementById("extrasJson");
    var catsIsland = document.getElementById("categories-data");
    var tiersIsland = document.getElementById("price-tiers-data");
    var extrasIsland = document.getElementById("extras-data");
    if (catsField && catsIsland) catsField.value = catsIsland.textContent.trim();
    if (tiersField && tiersIsland) tiersField.value = tiersIsland.textContent.trim();
    if (extrasField && extrasIsland) extrasField.value = extrasIsland.textContent.trim();
  })();

  /* ── Categories repeater ─────────────────────────────────── */
  var catsEl = document.getElementById("cats");
  var catCountEl = document.getElementById("catCount");

  function makeCatRow(c) {
    c = c || {};
    var row = document.createElement("div");
    row.className = "cat-row" + (c.is_active === false ? " is-inactive" : "");
    if (c.id != null) row.dataset.id = c.id;
    row.innerHTML =
      '<div class="handle" title="Перетащите, чтобы изменить порядок" draggable="true">⠿</div>' +
      '<input class="control mono c-code" type="text" maxlength="15" placeholder="код">' +
      '<input class="control c-short" type="text" maxlength="15" placeholder="кратко">' +
      '<input class="control c-name" type="text" maxlength="50" placeholder="название">' +
      '<input class="control c-desc" type="text" maxlength="150" placeholder="описание">' +
      '<input class="control c-min" type="number" min="1" step="1" placeholder="мин">' +
      '<input class="control c-max" type="number" min="1" step="1" placeholder="макс">' +
      '<input class="control c-limit" type="number" min="0" step="1" placeholder="лимит" title="Лимит участников категории (0 — без лимита)">' +
      '<div class="cat-toggle-cell">' +
      '<label class="switch"><input type="checkbox" class="c-active"><span class="track"></span></label>' +
      "</div>" +
      '<button class="cat-del" type="button" title="Удалить категорию">' +
      '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6"><path d="M3 4h10M6.5 4V2.8h3V4M5 4l.6 9h4.8L11 4"/></svg>' +
      "</button>";

    row.querySelector(".c-code").value = c.code || "";
    row.querySelector(".c-short").value = c.short_name || "";
    row.querySelector(".c-name").value = c.name || "";
    row.querySelector(".c-desc").value = c.description || "";
    row.querySelector(".c-min").value = c.min_people != null ? c.min_people : 2;
    row.querySelector(".c-max").value = c.max_people != null ? c.max_people : 6;
    row.querySelector(".c-limit").value = c.people_limit != null ? c.people_limit : 0;
    var activeInput = row.querySelector(".c-active");
    activeInput.checked = c.is_active !== false;

    activeInput.addEventListener("change", function (e) {
      row.classList.toggle("is-inactive", !e.target.checked);
    });
    row.querySelector(".cat-del").addEventListener("click", function () {
      row.style.transition = "opacity .12s";
      row.style.opacity = "0";
      setTimeout(function () {
        row.remove();
        refreshCatCount();
      }, 120);
    });
    wireDrag(row);
    return row;
  }

  function refreshCatCount() {
    if (!catsEl) return;
    var n = catsEl.querySelectorAll(".cat-row").length;
    if (catCountEl) catCountEl.textContent = n;
    var empty = catsEl.querySelector(".cats-empty");
    if (n === 0 && !empty) {
      empty = document.createElement("div");
      empty.className = "cats-empty";
      empty.textContent = "Пока нет категорий — добавьте первую.";
      catsEl.appendChild(empty);
    } else if (n > 0 && empty) {
      empty.remove();
    }
  }

  /* ── Drag-reorder of category rows (handle initiates drag) ─ */
  var dragSrc = null;

  function wireDrag(row) {
    var handle = row.querySelector(".handle");
    if (!handle) return;
    handle.addEventListener("dragstart", function (e) {
      dragSrc = row;
      row.classList.add("is-dragging");
      e.dataTransfer.effectAllowed = "move";
      try {
        e.dataTransfer.setData("text/plain", "");
      } catch (err) {
        /* IE guard — ignored */
      }
    });
    handle.addEventListener("dragend", function () {
      if (dragSrc) dragSrc.classList.remove("is-dragging");
      dragSrc = null;
      catsEl
        .querySelectorAll(".drag-over")
        .forEach(function (r) { r.classList.remove("drag-over"); });
    });
    row.addEventListener("dragover", function (e) {
      if (!dragSrc || dragSrc === row) return;
      e.preventDefault();
      e.dataTransfer.dropEffect = "move";
      row.classList.add("drag-over");
    });
    row.addEventListener("dragleave", function () {
      row.classList.remove("drag-over");
    });
    row.addEventListener("drop", function (e) {
      e.preventDefault();
      row.classList.remove("drag-over");
      if (!dragSrc || dragSrc === row) return;
      var rows = Array.prototype.slice.call(catsEl.querySelectorAll(".cat-row"));
      var srcIdx = rows.indexOf(dragSrc);
      var tgtIdx = rows.indexOf(row);
      if (srcIdx < tgtIdx) {
        row.parentNode.insertBefore(dragSrc, row.nextSibling);
      } else {
        row.parentNode.insertBefore(dragSrc, row);
      }
    });
  }

  function applyRowErrors(container, rowSelector, fieldMap, errorsById) {
    var rows = container ? container.querySelectorAll(rowSelector) : [];
    Object.keys(errorsById).forEach(function (idxStr) {
      var row = rows[parseInt(idxStr, 10)];
      if (!row) return;
      var fieldErrors = errorsById[idxStr];
      row.classList.add("has-error");
      Object.keys(fieldErrors).forEach(function (field) {
        var sel = fieldMap[field];
        if (!sel) return;
        var input = row.querySelector(sel);
        if (input) input.classList.add("has-error");
        var msg = fieldErrors[field];
        var span = document.createElement("span");
        span.className = "field-error row-error";
        span.textContent = msg;
        if (input && input.parentNode) input.parentNode.appendChild(span);
      });
    });
  }

  if (catsEl) {
    readJson("categories-data").forEach(function (c) {
      catsEl.appendChild(makeCatRow(c));
    });
    refreshCatCount();
    applyRowErrors(catsEl, ".cat-row", {
      code: ".c-code", short_name: ".c-short", name: ".c-name",
      description: ".c-desc", min_people: ".c-min", max_people: ".c-max",
      people_limit: ".c-limit"
    }, readJsonObj("category-errors"));
    var addCat = document.getElementById("addCat");
    if (addCat) {
      addCat.addEventListener("click", function () {
        var row = makeCatRow();
        catsEl.appendChild(row);
        refreshCatCount();
        row.querySelector(".c-code").focus();
      });
    }
  }

  /* ── Price-tiers repeater ────────────────────────────────── */
  var tiersEl = document.getElementById("tiers");
  var tierCountEl = document.getElementById("tierCount");

  function makeTierRow(t) {
    t = t || {};
    var row = document.createElement("div");
    row.className = "tier-row";
    if (t.id != null) row.dataset.id = t.id;
    row.innerHTML =
      '<div class="tier-ord">●</div>' +
      '<input class="control t-until" type="date">' +
      '<div class="input-affix">' +
      '<input class="control t-price" type="number" min="1" step="50" placeholder="цена">' +
      '<span class="suffix">₽</span>' +
      "</div>" +
      '<button class="tier-del" type="button" title="Удалить период">' +
      '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6"><path d="M3 4h10M6.5 4V2.8h3V4M5 4l.6 9h4.8L11 4"/></svg>' +
      "</button>";
    row.querySelector(".t-until").value = t.active_until || "";
    row.querySelector(".t-price").value = t.price != null ? t.price : "";
    row.querySelector(".tier-del").addEventListener("click", function () {
      row.style.transition = "opacity .12s";
      row.style.opacity = "0";
      setTimeout(function () {
        row.remove();
        refreshTierCount();
      }, 120);
    });
    return row;
  }

  function refreshTierCount() {
    if (!tiersEl) return;
    var n = tiersEl.querySelectorAll(".tier-row").length;
    if (tierCountEl) tierCountEl.textContent = n;
    var empty = tiersEl.querySelector(".tiers-empty");
    if (n === 0 && !empty) {
      empty = document.createElement("div");
      empty.className = "tiers-empty";
      empty.textContent = "Без периодов — цена берётся из «Стоимости участия».";
      tiersEl.appendChild(empty);
    } else if (n > 0 && empty) {
      empty.remove();
    }
  }

  if (tiersEl) {
    readJson("price-tiers-data").forEach(function (t) {
      tiersEl.appendChild(makeTierRow(t));
    });
    refreshTierCount();
    applyRowErrors(tiersEl, ".tier-row", {
      price: ".t-price", active_until: ".t-until"
    }, readJsonObj("price-tier-errors"));
    var addTier = document.getElementById("addTier");
    if (addTier) {
      addTier.addEventListener("click", function () {
        var row = makeTierRow();
        tiersEl.appendChild(row);
        refreshTierCount();
        row.querySelector(".t-until").focus();
      });
    }
  }

  /* ── Add-ons (Доп-услуги) repeater ───────────────────────── */
  var extrasEl = document.getElementById("extras");
  var extraCountEl = document.getElementById("extraCount");

  function makeExtraRow(e) {
    e = e || {};
    var saved = e.id != null;
    var inUse = e.has_teams === true;
    var row = document.createElement("div");
    row.className = "extra-row" + (e.is_active === false ? " is-inactive" : "");
    if (saved) row.dataset.id = e.id;
    if (inUse) row.dataset.hasTeams = "1";
    row.innerHTML =
      '<div class="extra-ord">●</div>' +
      '<input class="control mono e-code" type="text" maxlength="32" placeholder="код">' +
      '<input class="control e-name" type="text" maxlength="100" placeholder="название">' +
      '<input class="control e-price" type="number" min="0" step="50" placeholder="цена">' +
      '<input class="control e-free" type="number" min="0" step="1" placeholder="0" title="Сколько входит в команду бесплатно">' +
      '<div class="extra-toggle-cell">' +
      '<label class="switch"><input type="checkbox" class="e-active"><span class="track"></span></label>' +
      "</div>" +
      '<button class="extra-del" type="button" title="Удалить услугу">' +
      '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6"><path d="M3 4h10M6.5 4V2.8h3V4M5 4l.6 9h4.8L11 4"/></svg>' +
      "</button>";

    var codeInput = row.querySelector(".e-code");
    codeInput.value = e.code || "";
    // Code is the natural key; once saved it must not change (unique per race).
    if (saved) codeInput.readOnly = true;
    row.querySelector(".e-name").value = e.name || "";
    row.querySelector(".e-price").value = e.price != null ? e.price : 0;
    row.querySelector(".e-free").value = e.free_per_team != null ? e.free_per_team : 0;
    var activeInput = row.querySelector(".e-active");
    activeInput.checked = e.is_active !== false;

    activeInput.addEventListener("change", function (ev) {
      row.classList.toggle("is-inactive", !ev.target.checked);
    });
    var delBtn = row.querySelector(".extra-del");
    if (inUse) {
      // An add-on teams already bought can't be deleted — «remove» deactivates.
      delBtn.title = "Услуга используется — деактивировать";
      delBtn.addEventListener("click", function () {
        activeInput.checked = false;
        row.classList.add("is-inactive");
      });
    } else {
      delBtn.addEventListener("click", function () {
        row.style.transition = "opacity .12s";
        row.style.opacity = "0";
        setTimeout(function () {
          row.remove();
          refreshExtraCount();
        }, 120);
      });
    }
    return row;
  }

  function refreshExtraCount() {
    if (!extrasEl) return;
    var n = extrasEl.querySelectorAll(".extra-row").length;
    if (extraCountEl) extraCountEl.textContent = n;
    var empty = extrasEl.querySelector(".extras-empty");
    if (n === 0 && !empty) {
      empty = document.createElement("div");
      empty.className = "extras-empty";
      empty.textContent = "Доп-услуг нет — добавьте первую при необходимости.";
      extrasEl.appendChild(empty);
    } else if (n > 0 && empty) {
      empty.remove();
    }
  }

  if (extrasEl) {
    readJson("extras-data").forEach(function (e) {
      extrasEl.appendChild(makeExtraRow(e));
    });
    refreshExtraCount();
    applyRowErrors(extrasEl, ".extra-row", {
      code: ".e-code", name: ".e-name", price: ".e-price",
      free_per_team: ".e-free"
    }, readJsonObj("extra-errors"));
    var addExtra = document.getElementById("addExtra");
    if (addExtra) {
      addExtra.addEventListener("click", function () {
        var row = makeExtraRow();
        extrasEl.appendChild(row);
        refreshExtraCount();
        row.querySelector(".e-code").focus();
      });
    }
  }

  /* ── Char counters ───────────────────────────────────────── */
  document.querySelectorAll("[data-count-for]").forEach(function (span) {
    var input = document.getElementById(span.getAttribute("data-count-for"));
    if (!input) return;
    var upd = function () {
      span.textContent = input.value.length;
    };
    input.addEventListener("input", upd);
    upd();
  });

  /* ── Slug auto-generate ──────────────────────────────────── */
  function translit(str) {
    var map = {
      а: "a", б: "b", в: "v", г: "g", д: "d", е: "e", ё: "e", ж: "zh",
      з: "z", и: "i", й: "y", к: "k", л: "l", м: "m", н: "n", о: "o",
      п: "p", р: "r", с: "s", т: "t", у: "u", ф: "f", х: "h", ц: "c",
      ч: "ch", ш: "sh", щ: "sch", ъ: "", ы: "y", ь: "", э: "e", ю: "yu",
      я: "ya", " ": "-"
    };
    return str
      .toLowerCase()
      .split("")
      .map(function (ch) {
        return map[ch] !== undefined ? map[ch] : ch;
      })
      .join("")
      .replace(/[^a-z0-9-]/g, "")
      .replace(/-+/g, "-")
      .replace(/^-|-$/g, "");
  }
  var slugAuto = document.getElementById("slugAuto");
  if (slugAuto) {
    slugAuto.addEventListener("click", function (e) {
      e.preventDefault();
      var src = document.querySelector("[data-slug-src]");
      var slug = document.getElementById("f-slug");
      if (src && slug) slug.value = translit(src.value) || "race";
    });
  }

  /* ── Publish status reflects «Активна» ───────────────────── */
  var activeToggle = document.getElementById("f-active");
  var pubStatus = document.getElementById("pubStatus");
  if (activeToggle && pubStatus) {
    activeToggle.addEventListener("change", function () {
      var on = activeToggle.checked;
      pubStatus.classList.toggle("off", !on);
      var txt = pubStatus.querySelector(".txt");
      if (txt) {
        txt.textContent = on
          ? "Гонка опубликована"
          : "Черновик — скрыта";
      }
    });
  }

  /* ── Live image preview ──────────────────────────────────── */
  document.querySelectorAll("[data-preview-for]").forEach(function (input) {
    var img = document.getElementById(input.getAttribute("data-preview-for"));
    if (!img) return;
    var ph = img.parentNode.querySelector(".ph, .lph");
    var upd = function () {
      var url = input.value.trim();
      if (url) {
        img.src = url;
        img.hidden = false;
        if (ph) ph.hidden = true;
      } else {
        img.removeAttribute("src");
        img.hidden = true;
        if (ph) ph.hidden = false;
      }
    };
    input.addEventListener("input", upd);
  });

  /* ── Serialize on submit ─────────────────────────────────── */
  var form = document.getElementById("raceForm");
  if (form) {
    form.addEventListener("submit", function () {
      var cats = [];
      if (catsEl) {
        catsEl.querySelectorAll(".cat-row").forEach(function (row) {
          var idAttr = row.dataset.id;
          cats.push({
            id: idAttr != null && idAttr !== "" ? parseInt(idAttr, 10) : null,
            code: row.querySelector(".c-code").value.trim(),
            short_name: row.querySelector(".c-short").value.trim(),
            name: row.querySelector(".c-name").value.trim(),
            description: row.querySelector(".c-desc").value.trim(),
            is_active: row.querySelector(".c-active").checked,
            min_people: parseInt(row.querySelector(".c-min").value, 10) || 0,
            max_people: parseInt(row.querySelector(".c-max").value, 10) || 0,
            people_limit: parseInt(row.querySelector(".c-limit").value, 10) || 0
          });
        });
      }
      var tiers = [];
      if (tiersEl) {
        tiersEl.querySelectorAll(".tier-row").forEach(function (row) {
          var idAttr = row.dataset.id;
          tiers.push({
            id: idAttr != null && idAttr !== "" ? parseInt(idAttr, 10) : null,
            price: parseInt(row.querySelector(".t-price").value, 10) || 0,
            active_until: row.querySelector(".t-until").value
          });
        });
      }
      var extras = [];
      if (extrasEl) {
        extrasEl.querySelectorAll(".extra-row").forEach(function (row) {
          var idAttr = row.dataset.id;
          extras.push({
            id: idAttr != null && idAttr !== "" ? parseInt(idAttr, 10) : null,
            code: row.querySelector(".e-code").value.trim(),
            name: row.querySelector(".e-name").value.trim(),
            price: parseInt(row.querySelector(".e-price").value, 10) || 0,
            free_per_team: parseInt(row.querySelector(".e-free").value, 10) || 0,
            is_active: row.querySelector(".e-active").checked
          });
        });
      }
      var catsField = document.getElementById("categoriesJson");
      var tiersField = document.getElementById("priceTiersJson");
      var extrasField = document.getElementById("extrasJson");
      if (catsField) catsField.value = JSON.stringify(cats);
      if (tiersField) tiersField.value = JSON.stringify(tiers);
      if (extrasField) extrasField.value = JSON.stringify(extras);
    });
  }
})();
