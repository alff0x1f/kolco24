/*
 * Shared Markdown editor initialization for
 * src/templates/website/edit_page.html and src/templates/race/post_form.html.
 */
document.addEventListener("DOMContentLoaded", function () {
  if (typeof EasyMDE === "undefined") return;

  document.querySelectorAll("[data-markdown-editor]").forEach(function (el) {
    var mde = new EasyMDE({
      element: el,
      minHeight: "300px",
      spellChecker: false,
      autosave: {
        enabled: false,
      },
      autoDownloadFontAwesome: false,
      toolbar: [
        "bold",
        "italic",
        "heading",
        "|",
        "quote",
        "unordered-list",
        "ordered-list",
        {
          name: "table1",
          action: EasyMDE.drawTable,
          className: "fa fa-th mde-icon-table",
          title: "Вставить таблицу",
        },
        "code",
        "|",
        "link",
        "image",
        "|",
        "preview",
        "side-by-side",
        "fullscreen",
        "guide",
      ],
      renderingConfig: { singleLineBreaks: false },
    });

    if (el.form) {
      el.form.addEventListener("submit", function () {
        el.value = mde.value();
      });
    }
  });
});
