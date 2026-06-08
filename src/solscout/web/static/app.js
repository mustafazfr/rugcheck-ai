/* rugcheck.ai — frontend logic (ADR-040) */
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];

const LEVEL = {
  SAFE:     { color: "#c4f73a", soft: "rgba(196,247,58,.12)", word: "LOOKS CLEAN" },
  CAUTION:  { color: "#ffb02e", soft: "rgba(255,176,46,.12)", word: "CAUTION" },
  DANGER:   { color: "#ff7a3d", soft: "rgba(255,122,61,.13)", word: "HIGH RISK" },
  CRITICAL: { color: "#ff4d57", soft: "rgba(255,77,87,.14)",  word: "CRITICAL" },
};
const STATUS_ICON = { pass: "✓", warn: "!", fail: "✕", skip: "–", info: "i" };
const CATEGORY_ORDER = ["Authorities", "Liquidity", "Holders", "Activity", "Manipulation",
  "Bundle & Insiders", "Creator / deployer", "Buyers (wallet history)",
  "External (RugCheck)", "External (GoPlus)", "Honeypot", "Social & AI"];

/* ---------- helpers ---------- */
const usd = (v) => {
  if (v == null) return "—";
  if (v < 1) return "$" + Number(v).toPrecision(3);
  if (v < 1e3) return "$" + v.toFixed(2);
  if (v < 1e6) return "$" + (v / 1e3).toFixed(1) + "K";
  if (v < 1e9) return "$" + (v / 1e6).toFixed(2) + "M";
  return "$" + (v / 1e9).toFixed(2) + "B";
};
const num = (v) => (v == null ? "—" : Intl.NumberFormat("en", { notation: "compact" }).format(v));
const short = (a) => (a ? a.slice(0, 4) + "…" + a.slice(-4) : "—");
const age = (m) => (m == null ? "—" : m < 90 ? m + "m" : m < 1440 ? (m / 60).toFixed(1) + "h" : (m / 1440).toFixed(1) + "d");
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

/* ---------- health ---------- */
(async () => {
  try {
    const h = await (await fetch("/api/health")).json();
    const el = $("#health");
    el.classList.toggle("ok", !!h.ok);
    const on = Object.values(h.providers || {}).filter(Boolean).length;
    el.lastChild.textContent = ` ${on} providers`;
    el.title = Object.entries(h.providers).map(([k, v]) => `${k}: ${v ? "on" : "off"}`).join(" · ");
  } catch { /* offline */ }
})();

/* ---------- input ---------- */
$("#searchForm").addEventListener("submit", (e) => { e.preventDefault(); run($("#mintInput").value.trim()); });
$("#rescanForm").addEventListener("submit", (e) => { e.preventDefault(); run($("#mintInput2").value.trim()); });
$$(".chip").forEach((c) => c.addEventListener("click", () => { $("#mintInput").value = c.dataset.mint; run(c.dataset.mint); }));

const B58 = /^[1-9A-HJ-NP-Za-km-z]{32,44}$/;

// shareable / deep-link: /?mint=<addr> auto-scans on load and reflects the address in the URL
function syncUrl(mint) {
  try { history.replaceState(null, "", mint ? `/?mint=${mint}` : "/"); } catch { /* ignore */ }
}
window.addEventListener("DOMContentLoaded", () => {
  const m = new URLSearchParams(location.search).get("mint");
  if (m) { $("#mintInput").value = m; run(m); }
});

async function run(mint) {
  syncUrl(B58.test(mint) ? mint : "");
  const err = $("#err");
  err.hidden = true;
  if (!B58.test(mint)) { err.textContent = "✕ That doesn't look like a Solana mint address (base58, 32–44 chars)."; err.hidden = false; return; }

  $("#report").hidden = true;
  showScanning();
  try {
    const fetchP = fetch(`/api/check/${mint}`);     // kick off the real work
    await runScanLog(fetchP);                        // step the log, holding on AI synthesis until it lands
    const res = await fetchP;
    const data = await res.json();
    $("#scanning").hidden = true;
    if (!res.ok || data.error) {
      $("#hero").hidden = false;
      err.textContent = "✕ " + (data.detail || "Could not analyze this token right now. Try again.");
      err.hidden = false;
      window.scrollTo({ top: 0, behavior: "smooth" });
      return;
    }
    render(data);
  } catch (e) {
    $("#scanning").hidden = true;
    $("#hero").hidden = false;
    err.textContent = "✕ Network error — is the server running?"; err.hidden = false;
    window.scrollTo({ top: 0, behavior: "smooth" });
  }
}

