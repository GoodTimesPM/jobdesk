/* Puts the saved look on the page before anything is drawn.

   The real copy of the settings is on the server, and app.js applies it once
   /api/status answers. That is a round trip after the first paint, which on
   a dark theme is a white flash every time the window opens. So app.js also
   leaves the three appearance settings in localStorage, and this file, loaded
   in <head> ahead of the stylesheet, reads them back in time.

   A separate file rather than an inline script because the page's CSP does
   not allow inline scripts, and should not be loosened for this. */

"use strict";

(function () {
  var look = null;
  try { look = JSON.parse(localStorage.getItem("jobdesk.look") || "null"); } catch (e) { look = null; }
  if (!look) return;
  var root = document.documentElement;
  if (look.theme === "light" || look.theme === "dark") root.dataset.theme = look.theme;
  if (look.text_size) root.dataset.size = String(look.text_size);
  if (look.density) root.dataset.density = look.density;
})();
