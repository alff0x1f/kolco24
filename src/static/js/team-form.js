/* ──────────────────────────────────────────────────────────────────────────
   team-form.js — shared behaviour for the add/edit team pages (base-2 design).

   Ported from the demo mockup at src/templates/demo/team-register.html, with
   the hardcoded constants replaced by a JSON config island and доплата-aware
   live totals that mirror the server formula.

   Config island (rendered by the template):
     <script type="application/json" id="teamFormConfig">
       { "currentPrice": 3000, "paidPeople": 0, "mapCountPaid": 0,
         "mapPrice": 200, "freeMaps": 2, "isEdit": false }
     </script>

   DOM contract the template MUST provide (everything else is optional and
   null-guarded so the script never throws if a hook is absent):
     form#teamForm
     select#category            — name="category2_id", options carry data-counts
     #ucountSeg, #ucountCap      — segmented control container + caption
     input#ucountInput           — hidden, name="ucount" (kept in sync by JS)
     #members > .member-row[data-idx]  with .m-name / .m-year inputs
     #mapsRow, #mapStepper ([data-step]) , #mapVal
     input#mapCountInput         — hidden, name="map_count" (kept in sync)
     #consent                    — consent checkbox (add mode only)
     #submitBtn / #payBtn        — submit buttons (gated on consent in add mode)
   Sidebar (optional): #sumHeading, #sumCountLbl, #sumCost, #sumPeople,
     #sumMapsLine/#sumMapsN/#sumMaps, #sumPaidLine/#sumPaidN/#sumPaidAmt,
     #sumTotal, #regClosedWarn.
   Submit buttons may carry data-label-due / data-label-zero to swap their text
   when an amount is / isn't due (used by edit: "Сохранить и доплатить").
   ────────────────────────────────────────────────────────────────────────── */