/* ---------- scanning state ---------- */
// Honest about the real pipeline, ordered cheap→slow. The last step (deep wallet + AI work) is where the
// real time goes, so the log PARKS there in a "working" state until the request actually returns — instead
// of flashing every step "OK" in a second and then making the user stare at a frozen all-OK list.
const SCAN_STEPS = ["resolving mint & metadata", "reading on-chain authorities (mint / freeze)",
  "pulling DEX market + liquidity", "mapping holder distribution", "cross-checking RugCheck + GoPlus",
  "scanning wash-trading & insider bundles", "tracing deployer + buyer wallets",
  "AI forensic synthesis over the full report"];
function showScanning() {
  $("#hero").hidden = true;     // swap the big hero for the focused scan → report flow
  $("#report").hidden = true;
  $("#scanning").hidden = false;
  $("#scanLog").innerHTML = "";
  window.scrollTo({ top: 0, behavior: "smooth" });
}
function _logStep(log, text, state) {  // state: "working" | "ok"
  const li = document.createElement("li");
  if (state === "working") li.classList.add("cur");
  li.innerHTML = `<span>${esc(text)}</span><span class="stat ${state}">${state === "ok" ? "OK" : "working"}</span>`;
  log.appendChild(li);
  return li;
}
// Drive the log off the real request: advance through the cheap steps, but never mark the FINAL step done
// until `donePromise` settles — so the user always sees a live "working" stage explaining the wait.
async function runScanLog(donePromise) {
  const log = $("#scanLog");
  log.innerHTML = "";
  let done = false;
  Promise.resolve(donePromise).then(() => { done = true; }, () => { done = true; });
  for (let i = 0; i < SCAN_STEPS.length; i++) {
    const last = i === SCAN_STEPS.length - 1;
    const li = _logStep(log, SCAN_STEPS[i], "working");
    const stat = li.querySelector(".stat");
    const hold = last ? Infinity : (i < 4 ? 360 : 780);   // park on the last step until the work is done
    let waited = 0;
    while (!done && waited < hold) { await sleep(110); waited += 110; }
    li.classList.remove("cur");
    stat.className = "stat ok"; stat.textContent = "OK";
    if (done && !last) {                                  // work finished early → fill the rest instantly
      for (let j = i + 1; j < SCAN_STEPS.length; j++) _logStep(log, SCAN_STEPS[j], "ok");
      break;
    }
  }
  await sleep(140);
}

