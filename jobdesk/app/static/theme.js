/* Puts the saved look on the page before anything is drawn.

   The real copy of the settings is on the server, and app.js applies it once
   /api/status answers. That is a round trip after the first paint, which on
   a dark theme is a white flash every time the window opens. So app.js also
   leaves the appearance settings in localStorage, and this file, loaded
   in <head> ahead of the stylesheet, reads them back in time.

   A separate file rather than an inline script because the page's CSP does
   not allow inline scripts, and should not be loosened for this. */

"use strict";

(function () {
  // Rows or tiles, per device. app.js owns the switch; this only keeps a
  // phone from drawing a table and then snapping into cards.
  var layout = null;
  try { layout = localStorage.getItem("jobdesk.layout"); } catch (e) { layout = null; }
  if (layout !== "rows" && layout !== "tiles") {
    layout = window.matchMedia("(max-width: 700px)").matches ? "tiles" : "rows";
  }
  document.documentElement.dataset.layout = layout;

  // The same for how the Jobs list is drawn and whether the menu is folded.
  var phone = window.matchMedia("(max-width: 700px)").matches;
  var jobs = null, side = null;
  try {
    jobs = localStorage.getItem("jobdesk.jobs");
    side = localStorage.getItem("jobdesk.side");
  } catch (e) { jobs = side = null; }
  if (["list", "compact", "tiles", "timeline"].indexOf(jobs) < 0) {
    jobs = phone ? "timeline" : "list";
  }
  document.documentElement.dataset.jobs = jobs;
  document.documentElement.dataset.side = side === "folded" ? "folded" : "open";

  var look = null;
  try { look = JSON.parse(localStorage.getItem("jobdesk.look") || "null"); } catch (e) { look = null; }
  if (!look) return;
  var root = document.documentElement;
  if (look.theme === "light" || look.theme === "dark") root.dataset.theme = look.theme;
  if (look.scheme) root.dataset.scheme = look.scheme;
  if (look.font) root.dataset.font = look.font;
  if (look.text_size) root.style.setProperty("--zoom", String(look.text_size / 100));
  if (look.density) root.dataset.density = look.density;
  if (/^#[0-9a-f]{6}$/i.test(look.accent || "")) {
    // app.js works out the text color that sits on it; until then, the
    // scheme's own keeps the buttons readable.
    root.style.setProperty("--accent-light", look.accent);
    root.style.setProperty("--accent-dark", look.accent);
    root.style.setProperty("--accent-fixed", look.accent);
  }
  // Custom colors are worked out in app.js, which leaves the finished
  // properties here, so this only has to put them back.
  var paint = look.paint;
  if (paint && paint.vars && /^(fade-)?custom$/.test(paint.scheme)) {
    root.dataset.scheme = paint.scheme;
    Object.keys(paint.vars).forEach(function (prop) {
      if (/^--[a-z-]+$/.test(prop)) root.style.setProperty(prop, String(paint.vars[prop]));
    });
    root.style.colorScheme = paint.dark ? "dark" : "light";
  }
})();
