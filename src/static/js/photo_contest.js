(function () {
  "use strict";

  function initForms() {
    var panels = document.querySelectorAll(".photo-contest .pc-add");

    panels.forEach(function (panel) {
      panel.addEventListener("toggle", function () {
        if (!panel.open) return;
        panels.forEach(function (other) {
          if (other !== panel) other.open = false;
        });
        var file = panel.querySelector('input[type="file"]');
        if (file) file.focus({ preventScroll: true });
      });
    });

    var failed = document.querySelector(".photo-contest .pc-add[open]");
    if (failed) failed.scrollIntoView({ block: "center" });
  }

  function initViewer() {
    var dialog = document.querySelector(".photo-contest .pc-viewer");
    if (!dialog || typeof dialog.showModal !== "function") return;

    var img = dialog.querySelector(".pc-viewer-img");
    var text = dialog.querySelector(".pc-viewer-text");
    var meta = dialog.querySelector(".pc-viewer-meta");
    var prev = dialog.querySelector(".pc-viewer-prev");
    var next = dialog.querySelector(".pc-viewer-next");
    var links = [];
    var index = 0;
    var opener = null;

    function show(i) {
      index = i;
      var link = links[index];
      img.src = link.href;
      img.alt = link.querySelector("img").alt;
      text.textContent = link.dataset.caption || "";
      var parts = [link.dataset.status];
      if (links.length > 1) parts.push(index + 1 + " из " + links.length);
      meta.textContent = parts.join(", ");
      prev.hidden = index === 0;
      next.hidden = index === links.length - 1;
    }

    document.querySelectorAll(".photo-contest .pc-thumb-link").forEach(function (link) {
      link.addEventListener("click", function (event) {
        if (event.metaKey || event.ctrlKey || event.shiftKey || event.button !== 0) return;
        event.preventDefault();
        links = Array.prototype.slice.call(
          link.closest(".pc-thumbs").querySelectorAll(".pc-thumb-link")
        );
        opener = link;
        show(links.indexOf(link));
        dialog.showModal();
      });
    });

    prev.addEventListener("click", function () {
      if (index > 0) show(index - 1);
    });
    next.addEventListener("click", function () {
      if (index < links.length - 1) show(index + 1);
    });
    dialog.querySelector(".pc-viewer-close").addEventListener("click", function () {
      dialog.close();
    });

    dialog.addEventListener("keydown", function (event) {
      if (event.key === "ArrowLeft" && index > 0) show(index - 1);
      if (event.key === "ArrowRight" && index < links.length - 1) show(index + 1);
    });

    // A click on the dark area around the photo closes the viewer.
    dialog.addEventListener("click", function (event) {
      if (event.target === dialog) dialog.close();
    });

    dialog.addEventListener("close", function () {
      img.removeAttribute("src");
      if (opener) opener.focus();
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    initForms();
    initViewer();
  });
})();
