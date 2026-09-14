/* ──────────────────────────────────────────────────────────────────────────
   team-form.js — shared behaviour for the add/edit team pages (base-2 design).

   Ported from the demo mockup at src/templates/demo/team-register.html, with
   the hardcoded constants replaced by a JSON config island and доплата-aware
   live totals that mirror the server formula.

   This file is the CLIENT mirror of the charge formula in
   src/apps/race/pricing.py (compute_team_charge). Any change to the add-on or
   promo math here must be reflected there, and vice versa:

       fee      = max(0, floor((ucount − paidPeople) × currentPrice))
       discount = promo ? (percent ? floor(fee × value / 100)
                                   : min(value, fee)) : 0
       due      = max(0, fee − discount
                  + Σ active extras: max(0, count − countPaid) × price)

   A promo discounts the participation fee only — add-ons stay at full price.

   Config island (rendered by the template):
     <script type="application/json" id="teamFormConfig">
       { "currentPrice": 3000, "paidPeople": 0, "isEdit": false,
         "extras": [{ "code": "map", "name": "Доп. карты", "price": 200,
                      "freePerTeam": 2, "count": 0, "countPaid": 0 }],
         "promo": { "code": "SALE40", "type": "percent", "value": 40 },
         "promoCheckUrl": "/race/<slug>/promo/check/", "teamId": 42 }
     </script>

   DOM contract the template MUST provide (everything else is optional and
   null-guarded so the script never throws if a hook is absent):
     form#teamForm
     select#category            — name="category2_id", options carry data-counts
     #ucountSeg, #ucountCap      — segmented control container + caption
     input#ucountInput           — hidden, name="ucount" (kept in sync by JS)
     #members > .member-row[data-idx]  with .m-name / .m-year inputs
     #extrasRows                 — container; JS builds one stepper per extra,
                                   each with a hidden input name="extra_<code>"
     #consent                    — consent checkbox (add mode only)
     #submitBtn / #payBtn        — submit buttons (gated on consent in add mode)
     #promoInput / #promoBtn / #promoMsg / #promoCode (hidden, name="promo_code")
   Sidebar (optional): #sumHeading, #sumPeopleLine/#sumCountLbl/#sumCost/
     #sumPeople, #sumExtras (container for per-extra lines), #sumPromoLine/
     #sumPromoCode/#sumPromoAmt, #sumPaidLine/#sumPaidN, #sumTotal,
     #regClosedWarn.
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
    extras: [],
    isEdit: false,
    raceRemaining: null,
    currentCategoryId: null,
    bypassLimits: false,
    promo: null,
    promoCheckUrl: "",
    teamId: null,
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
  var PAID_PEOPLE = Number(cfg.paidPeople) || 0;
  var IS_EDIT = !!cfg.isEdit;
  // null/"" => unlimited; mirrors Race.remaining_people()/Category.remaining_people()
  var RACE_REMAINING = cfg.raceRemaining == null ? null : Number(cfg.raceRemaining);
  var CURRENT_CAT_ID = cfg.currentCategoryId == null ? null : String(cfg.currentCategoryId);
  var BYPASS_LIMITS = !!cfg.bypassLimits;
  var PROMO_CHECK_URL = cfg.promoCheckUrl || "";
  var TEAM_ID = cfg.teamId == null ? null : String(cfg.teamId);
  // Applied promo: { code, type: "percent"|"fixed", value } or null.
  var promo = cfg.promo || null;

  // ── DOM hooks ─────────────────────────────────────────
  var category = document.getElementById("category");
  var seg = document.getElementById("ucountSeg");
  var segCap = document.getElementById("ucountCap");
  var ucountInput = document.getElementById("ucountInput");
  var rows = Array.prototype.slice.call(document.querySelectorAll(".member-row"));
  var membersEl = document.getElementById("members");
  var extrasRows = document.getElementById("extrasRows");
  var consent = document.getElementById("consent");
  var submitBtn = document.getElementById("submitBtn");
  var payBtn = document.getElementById("payBtn");
  var regClosedWarn = document.getElementById("regClosedWarn");
  var promoInput = document.getElementById("promoInput");
  var promoBtn = document.getElementById("promoBtn");
  var promoMsg = document.getElementById("promoMsg");
  var promoCode = document.getElementById("promoCode");

  // sidebar
  var sumHeading = document.getElementById("sumHeading");
  var sumPeopleLine = document.getElementById("sumPeopleLine");
  var sumCountLbl = document.getElementById("sumCountLbl");
  var sumCost = document.getElementById("sumCost");
  var sumPeople = document.getElementById("sumPeople");
  var sumExtras = document.getElementById("sumExtras");
  var sumPromoLine = document.getElementById("sumPromoLine");
  var sumPromoCode = document.getElementById("sumPromoCode");
  var sumPromoAmt = document.getElementById("sumPromoAmt");
  var sumPaidLine = document.getElementById("sumPaidLine");
  var sumPaidN = document.getElementById("sumPaidN");
  var sumTotal = document.getElementById("sumTotal");

  function fmt(n) {
    return Math.round(n).toLocaleString("ru-RU").replace(/,/g, " ");
  }
  function fmtPeople(n) {
    return n % 1 === 0 ? String(n) : String(Math.round(n * 10) / 10);
  }

  // initial state (read back from the hidden inputs the template renders)
  var ucount = parseInt(ucountInput && ucountInput.value, 10);

  // ── Add-ons (one stepper per active extra) ────────────
  // Each entry mirrors one RaceExtra; the live total sums their deltas
  // exactly like compute_team_charge in src/apps/race/pricing.py.
  var extras = [];
  (cfg.extras || []).forEach(function (e) {
    var code = String(e.code);
    var price = Number(e.price) || 0;
    var free = Number(e.freePerTeam) || 0;
    var countPaid = Number(e.countPaid) || 0;
    var count = Number(e.count);
    if (isNaN(count) || count < countPaid) count = countPaid;

    var hidden = document.createElement("input");
    hidden.type = "hidden";
    hidden.name = "extra_" + code;
    hidden.value = count;

    var item = {
      code: code,
      name: String(e.name || code),
      price: price,
      free: free,
      count: count,
      countPaid: countPaid,
      input: hidden,
      valEl: null,
      decBtn: null,
      incBtn: null,
      row: null,
      sumLine: null,
      sumN: null,
      sumAmt: null,
    };

    if (extrasRows) {
      var row = document.createElement("div");
      row.className = "maps-row";
      row.dataset.code = code;
      row.style.display = "none";

      var txt = document.createElement("div");
      txt.className = "maps-txt";
      var h4 = document.createElement("h4");
      h4.textContent = item.name;
      var p = document.createElement("p");
      var freeNote = free > 0 ? "На команду — " + free + " бесплатно. " : "";
      p.innerHTML = freeNote + "+" + fmt(price) + " ₽ за штуку.";
      txt.appendChild(h4);
      txt.appendChild(p);

      var stepper = document.createElement("div");
      stepper.className = "stepper";
      var dec = document.createElement("button");
      dec.type = "button";
      dec.setAttribute("data-step", "-1");
      dec.setAttribute("aria-label", "Меньше");
      dec.textContent = "−";
      var val = document.createElement("span");
      val.className = "val";
      val.textContent = count;
      var inc = document.createElement("button");
      inc.type = "button";
      inc.setAttribute("data-step", "1");
      inc.setAttribute("aria-label", "Больше");
      inc.textContent = "+";
      stepper.appendChild(dec);
      stepper.appendChild(val);
      stepper.appendChild(inc);

      row.appendChild(txt);
      row.appendChild(stepper);
      extrasRows.appendChild(row);

      item.row = row;
      item.valEl = val;
      item.decBtn = dec;
      item.incBtn = inc;

      stepper.addEventListener("click", function (ev) {
        var step = ev.target.dataset.step;
        if (step) setExtra(item, item.count + parseInt(step, 10));
      });
    }
    // hidden input always lives in the form (even without a visible stepper)
    (extrasRows || form).appendChild(hidden);

    if (sumExtras) {
      var line = document.createElement("div");
      line.className = "sum-line muted";
      line.dataset.code = code;
      line.style.display = "none";
      var lbl = document.createElement("span");
      lbl.className = "lbl";
      var sumN = document.createElement("b");
      sumN.textContent = "0";
      lbl.appendChild(document.createTextNode(item.name + " · "));
      lbl.appendChild(sumN);
      lbl.appendChild(document.createTextNode(" × " + fmt(price) + " ₽"));
      var amt = document.createElement("span");
      amt.className = "amt";
      amt.textContent = "0 ₽";
      line.appendChild(lbl);
      line.appendChild(amt);
      sumExtras.appendChild(line);
      item.sumLine = line;
      item.sumN = sumN;
      item.sumAmt = amt;
    }

    extras.push(item);
  });

  function extraMax(item) {
    return Math.max(0, ucount - item.free);
  }

  function setExtra(item, v) {
    var max = extraMax(item);
    item.count = Math.min(Math.max(item.countPaid, v), max);
    render();
  }

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
    if (!opt || BYPASS_LIMITS) return true;
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
      if (BYPASS_LIMITS) {
        opt.disabled = false;
        return;
      }
      var isCurrent =
        opt.dataset.current === "1" ||
        (CURRENT_CAT_ID != null && String(opt.value) === CURRENT_CAT_ID);
      if (isCurrent) {
        opt.disabled = false;
        return;
      }
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
    render();
  }

  function render() {
    rows.forEach(function (row) {
      var name = row.querySelector(".m-name");
      if (name) row.classList.toggle("filled", name.value.trim().length > 0);
    });

    // paidPeople may be fractional; floor before the discount like the server
    var fee = Math.max(0, Math.floor((ucount - PAID_PEOPLE) * COST));
    var discount = promoDiscount(fee);
    var due = fee - discount;

    if (sumPromoLine) {
      if (promo && discount > 0) {
        sumPromoLine.style.display = "";
        if (sumPromoCode) sumPromoCode.textContent = promo.code;
        if (sumPromoAmt) sumPromoAmt.textContent = "−" + fmt(discount) + " ₽";
      } else {
        sumPromoLine.style.display = "none";
      }
    }

    extras.forEach(function (item) {
      var max = extraMax(item);
      if (item.count > max) item.count = max;
      if (item.count < item.countPaid) item.count = item.countPaid;
      if (item.input) item.input.value = item.count;
      if (item.valEl) item.valEl.textContent = item.count;
      if (item.row) item.row.style.display = max > 0 ? "" : "none";
      if (item.decBtn) item.decBtn.disabled = item.count <= item.countPaid;
      if (item.incBtn) item.incBtn.disabled = item.count >= max;

      var delta = Math.max(0, item.count - item.countPaid);
      var lineAmt = delta * item.price;
      due += lineAmt;
      if (item.sumLine) {
        if (delta > 0) {
          item.sumLine.style.display = "";
          if (item.sumN) item.sumN.textContent = delta;
          if (item.sumAmt) item.sumAmt.textContent = fmt(lineAmt) + " ₽";
        } else {
          item.sumLine.style.display = "none";
        }
      }
    });
    due = Math.max(0, due);

    // Only the unpaid seats are priced. Paid seats get a note without an
    // amount: valuing them at today's price would misstate what was paid (an
    // earlier tier or a promo).
    var newSeats = Math.max(0, ucount - PAID_PEOPLE);
    if (sumPeopleLine) {
      sumPeopleLine.style.display = PAID_PEOPLE > 0 && newSeats === 0 ? "none" : "";
    }
    if (sumCountLbl) sumCountLbl.textContent = newSeats;
    if (sumCost) sumCost.textContent = fmt(COST);
    if (sumPeople) sumPeople.textContent = fmt(newSeats * COST) + " ₽";

    if (sumPaidLine) {
      if (PAID_PEOPLE > 0) {
        sumPaidLine.style.display = "";
        if (sumPaidN) sumPaidN.textContent = fmtPeople(PAID_PEOPLE);
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

  // ── Promo code ────────────────────────────────────────
  // Server mirror: RacePromo.discount_for (src/apps/race/models.py) — a percent
  // is floored, and the discount never exceeds the fee.
  function promoDiscount(fee) {
    if (!promo || fee <= 0) return 0;
    var value = Number(promo.value) || 0;
    var raw =
      promo.type === "percent"
        ? Math.floor((fee * value) / 100)
        : Math.min(value, fee);
    return Math.max(0, Math.min(raw, fee));
  }

  function showPromoMsg(text, isError) {
    if (!promoMsg) return;
    promoMsg.textContent = text || "";
    promoMsg.style.display = text ? "" : "none";
    promoMsg.classList.toggle("err", !!isError);
    promoMsg.classList.toggle("ok", !!text && !isError);
  }

  function setPromo(next) {
    promo = next;
    if (promoCode) promoCode.value = next ? next.code : "";
    if (promoInput) {
      promoInput.value = next ? next.code : "";
      promoInput.readOnly = !!next;
      promoInput.classList.toggle("has-error", false);
    }
    if (promoBtn) promoBtn.textContent = next ? "Убрать" : "Применить";
    render();
  }

  function applyPromo() {
    var code = (promoInput ? promoInput.value : "").trim();
    if (!code || !PROMO_CHECK_URL) {
      showPromoMsg("Введите промокод", true);
      return;
    }
    var url = PROMO_CHECK_URL + "?code=" + encodeURIComponent(code);
    if (TEAM_ID) url += "&team_id=" + encodeURIComponent(TEAM_ID);
    if (promoBtn) promoBtn.disabled = true;
    fetch(url, { credentials: "same-origin" })
      .then(function (resp) {
        return resp.json().catch(function () {
          return { ok: false, error: "Не удалось проверить промокод" };
        });
      })
      .then(function (data) {
        if (data && data.ok) {
          setPromo({ code: data.code, type: data.type, value: data.value });
          showPromoMsg("Промокод применён", false);
        } else {
          setPromo(null);
          if (promoInput) {
            promoInput.value = code;
            promoInput.classList.add("has-error");
          }
          showPromoMsg((data && data.error) || "Промокод не найден", true);
        }
      })
      .catch(function () {
        showPromoMsg("Не удалось проверить промокод", true);
      })
      .then(function () {
        if (promoBtn) promoBtn.disabled = false;
      });
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
  if (membersEl) {
    membersEl.addEventListener("input", function (e) {
      if (e.target.classList.contains("m-year")) {
        e.target.value = e.target.value.replace(/\D/g, "").slice(0, 4);
      }
      render();
    });
  }
  if (consent) consent.addEventListener("change", render);
  if (promoBtn) {
    promoBtn.addEventListener("click", function () {
      if (promo) {
        setPromo(null);
        showPromoMsg("", false);
      } else {
        applyPromo();
      }
    });
  }
  if (promoInput) {
    promoInput.addEventListener("keydown", function (e) {
      if (e.key === "Enter") {
        e.preventDefault();
        if (!promo) applyPromo();
      }
    });
  }
  // ── Init ──────────────────────────────────────────────
  if (seg && category) {
    syncCategoryOptions();
    buildSeg();
  } else {
    setCount(isNaN(ucount) ? counts()[0] || 2 : ucount);
  }
  // A promo resolved server-side survives a re-render with errors; applied
  // after the size control is built so the total is recomputed with it.
  // A rejected code stays visible next to its error, but must not be re-sent:
  // otherwise removing it from the input never clears the submitted value.
  if (promo) setPromo(promo);
  else if (promoCode) promoCode.value = "";
})();
