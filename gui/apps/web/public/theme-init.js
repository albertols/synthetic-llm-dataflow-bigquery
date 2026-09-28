/*
 * Stamps the stored theme on <html> before the first paint, so a reader who
 * chose light (or "system" on a light OS) never sees the dark default flash
 * while the app's module script downloads. A classic blocking script on
 * purpose: module scripts run after parsing, too late. It lives in public/
 * (served as a same-origin file) rather than inline, so a CSP needs no hash.
 * src/lib/theme.ts owns the same storage key and colours;
 * src/lib/theme.test.ts runs this file against it.
 */
(function () {
  var theme = "dark";
  try {
    var stored = window.localStorage.getItem("synthetic-platform.theme");
    if (stored === "light") theme = "light";
    else if (stored === "system" && window.matchMedia && window.matchMedia("(prefers-color-scheme: light)").matches)
      theme = "light";
  } catch (e) {
    // Blocked storage: the dark default.
  }
  var root = document.documentElement;
  root.setAttribute("data-theme", theme);
  root.style.colorScheme = theme;
  var meta = document.querySelector('meta[name="theme-color"]');
  if (meta) meta.setAttribute("content", theme === "light" ? "#f4f5f7" : "#0b0d12");
})();