(function () {
  "use strict";

  var form = document.getElementById("teamForm");
  if (!form) return;

  // ── Config island ────────────────────────────────────
  var cfg = {
    currentPrice: 0,
    paidPeople: 0,
    mapCountPaid: 0,
    mapPrice: 200,
    freeMaps: 2,
    isEdit: false,
    raceRemaining: null,
    currentCategoryId: null,
  };
  var cfgEl = document.getElementById("teamFormConfig");
  if (cfgEl) {
    try {
      var parsed = JSON.parse(cfgEl.textContent || "{}");
      for (var k in parsed) {
        if (Object.prototype.hasOwnProperty.call(parsed, k)) cfg[k] = parsed[k];
      }
    } catch (e) {
      /* malformed island — keep defaults */
    }
  }
  var COST = Number(cfg.currentPrice) || 0;
  var MAP = Number(cfg.mapPrice) || 0;
  var FREE_MAPS = Number(cfg.freeMaps) || 0;
  var PAID_PEOPLE = Number(cfg.paidPeople) || 0;
  var MAPS_PAID = Number(cfg.mapCountPaid) || 0;
  var IS_EDIT = !!cfg.isEdit;
  // null/"" => unlimited; mirrors Race.remaining_people()/Category.remaining_people()
  var RACE_REMAINING = cfg.raceRemaining == null ? null : Number(cfg.raceRemaining);
  var CURRENT_CAT_ID = cfg.currentCategoryId == null ? null : String(cfg.currentCategoryId);

  // ── DOM hooks ─────────────────────────────────────────
  var category = document.getElementById("category");
  var seg = document.getElementById("ucountSeg");
  var segCap = document.getElementById("ucountCap");
  var ucountInput = document.getElementById("ucountInput");
  var rows = Array.prototype.slice.call(document.querySelectorAll(".member-row"));
  var membersEl = document.getElementById("members");
  var mapsRow = document.getElementById("mapsRow");
  var mapStepper = document.getElementById("mapStepper");
  var mapVal = document.getElementById("mapVal");
  var mapCountInput = document.getElementById("mapCountInput");
  var consent = document.getElementById("consent");
  var submitBtn = document.getElementById("submitBtn");
  var payBtn = document.getElementById("payBtn");
  var regClosedWarn = document.getElementById("regClosedWarn");

  // sidebar
  var sumHeading = document.getElementById("sumHeading");
  var sumCountLbl = document.getElementById("sumCountLbl");
  var sumCost = document.getElementById("sumCost");
  var sumPeople = document.getElementById("sumPeople");
  var sumMapsLine = document.getElementById("sumMapsLine");
  var sumMapsN = document.getElementById("sumMapsN");
  var sumMaps = document.getElementById("sumMaps");
  var sumPaidLine = document.getElementById("sumPaidLine");
  var sumPaidN = document.getElementById("sumPaidN");
  var sumPaidAmt = document.getElementById("sumPaidAmt");
  var sumTotal = document.getElementById("sumTotal");

  function fmt(n) {
    return Math.round(n).toLocaleString("ru-RU").replace(/,/g, " ");
  }
  function fmtPeople(n) {
    return n % 1 === 0 ? String(n) : String(Math.round(n * 10) / 10);
  }

  // initial state (read back from the hidden inputs the template renders)
  var ucount = parseInt(ucountInput && ucountInput.value, 10);
  var maps = parseInt(mapCountInput && mapCountInput.value, 10);
  if (isNaN(maps) || maps < 0) maps = 0;

  function counts() {
    var opt = category && category.selectedOptions[0];
    return ((opt && opt.dataset.counts) || "2")
      .split(",")
      .map(function (s) {
        return parseInt(s, 10);
      })
      .filter(function (n) {
        return !isNaN(n);
      });
  }

  // Free slots for an option (null = unlimited), as published by the server.
  function optRemaining(opt) {
    if (!opt) return null;
    var raw = opt.dataset.remaining;
    return raw === "" || raw == null ? null : Number(raw);
  }

  // Whether team size `n` is allowed for the selected option, mirroring the
  // server gate in TeamForm.clean(): race blocks only growth, category blocks
  // entering-full / growing-in-full (self-exclusion already baked into
  // data-remaining). The team's own current category is never blocked.
  function countAllowed(opt, n) {
    if (!opt) return true;
    var isCurrent =
      opt.dataset.current === "1" ||
      (CURRENT_CAT_ID != null && String(opt.value) === CURRENT_CAT_ID);
    // race: needed > 0 must fit into RACE_REMAINING
    if (RACE_REMAINING != null) {
      var needed = n - PAID_PEOPLE;
      if (needed > 0 && needed > RACE_REMAINING) return false;
    }
    // category: only when entering a new category or growing
    var catRem = optRemaining(opt);
    if (catRem != null) {
      var movingIn = !isCurrent;
      var growing = n > PAID_PEOPLE;
      if ((movingIn || growing) && n > catRem) return false;
    }
    return true;
  }

  // An option is "full" (and disabled in the dropdown) when even its smallest
  // allowed team size can't fit. The team's own category is never disabled.
  function syncCategoryOptions() {
    if (!category) return;
    Array.prototype.forEach.call(category.options, function (opt) {
      var sizes = (opt.dataset.counts || "")
        .split(",")
        .map(function (s) {
          return parseInt(s, 10);
        })
        .filter(function (x) {
          return !isNaN(x);
        });
      var minN = sizes.length ? sizes[0] : 2;
      opt.disabled = !countAllowed(opt, minN);
    });
  }

  function buildSeg() {
    var opt = category && category.selectedOptions[0];
    var opts = counts();
    seg.innerHTML = "";
    opts.forEach(function (n) {
      var b = document.createElement("button");
      b.type = "button";
      b.textContent = n;
      b.dataset.n = n;
      b.disabled = !countAllowed(opt, n);
      b.addEventListener("click", function () {
        if (b.disabled) return;
        setCount(n);
      });
      seg.appendChild(b);
    });
    if (segCap) {
      segCap.textContent =
        opts.length === 1
          ? "состав фиксирован: " + opts[0] + " чел."
          : "от " + opts[0] + " до " + opts[opts.length - 1] + " человек";
    }
    // prefer the current size if still allowed, else the largest allowed size,
    // else fall back to the smallest size (server still has final say)
    var allowed = opts.filter(function (n) {
      return countAllowed(opt, n);
    });
    var pick;
    if (opts.indexOf(ucount) >= 0 && countAllowed(opt, ucount)) {
      pick = ucount;
    } else if (allowed.length) {
      pick = allowed[allowed.length - 1];
    } else {
      pick = opts[0];
    }
    setCount(pick);
  }

  function setCount(n) {
    ucount = n;
    if (ucountInput) ucountInput.value = n;
    if (seg) {
      Array.prototype.forEach.call(seg.children, function (b) {
        b.classList.toggle("on", parseInt(b.dataset.n, 10) === n);
      });
    }
    rows.forEach(function (row) {
      var idx = parseInt(row.dataset.idx, 10);
      var show = idx <= n;
      var wasHidden = row.classList.contains("is-hidden");
      row.classList.toggle("is-hidden", !show);
      if (show && wasHidden) {
        row.classList.add("reveal");
        setTimeout(function () {
          row.classList.remove("reveal");
        }, 240);
      }
    });
    var maxMaps = Math.max(0, n - FREE_MAPS);
    if (maps > maxMaps) maps = maxMaps;
    if (mapsRow) mapsRow.style.display = maxMaps > 0 ? "" : "none";
    render();
  }

  function setMaps(v) {
    var maxMaps = Math.max(0, ucount - FREE_MAPS);
    maps = Math.min(Math.max(0, v), maxMaps);
    render();
  }

  function render() {
    rows.forEach(function (row) {
      var name = row.querySelector(".m-name");
      if (name) row.classList.toggle("filled", name.value.trim().length > 0);
    });

    var maxMaps = Math.max(0, ucount - FREE_MAPS);
    if (mapVal) mapVal.textContent = maps;
    if (mapCountInput) mapCountInput.value = maps;
    if (mapStepper) {
      var dec = mapStepper.querySelector('[data-step="-1"]');
      var inc = mapStepper.querySelector('[data-step="1"]');
      if (dec) dec.disabled = maps <= 0;
      if (inc) inc.disabled = maps >= maxMaps;
    }

    var peopleGross = ucount * COST;
    var mapsGross = maps * MAP;
    // already-paid credit, valued at the current price (mirrors the backend
    // formula: (ucount − paidPeople)·price + (maps − mapsPaid)·mapPrice)
    var credit = PAID_PEOPLE * COST + MAPS_PAID * MAP;
    var due = Math.max(0, peopleGross + mapsGross - credit);

    if (sumCountLbl) sumCountLbl.textContent = ucount;
    if (sumCost) sumCost.textContent = fmt(COST);
    if (sumPeople) sumPeople.textContent = fmt(peopleGross) + " ₽";

    if (sumMapsLine) {
      if (maps > 0) {
        sumMapsLine.style.display = "";
        if (sumMapsN) sumMapsN.textContent = maps;
        if (sumMaps) sumMaps.textContent = fmt(mapsGross) + " ₽";
      } else {
        sumMapsLine.style.display = "none";
      }
    }

    // edit mode: "уже оплачено за N чел." credit line
    if (sumPaidLine) {
      if (PAID_PEOPLE > 0 && credit > 0) {
        sumPaidLine.style.display = "";
        if (sumPaidN) sumPaidN.textContent = fmtPeople(PAID_PEOPLE);
        if (sumPaidAmt) sumPaidAmt.textContent = "−" + fmt(credit) + " ₽";
      } else {
        sumPaidLine.style.display = "none";
      }
    }

    if (sumHeading) sumHeading.textContent = IS_EDIT && due > 0 ? "К доплате" : "К оплате";
    if (sumTotal) sumTotal.textContent = fmt(due);

    // "регистрация закрыта" warning only matters when there is an amount due;
    // the element is rendered by the template only when reg_status != open.
    if (regClosedWarn) regClosedWarn.style.display = due > 0 ? "" : "none";

    updateButtons(due);
  }

  function updateButtons(due) {
    // consent gates submit in add mode; in edit mode the gate is skipped.
    var enabled = IS_EDIT || (consent ? consent.checked : true);
    [submitBtn, payBtn].forEach(function (btn) {
      if (!btn) return;
      btn.disabled = !enabled;
      var labelDue = btn.getAttribute("data-label-due");
      var labelZero = btn.getAttribute("data-label-zero");
      if (labelDue && labelZero) {
        btn.textContent = due > 0 ? labelDue : labelZero;
      }
    });
  }

  // ── Wiring ────────────────────────────────────────────
  if (category) category.addEventListener("change", buildSeg);
  if (mapStepper) {
    mapStepper.addEventListener("click", function (e) {
      var step = e.target.dataset.step;
      if (step) setMaps(maps + parseInt(step, 10));
    });
  }
  if (membersEl) {
    membersEl.addEventListener("input", function (e) {
      if (e.target.classList.contains("m-year")) {
        e.target.value = e.target.value.replace(/\D/g, "").slice(0, 4);
      }
      render();
    });
  }
  if (consent) consent.addEventListener("change", render);

  // ── Init ──────────────────────────────────────────────
  if (seg && category) {
    syncCategoryOptions();
    buildSeg();
  } else {
    setCount(isNaN(ucount) ? counts()[0] || 2 : ucount);
  }
})();
