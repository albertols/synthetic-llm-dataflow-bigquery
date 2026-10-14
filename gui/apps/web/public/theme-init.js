/*
 * Stamps the stored brand and theme on <html> before the first paint, so a
 * reader who chose light (or "system" on a light OS), or another brand, never
 * sees the default flash while the app's module script downloads. A classic
 * blocking script on purpose: module scripts run after parsing, too late. It
 * lives in public/ (served as a same-origin file) rather than inline, so a CSP
 * needs no hash.
 *
 * The build's brands, its default brand and each brand's browser colour come
 * from the #brand-packs JSON block the build writes just above this script
 * (vite-plugins/brandPacks.ts); without it, the committed brand. An unknown
 * stored brand (its pack removed) falls back to the default.
 * src/lib/theme.ts and src/lib/brand.ts own the same storage keys;
 * src/lib/theme.test.ts runs this file against them.
 */
(function () {
  var brand = "beam-dataflow";
  var colours = { "beam-dataflow": { dark: "#0b0d12", light: "#f4f5f7" } };
  try {
    var head = JSON.parse(document.getElementById("brand-packs").textContent);
    var known = {};
    for (var i = 0; i < head.packs.length; i++) known[head.packs[i].id] = head.packs[i].themeColor;
    if (Object.prototype.hasOwnProperty.call(known, head.default)) {
      colours = known;
      brand = head.default;
    }
  } catch (e) {
    // No list, or an unreadable one: the committed brand.
  }
  var theme = "dark";
  try {
    var stored = window.localStorage.getItem("synthetic-platform.theme");
    if (stored === "light") theme = "light";
    else if (stored === "system" && window.matchMedia && window.matchMedia("(prefers-color-scheme: light)").matches)
      theme = "light";
    var storedBrand = window.localStorage.getItem("synthetic-platform.brand");
    if (storedBrand !== null && Object.prototype.hasOwnProperty.call(colours, storedBrand)) brand = storedBrand;
  } catch (e) {
    // Blocked storage: the defaults.
  }
  var root = document.documentElement;
  root.setAttribute("data-brand", brand);
  root.setAttribute("data-theme", theme);
  root.style.colorScheme = theme;
  var meta = document.querySelector('meta[name="theme-color"]');
  if (meta) meta.setAttribute("content", colours[brand][theme]);
})();