/* ---------- render ---------- */
function render(d) {
  const rep = $("#report");
  rep.hidden = false;
  const L = LEVEL[d.level] || LEVEL.CAUTION;
  document.documentElement.style.setProperty("--verdict", L.color);
  document.documentElement.style.setProperty("--verdict-soft", L.soft);

  // verdict text
  $("#badge").textContent = `${d.level} · ${L.word}`;
  $("#tName").textContent = d.token.name || "Unknown token";
  $("#tSym").textContent = d.token.symbol ? "$" + d.token.symbol : "";
  $("#tDex").textContent = d.token.dex || "";
  $("#tDex").style.display = d.token.dex ? "" : "none";
  $("#vSummary").textContent = d.summary;
  $("#mintLine").textContent = d.mint;
  const c = d.counts;
  $("#counts").innerHTML =
    `<span class="c-pass">● <b>${c.pass}</b> pass</span>` +
    `<span class="c-warn">▲ <b>${c.warn}</b> warn</span>` +
    `<span class="c-fail">✕ <b>${c.fail}</b> fail</span>`;
  const lk = d.token.links;
  $("#links").innerHTML = [["DexScreener", lk.dexscreener], ["Solscan", lk.solscan],
    ["Bubblemaps", lk.bubblemaps], ["RugCheck", lk.rugcheck]]
    .map(([t, u]) => `<a href="${u}" target="_blank" rel="noopener">${t} ↗</a>`).join("");

  // gauge
  setGauge(d.score);

  // the watcher reacts to the verdict — glares at rugs, eases up on clean coins
  setWatcher($("#watcherReaction"), LEVEL_MOOD[d.level] || "safe");

  // checks
  $("#checkMeta").textContent = `${c.total} checks · ${d.meta.took_ms ?? "?"}ms${d.cached ? " · cached" : ""}`;
  renderChecks(d.checks);

  // deployer / twitter / holders+buyers / sources / ai
  renderDeployer(d.deployer);
  renderTwitter(d.twitter);
  renderHolders(d.holders, d.holders_intel, d.labels);
  renderMarket(d.market, d.flow, d.overview);
  renderInsiderNetworks(d.insider_networks);
  renderSources(d.sources, d.honeypot);
  renderAI(d.ai);

  // re-trigger reveal animations
  $$(".reveal", rep).forEach((el, i) => { el.style.animation = "none"; void el.offsetWidth; el.style.animation = ""; el.style.animationDelay = (i * 0.05) + "s"; });
  window.scrollTo({ top: 0, behavior: "smooth" });
}

function setGauge(score) {
  const fill = $("#gFill"), txt = $("#gScore");
  const final = Math.round(score);
  // 270° arc → 75 units of pathLength; offset 75 = empty, 0 = full
  requestAnimationFrame(() => { fill.style.strokeDashoffset = (75 * (1 - score / 100)).toFixed(2); });
  // count-up (eased) with a hard guarantee of the final value even if rAF is throttled
  let t0 = null; const dur = 1000;
  function tick(t) {
    if (t0 === null) t0 = t;
    const p = Math.min(1, (t - t0) / dur);
    txt.textContent = Math.round(final * (1 - Math.pow(1 - p, 3)));
    if (p < 1) requestAnimationFrame(tick); else txt.textContent = final;
  }
  requestAnimationFrame(tick);
  setTimeout(() => { txt.textContent = final; }, 1150);
}

function renderChecks(checks) {
  const groups = {};
  for (const ch of checks) (groups[ch.category] ||= []).push(ch);
  const cats = Object.keys(groups).sort((a, b) => CATEGORY_ORDER.indexOf(a) - CATEGORY_ORDER.indexOf(b));
  $("#checks").innerHTML = cats.map((cat) => {
    const items = groups[cat];
    const fails = items.filter((i) => i.status === "fail").length;
    const warns = items.filter((i) => i.status === "warn").length;
    const stat = fails ? `<span style="color:var(--red)">${fails} fail</span>`
      : warns ? `<span style="color:var(--amber)">${warns} warn</span>`
      : `<span style="color:var(--lime)">clear</span>`;
    return `<div class="cat reveal"><div class="cat-h"><span>${esc(cat)}</span><span class="cstat">${stat}</span></div>` +
      items.map((i) =>
        `<div class="check s-${i.status}"><div class="ic">${STATUS_ICON[i.status] || "·"}</div>` +
        `<div class="body"><div class="lab">${esc(i.label)}</div><div class="det">${esc(i.detail)}</div></div></div>`
      ).join("") + `</div>`;
  }).join("");
}

