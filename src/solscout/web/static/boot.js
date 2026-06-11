/* Design assignment (ADR-047): the "case file" (B) won — it IS the brand now. No more coin flip.
   ?v=a|b still overrides and sticks (localStorage), keeping the old neon lab reachable for those
   who ask. Key is rc_v2 so stale rc_v coin-flip assignments from the A/B era are forgotten.
   Loaded as a BLOCKING head script (no defer) so the right skin paints first — and as an external
   file (ADR-048) so the CSP can forbid inline scripts entirely. */
(function () {
  var q = new URLSearchParams(location.search).get("v");
  var v = (q === "a" || q === "b") ? q : null;
  if (v) { try { localStorage.setItem("rc_v2", v); } catch (e) { /* private mode */ } }
  else { try { v = localStorage.getItem("rc_v2"); } catch (e) { /* private mode */ } }
  if (v !== "a" && v !== "b") v = "b";
  window.__V = v;
  document.documentElement.dataset.variant = v;
  function css(href) {
    var l = document.createElement("link"); l.rel = "stylesheet"; l.href = href;
    document.head.appendChild(l);
  }
  css(v === "b"
    ? "https://fonts.googleapis.com/css2?family=Special+Elite&family=IBM+Plex+Serif:ital,wght@0,400;0,600;1,400&family=Courier+Prime:wght@400;700&display=swap"
    : "https://fonts.googleapis.com/css2?family=Chakra+Petch:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;700;800&display=swap");
  css(v === "b" ? "/static/styles-b.css" : "/static/styles.css");
})();
