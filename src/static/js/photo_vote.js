(function () {
  "use strict";

  var SIDES = ["left", "right"];
  var SWIPE_MIN_PX = 50;

  function init() {
    var root = document.querySelector(".photo-vote");
    var configNode = document.getElementById("photoVoteConfig");
    if (!root || !configNode) return;
    var config = JSON.parse(configNode.textContent);

    var progress = root.querySelector(".pv-progress");
    var loading = root.querySelector(".pv-loading");
    var pairBox = root.querySelector(".pv-pair");
    var errorBox = root.querySelector(".pv-error");
    var doneBox = root.querySelector(".pv-done");
    var cards = {};
    SIDES.forEach(function (side) {
      var card = root.querySelector('.pv-card[data-side="' + side + '"]');
      cards[side] = {
        img: card.querySelector(".pv-photo img"),
        photo: card.querySelector(".pv-photo"),
        choose: card.querySelector(".pv-choose"),
      };
    });

    var pair = null;
    var busy = true;
    var request = 0;

    function setBusy(value) {
      busy = value;
      SIDES.forEach(function (side) {
        cards[side].choose.disabled = value;
      });
      viewerChoose.disabled = value;
    }

    function showOnly(box) {
      [loading, pairBox, errorBox, doneBox].forEach(function (el) {
        el.hidden = el !== box;
      });
    }

    function preload(url) {
      return new Promise(function (resolve, reject) {
        var img = new Image();
        img.onload = resolve;
        img.onerror = reject;
        img.src = url;
      });
    }

    function render(data) {
      progress.hidden = false;
      progress.textContent = Math.min(data.answered, data.limit) + " из " + data.limit;
      if (data.done) {
        pair = null;
        doneBox.querySelectorAll("[data-reason]").forEach(function (el) {
          el.hidden = el.dataset.reason !== data.reason;
        });
        showOnly(doneBox);
        return Promise.resolve();
      }
      var mine = ++request;
      // Both photos must be on screen before a choice is possible.
      return Promise.all([preload(data.left.url), preload(data.right.url)]).then(
        function () {
          if (mine !== request) return;
          pair = data;
          SIDES.forEach(function (side) {
            cards[side].img.src = data[side].url;
          });
          showOnly(pairBox);
          setBusy(false);
        }
      );
    }

    function fail() {
      pair = null;
      showOnly(errorBox);
    }

    function handle(response) {
      if (!response.ok) throw new Error(response.status);
      return response.json().then(render);
    }

    function load() {
      setBusy(true);
      showOnly(loading);
      return fetch(config.pairUrl, {
        credentials: "same-origin",
        headers: { Accept: "application/json" },
      })
        .then(handle)
        .catch(fail);
    }

    function choose(side) {
      if (busy || !pair) return;
      setBusy(true);
      fetch(config.pairUrl, {
        method: "POST",
        credentials: "same-origin",
        headers: {
          Accept: "application/json",
          "Content-Type": "application/json",
          "X-CSRFToken": config.csrfToken,
        },
        body: JSON.stringify({ token: pair.token, winner_id: pair[side].id }),
      })
        .then(function (response) {
          // The pair went stale (expired, photo withdrawn): ask for the current one.
          if (response.status === 400) return load();
          return handle(response);
        })
        .catch(fail);
    }

    // --- large view ------------------------------------------------------

    var dialog = root.querySelector(".pv-viewer");
    var viewerImg = dialog.querySelector(".pc-viewer-img");
    var viewerMeta = dialog.querySelector(".pc-viewer-meta");
    var viewerChoose = dialog.querySelector(".pv-viewer-choose");
    var prev = dialog.querySelector(".pc-viewer-prev");
    var next = dialog.querySelector(".pc-viewer-next");
    var close = dialog.querySelector(".pc-viewer-close");
    var shown = "left";
    var opener = null;
    var touchX = null;

    function show(side) {
      shown = side;
      viewerImg.src = pair[side].url;
      viewerImg.alt = side === "left" ? "Фото слева" : "Фото справа";
      viewerMeta.textContent = (side === "left" ? "Левое" : "Правое") + " фото";
      var focused = document.activeElement;
      prev.hidden = side === "left";
      next.hidden = side === "right";
      if (focused === prev && prev.hidden) next.focus();
      if (focused === next && next.hidden) prev.focus();
    }

    function openViewer(side) {
      if (!pair || typeof dialog.showModal !== "function") return;
      opener = cards[side].photo;
      show(side);
      dialog.showModal();
    }

    SIDES.forEach(function (side) {
      cards[side].photo.addEventListener("click", function () {
        openViewer(side);
      });
      cards[side].choose.addEventListener("click", function () {
        choose(side);
      });
    });

    prev.addEventListener("click", function () {
      show("left");
    });
    next.addEventListener("click", function () {
      show("right");
    });
    close.addEventListener("click", function () {
      dialog.close();
    });
    viewerChoose.addEventListener("click", function () {
      var side = shown;
      opener = null;
      dialog.close();
      choose(side);
    });

    dialog.addEventListener("keydown", function (event) {
      if (event.key === "ArrowLeft") show("left");
      if (event.key === "ArrowRight") show("right");
    });
    dialog.addEventListener("click", function (event) {
      if (event.target === dialog) dialog.close();
    });
    dialog.addEventListener(
      "touchstart",
      function (event) {
        touchX = event.touches.length === 1 ? event.touches[0].clientX : null;
      },
      { passive: true }
    );
    dialog.addEventListener("touchend", function (event) {
      if (touchX === null) return;
      var dx = event.changedTouches[0].clientX - touchX;
      touchX = null;
      if (dx <= -SWIPE_MIN_PX) show("right");
      if (dx >= SWIPE_MIN_PX) show("left");
    });
    dialog.addEventListener("close", function () {
      viewerImg.removeAttribute("src");
      if (opener) opener.focus();
    });

    document.addEventListener("keydown", function (event) {
      if (dialog.open || event.altKey || event.ctrlKey || event.metaKey) return;
      var target = event.target;
      if (target.closest && target.closest("input, textarea, select, [contenteditable]"))
        return;
      if (event.key === "ArrowLeft") choose("left");
      if (event.key === "ArrowRight") choose("right");
    });

    root.querySelector(".pv-retry").addEventListener("click", load);

    load();
  }

  document.addEventListener("DOMContentLoaded", init);
})();