function renderHolders(h, intel, labels) {
  if (!h || (h.count == null && !(h.distribution || []).length)) {
    $("#hStat").innerHTML = `<span class="muted-note">Holder set not fully resolvable (token too large or not indexed).</span>`;
    $("#holderBars").innerHTML = ""; return;
  }
  labels = labels || {};
  const fresh = intel && intel.profiled ? intel.fresh : null;
  $("#hStat").innerHTML =
    `<div><div class="v">${h.count != null ? num(h.count) : "—"}</div><div class="k">holders</div></div>` +
    `<div><div class="v">${h.top1_pct != null ? h.top1_pct.toFixed(1) + "%" : "—"}</div><div class="k">top wallet</div></div>` +
    `<div><div class="v">${h.top10_pct != null ? h.top10_pct.toFixed(1) + "%" : "—"}</div><div class="k">top 10</div></div>` +
    (fresh != null ? `<div><div class="v ${fresh >= 5 ? "bad" : ""}">${fresh}/${intel.profiled}</div><div class="k">fresh buyers</div></div>` : "");
  // map per-holder intel (fresh/trader) onto the bars
  const byOwner = {};
  (intel && intel.rows || []).forEach((r) => byOwner[r.owner] = r);
  const dist = (h.distribution || []).slice(0, 12);
  const max = Math.max(1, ...dist.map((x) => x.pct));
  $("#holderBars").innerHTML = dist.map((x, i) => {
    const r = byOwner[x.owner];
    const lbl = labels[x.owner];
    const tag = lbl ? `<span class="wtag known">${esc(lbl)}</span>`
      : (r ? (r.fresh ? `<span class="wtag fresh">fresh</span>` : (r.trader ? `<span class="wtag trader">trader</span>` : "")) : "");
    return `<div class="bar-row ${x.pct >= 25 ? "whale" : ""}"><span class="addr">#${i + 1} <a href="https://solscan.io/account/${x.owner}" target="_blank" rel="noopener">${short(x.owner)}</a> ${tag}</span>` +
    `<span class="bar-track"><span class="bar-fill" data-w="${(x.pct / max * 100).toFixed(1)}"></span></span>` +
    `<span class="pct">${x.pct.toFixed(1)}%</span></div>`;
  }).join("");
  if (intel && intel.profiled) {
    $("#holderBars").insertAdjacentHTML("beforeend",
      `<div class="legend"><b>fresh</b> = near-empty / brand-new wallet (almost no trade history) — ` +
      `we traced the top ${intel.profiled} holders and ${intel.fresh} look like that. ` +
      `Many fresh wallets = likely insider/sybil cluster faking the holder count. ` +
      `<span class="wtag trader">trader</span> = a real, active wallet.</div>`);
  }
  requestAnimationFrame(() => $$("#holderBars .bar-fill").forEach((b) => b.style.width = b.dataset.w + "%"));
}

function renderDeployer(dep) {
  const el = $("#deployerBody");
  if (!dep || !dep.wallet) { el.innerHTML = `<span class="muted-note">Creator wallet not resolvable for this token.</span>`; return; }
  const prior = dep.prior_creations;
  const serialBad = prior != null && prior >= 4;
  let html = `<div class="wallet-id"><code>${short(dep.wallet)}</code>` +
    `<a href="https://solscan.io/account/${dep.wallet}" target="_blank" rel="noopener">solscan ↗</a></div>`;
  html += `<div class="kv"><span class="k">Prior tokens launched</span><span class="${serialBad ? "bad" : "good"}">${prior != null ? prior : "—"}${serialBad ? " · serial deployer ⚑" : ""}</span></div>`;
  if (dep.rugcheck_tokens != null) html += `<div class="kv"><span class="k">RugCheck creator tokens</span><span>${dep.rugcheck_tokens}</span></div>`;
  html += `<div class="kv"><span class="k">Wallet age</span><span>${dep.age_days != null ? dep.age_days + "d" : "—"}</span></div>`;
  if (dep.funded_by) html += `<div class="kv"><span class="k">Funded by</span><span><a href="https://solscan.io/account/${dep.funded_by}" target="_blank" rel="noopener">${short(dep.funded_by)} ↗</a></span></div>`;
  el.innerHTML = html;
}

function renderTwitter(tw) {
  const el = $("#twitterBody");
  if (!tw || (!tw.available && tw.linked === false)) { el.innerHTML = `<span class="muted-note">No Twitter/X account linked on DexScreener.</span>`; return; }
  if (!tw.available) { el.innerHTML = `<div class="wallet-id"><code>@${esc(tw.handle)}</code><a href="${tw.url}" target="_blank" rel="noopener">open ↗</a></div><span class="muted-note">Profile not fetchable (private/suspended/renamed).</span>`; return; }
  const V = { credible: "var(--lime)", ok: "var(--lime)", weak: "var(--amber)", inauthentic: "var(--red)" }[tw.verdict] || "var(--muted)";
  el.innerHTML =
    `<div class="tw-head">` +
    (tw.avatar_url ? `<img class="tw-av" src="${esc(tw.avatar_url)}" alt="" referrerpolicy="no-referrer"/>` : "") +
    `<div class="tw-id"><div class="tw-name">${esc(tw.name || tw.handle)} ${tw.verified ? '<span class="tw-v">✓</span>' : ""}</div>` +
    `<a class="tw-handle" href="${esc(tw.url)}" target="_blank" rel="noopener">@${esc(tw.handle)} ↗</a></div>` +
    `<div class="tw-verdict" style="color:${V}">${esc((tw.verdict || "").toUpperCase())}</div></div>` +
    (tw.description ? `<p class="tw-desc">${esc(tw.description)}</p>` : "") +
    `<div class="tw-stats">` +
    `<div><b>${_compact(tw.followers)}</b><span>followers</span></div>` +
    `<div><b>${_compact(tw.tweets)}</b><span>tweets</span></div>` +
    `<div><b>${tw.age_days != null ? (tw.age_days >= 365 ? Math.floor(tw.age_days / 365) + "y" : tw.age_days + "d") : "—"}</b><span>age</span></div>` +
    `</div>` +
    `<div class="ai mtr" style="margin-top:12px"><div class="k"><span>identity authenticity</span><span>${Math.round((tw.score || 0) * 100)}</span></div><div class="t"><div class="f" style="width:${Math.round((tw.score || 0) * 100)}%;background:${V}"></div></div></div>`;
}

function _compact(v) { if (v == null) return "—"; if (v < 1000) return "" + v; if (v < 1e6) return (v / 1e3).toFixed(1) + "K"; return (v / 1e6).toFixed(1) + "M"; }

/* ---------- the watcher (reactive meme face) ---------- */
// Three faces, keyed to how bad the verdict is:
//   rugged → "surprise, motherf*****" (caught red-handed)  · CRITICAL
//   risky  → the cold "I'm watching you" stare             · DANGER / CAUTION
//   safe   → eased-up / approving                          · SAFE  (placeholder until a clean face is set)
// Real media first; an SVG silhouette is the fallback if a file is ever missing (404 → onerror swaps it in).
// currentColor inherits the theme --verdict tint.
const LEVEL_MOOD = { CRITICAL: "rugged", DANGER: "risky", CAUTION: "risky", SAFE: "safe" };
// per-face media + how long to keep it on screen before it gently fades out (≈2–3 plays of that clip, then a
// soft CSS opacity fade — not an abrupt cut). `safe` has no file yet (the user is supplying the clean "okay"
// face) → it renders the calm line-art silhouette below and just stays. Add safe here with a file to use it.
// MP4 video, not GIF — full colour, no 256-colour banding, sharper, smaller. Autoplay-muted-loop renders
// like a gif but at much higher quality. holdMs ≈ 2–3 plays of that clip before the soft fade.
const _FACE = {
  rugged: { file: "rugged.mp4", holdMs: 12700 },  // 6.33s clip × ~2 plays
  risky:  { file: "risky.mp4",  holdMs: 8200 },   // 2.73s clip × ~3 plays
};
const _WATCHER_FADE_MS = 1500;  // must match .watcher-corner transition in styles.css
const HEAD = "M22 28 Q22 13 40 13 L60 13 Q78 13 78 28 L78 55 Q78 85 50 92 Q22 85 22 55 Z";
const _FALLBACK_SVG = (mood) =>
  `<svg class="watcher-svg ${mood}" viewBox="0 0 100 100" fill="none" stroke="currentColor" ` +
  `stroke-width="3.2" stroke-linecap="round" stroke-linejoin="round">` +
  `<path class="head" d="${HEAD}" stroke-width="2.4" opacity=".55"/>` +
  (mood === "glare"
    ? `<path d="M20 35 L44 43"/><path d="M80 35 L56 43"/><path d="M27 52 L41 52" stroke-width="5"/><path d="M59 52 L73 52" stroke-width="5"/><path d="M33 74 Q50 64 67 74"/>`
    : `<path d="M22 36 L44 36"/><path d="M56 36 L78 36"/><circle cx="35" cy="50" r="2.8" fill="currentColor" stroke="none"/><circle cx="65" cy="50" r="2.8" fill="currentColor" stroke="none"/><path d="M34 72 L66 72"/>`) +
  `</svg>`;
function watcherFace(mood) {
  const hostile = (mood === "rugged" || mood === "risky");  // both glare; safe is calm
  const f = _FACE[mood];
  // no media for this mood (e.g. safe until a clean face is supplied) → render the calm silhouette directly,
  // so there's no broken/404 request. A real file, when present, falls back to the same SVG on load error.
  if (!f) return _FALLBACK_SVG(hostile ? "glare" : "watch");
  const svg = _FALLBACK_SVG(hostile ? "glare" : "watch").replace(/"/g, "&quot;");
  return `<video class="watcher-gif ${mood}" autoplay loop muted playsinline ` +
    `src="/static/watcher/${f.file}" onerror="this.outerHTML='${svg}'"></video>`;
}
// generation token so a fresh scan cancels a pending fade/clear from the previous one
let _watcherGen = 0;
function setWatcher(el, mood) {
  if (!el) return;
  const gen = ++_watcherGen;
  el.classList.remove("faded");          // re-show (a new scan revives the corner)
  el.innerHTML = watcherFace(mood);
  const f = _FACE[mood];
  if (!f) return;                        // safe line-art: no auto-fade, it just sits there calmly
  // after ~2–3 plays, gently fade out; once invisible, drop the gif so it stops looping (spares CPU)
  setTimeout(() => {
    if (gen !== _watcherGen) return;     // a newer scan already took over
    el.classList.add("faded");
    setTimeout(() => { if (gen === _watcherGen) el.innerHTML = ""; }, _WATCHER_FADE_MS + 80);
  }, f.holdMs);
}

function renderMarket(m, flow, overview) {
  if (!m || !Object.keys(m).length) { $("#metrics").innerHTML = `<span class="muted-note">No DEX market found.</span>`; $("#flow").innerHTML = ""; return; }
  const chg = m.price_change_h24;
  const chgCls = chg == null ? "" : chg >= 0 ? "up" : "down";
  const ov = overview || {};
  const lp = ov.lp_locked_pct;
  const lpCell = lp == null ? "—" : `<span class="${lp >= 90 ? "up" : lp < 50 ? "down" : ""}">${lp.toFixed(1)}%${lp >= 90 ? " 🔒" : ""}</span>`;
  $("#metrics").innerHTML = [
    ["Liquidity", usd(m.liquidity_usd)],
    ["Market cap", usd(m.market_cap)],
    ["24h volume", usd(m.volume_24h)],
    ["Pair age", age(m.age_minutes)],
    ["LP locked", lpCell],
    ["Markets", ov.markets != null ? num(ov.markets) : "—"],
  ].map(([k, v]) => `<div class="metric"><div class="k">${k}</div><div class="v">${v}</div></div>`).join("");

  const b = flow.buyers_h24, s = flow.sellers_h24;
  if (b != null && s != null && b + s > 0) {
    const buyPct = (b / (b + s) * 100).toFixed(0);
    $("#flow").innerHTML = `<div class="flabel"><span>${num(b)} buyers</span><span>${num(s)} sellers (24h)</span></div>` +
      `<div class="flow-track"><span class="flow-buy" style="width:${buyPct}%"></span></div>`;
  } else $("#flow").innerHTML = "";
}

function renderInsiderNetworks(nets) {
  const panel = $("#insiderPanel");
  if (!nets || !nets.length) { panel.hidden = true; return; }
  panel.hidden = false;
  $("#insiderBody").innerHTML =
    `<div class="muted-note" style="margin-bottom:10px">Groups of wallets that move tokens together — coordinated/insider clusters (per RugCheck's graph). A big cluster holding a large % is a manipulation tell.</div>` +
    `<table class="net-tbl"><tr><th>cluster</th><th>wallets</th><th>% supply</th></tr>` +
    nets.map((n) => {
      const big = (n.pct || 0) >= 10;
      return `<tr><td><code>${esc(n.id)}</code></td><td>${num(n.accounts)}</td>` +
        `<td class="${big ? "neg" : ""}">${n.pct != null ? n.pct.toFixed(2) + "%" : "—"}</td></tr>`;
    }).join("") + `</table>`;
}

function renderSources(sources, hp) {
  const el = $("#rcBody");
  const rc = (sources && sources.rugcheck) || { available: false };
  const gp = (sources && sources.goplus) || { available: false };
  let html = "";
  // RugCheck
  if (rc.available) {
    const s = rc.score;
    html += `<div class="src"><div class="src-h"><span>RugCheck</span><span class="src-dot" style="background:${s < 35 ? "var(--lime)" : s < 60 ? "var(--amber)" : "var(--red)"}"></span></div>` +
      `<div class="rc-score"><div class="rc-num" style="color:${s < 35 ? "var(--lime)" : s < 60 ? "var(--amber)" : "var(--red)"}">${s}</div>` +
      `<div class="rc-meter"><div class="rc-bar"><span class="rc-needle" style="left:${Math.min(100, s)}%"></span></div>` +
      `<div class="rc-cap">risk ${rc.rugged ? "· RUGGED ⚑" : "· lower = safer"}</div></div></div>` +
      ((rc.risks || []).length ? `<div class="srisks">${rc.risks.map((r) => `<span class="srisk">${esc(r)}</span>`).join("")}</div>` : "") +
      `</div>`;
  } else html += `<div class="src muted-note">RugCheck unavailable</div>`;
  // GoPlus
  if (gp.available) {
    const ok = gp.trusted || !(gp.risks || []).length;
    html += `<div class="src"><div class="src-h"><span>GoPlus</span><span class="src-dot" style="background:${ok ? "var(--lime)" : "var(--red)"}"></span></div>` +
      `<div class="src-line">${gp.trusted ? "Trusted allow-list ✓" : ((gp.risks || []).length ? "" : "No risks flagged")}</div>` +
      ((gp.risks || []).length ? `<div class="srisks">${gp.risks.map((r) => `<span class="srisk">${esc(r)}</span>`).join("")}</div>` : "") +
      (gp.holder_count != null ? `<div class="src-meta">${num(gp.holder_count)} holders · ${gp.lp_holders ?? "?"} LP holders</div>` : "") +
      `</div>`;
  } else html += `<div class="src muted-note">GoPlus unavailable</div>`;
  // honeypot
  html += `<div class="kv"><span class="k">Honeypot sell-sim (Jupiter)</span><span>` +
    (hp.simulated ? (hp.sell_ok ? `sellable${hp.round_trip_tax_pct != null ? " · tax " + hp.round_trip_tax_pct + "%" : ""}` : "CANNOT SELL ⚑") : "not run (off by default)") +
    `</span></div>`;
  el.innerHTML = html;
}

function renderAI(ai) {
  const el = $("#aiBody");
  if (!ai || !ai.summary) { el.innerHTML = `<span class="muted-note">AI analyst unavailable (local model offline).</span>`; return; }
  const via = ai.model ? `via ${esc(ai.model)}` : (ai.provider === "rules" ? "rule-based (model offline)" : "");
  el.innerHTML =
    `<div class="note">${esc(ai.summary)}</div>` +
    `<div class="muted-note" style="margin-top:12px">Reasons over the full forensic report ${via ? "· " + via : ""}. One signal — never the verdict. Not financial advice.</div>`;
}
