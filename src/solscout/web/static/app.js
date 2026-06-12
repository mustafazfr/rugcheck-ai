/* rugcheck.ai — frontend logic (ADR-040) */
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];

// Per-variant verdict palettes (ADR-046 A/B): A = neon lab tints, B = stamp-pad inks on paper.
const LEVEL_AB = {
  a: {
    SAFE:     { color: "#c4f73a", soft: "rgba(196,247,58,.12)", word: "LOOKS CLEAN" },
    CAUTION:  { color: "#ffb02e", soft: "rgba(255,176,46,.12)", word: "CAUTION" },
    DANGER:   { color: "#ff7a3d", soft: "rgba(255,122,61,.13)", word: "HIGH RISK" },
    CRITICAL: { color: "#ff4d57", soft: "rgba(255,77,87,.14)",  word: "CRITICAL" },
  },
  b: {
    SAFE:     { color: "#1e6e46", soft: "rgba(30,110,70,.10)",  word: "CLEARED" },
    CAUTION:  { color: "#9a6011", soft: "rgba(154,96,17,.10)",  word: "UNDER REVIEW" },
    DANGER:   { color: "#b3261e", soft: "rgba(179,38,30,.10)",  word: "HIGH RISK" },
    CRITICAL: { color: "#7f1d16", soft: "rgba(127,29,22,.12)",  word: "CASE CLOSED" },
  },
};
const LEVEL = LEVEL_AB[window.__V === "b" ? "b" : "a"];
const STATUS_ICON = { pass: "✓", warn: "!", fail: "✕", skip: "–", info: "i" };
const CATEGORY_ORDER = ["Authorities", "Liquidity", "Holders", "Activity", "Manipulation",
  "Bundle & Insiders", "Creator / deployer", "Buyers (wallet history)",
  "External (RugCheck)", "External (GoPlus)", "External (Jupiter)", "Honeypot", "Social & Web"];

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
  // A/B footer switch (ADR-046): name the current design, link to the other one
  const v = window.__V === "b" ? "b" : "a";
  const nameEl = $("#abName"), sw = $("#abSwitch");
  if (nameEl) nameEl.textContent = v === "b" ? "case file" : "crypto lab";
  if (sw) sw.href = `/?v=${v === "b" ? "a" : "b"}`;
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
    // variant rides the same call (A/B count); a PRO pass rides as X-Pass (no extra request either)
    const fetchP = fetch(`/api/check/${mint}?variant=${window.__V || "a"}`, _passHeaders());
    await runScanLog(fetchP);                        // step the log, parking on the slow wallet-trace stage
    const res = await fetchP;
    const data = await res.json();
    $("#scanning").hidden = true;
    if (!res.ok || data.error) {
      $("#hero").hidden = false;
      // ADR-048 trial: the daily free scan is spent → no warning text anywhere, the PRO sheet IS the answer
      if (res.status === 429 && data.scope === "daily_mints" && PAY_INFO) {
        window.scrollTo({ top: 0, behavior: "smooth" });
        openPay(true);
        return;
      }
      let msg = data.detail || "Could not analyze this token right now. Try again.";
      if (res.status === 429 && data.retry_after_s && data.scope === "burst") {
        msg = `Rate limit — wait ~${data.retry_after_s}s and try again.`;
      }
      err.textContent = "✕ " + msg;
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
// Honest about the real pipeline, ordered cheap→slow. The last step (the deep wallet trace) is where the
// real time goes, so the log PARKS there in a "working" state until the request actually returns — instead
// of flashing every step "OK" in a second and then making the user stare at a frozen all-OK list.
// Two voices, same honest pipeline: A speaks lab, B speaks case file (ADR-047) — step N maps to the
// same real work in both, so the log never lies about what's happening.
const SCAN_STEPS_AB = {
  a: ["resolving mint & metadata", "reading on-chain authorities (mint / freeze)",
    "pulling DEX market + liquidity", "mapping holder distribution", "cross-checking RugCheck + GoPlus",
    "scanning wash-trading & insider bundles", "tracing deployer + buyer wallets"],
  b: ["opening case file", "subpoenaing on-chain authorities (mint / freeze)",
    "examining the liquidity exhibit", "mapping the holder lineup", "cross-examining RugCheck + GoPlus + Jupiter",
    "dusting for wash-trading & insider prints", "interviewing deployer + buyer wallets"],
};
const SCAN_STEPS = SCAN_STEPS_AB[window.__V === "b" ? "b" : "a"];
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
  const vw = $(".verdict-wrap");
  if (vw) vw.dataset.case = (d.mint || "").slice(0, 4).toUpperCase();  // theme B's "CASE Nº SOL-XXXX" strip

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

  // checks
  $("#checkMeta").textContent = `${c.total} checks · ${d.meta.took_ms ?? "?"}ms${d.cached ? " · cached" : ""}`;
  renderChecks(d.checks);

  // deployer / twitter / holders+buyers / sources / ai
  renderDeployer(d.deployer);
  renderTwitter(d.twitter);
  renderHolders(d.holders, d.holders_intel, d.labels, d.market);
  renderMarket(d.market, d.flow, d.overview);
  renderInsiderNetworks(d.insider_networks);
  renderSources(d.sources, d.honeypot);

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

// adaptive holders (ADR-049): the full lineup + fresh-buyer forensics is the point on YOUNG case
// files; on established/big tokens it's familiar noise — collapse to the stat row + an expand link.
// Any fresh-buyer signal forces the full view regardless (forensics outrank tidiness).
const HOLDERS_FULL_MAX_AGE_D = 90, HOLDERS_FULL_MAX_COUNT = 50000;

function renderHolders(h, intel, labels, market) {
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
  let bars = dist.map((x, i) => {
    const r = byOwner[x.owner];
    const lbl = labels[x.owner];
    const tag = lbl ? `<span class="wtag known">${esc(lbl)}</span>`
      : (r ? (r.fresh ? `<span class="wtag fresh">fresh</span>` : (r.trader ? `<span class="wtag trader">trader</span>` : "")) : "");
    return `<div class="bar-row ${x.pct >= 25 ? "whale" : ""}"><span class="addr">#${i + 1} <a href="https://solscan.io/account/${x.owner}" target="_blank" rel="noopener">${short(x.owner)}</a> ${tag}</span>` +
    `<span class="bar-track"><span class="bar-fill" data-w="${(x.pct / max * 100).toFixed(1)}"></span></span>` +
    `<span class="pct">${x.pct.toFixed(1)}%</span></div>`;
  }).join("");
  if (intel && intel.profiled) {
    bars += `<div class="legend"><b>fresh</b> = near-empty / brand-new wallet (almost no trade history) — ` +
      `we traced the top ${intel.profiled} holders and ${intel.fresh} look like that. ` +
      `Many fresh wallets = likely insider/sybil cluster faking the holder count. ` +
      `<span class="wtag trader">trader</span> = a real, active wallet.</div>`;
  }
  const fill = () => requestAnimationFrame(() => $$("#holderBars .bar-fill").forEach((b) => b.style.width = b.dataset.w + "%"));
  const ageD = market && market.age_minutes != null ? market.age_minutes / 1440 : null;
  const established = (ageD != null && ageD > HOLDERS_FULL_MAX_AGE_D) ||
                      (h.count != null && h.count > HOLDERS_FULL_MAX_COUNT);
  if (established && bars && !(fresh > 0)) {
    $("#holderBars").innerHTML =
      `<div class="legend">Established token — distribution summarized above; nothing anomalous flagged ` +
      `in the lineup.</div><button class="chip" id="holdersMore">show full holder lineup</button>`;
    $("#holdersMore").addEventListener("click", () => { $("#holderBars").innerHTML = bars; fill(); });
    return;
  }
  $("#holderBars").innerHTML = bars;
  fill();
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
    _twTimeline(tw) +
    `<div class="ai mtr" style="margin-top:12px"><div class="k"><span>identity authenticity</span><span>${Math.round((tw.score || 0) * 100)}</span></div><div class="t"><div class="f" style="width:${Math.round((tw.score || 0) * 100)}%;background:${V}"></div></div></div>`;
}

// timeline forensics (ADR-046): what the account has actually been TWEETING — shill/CA/cadence tells
function _twTimeline(tw) {
  const tl = tw.timeline;
  let rows = "";
  if (tl && tl.count) {
    const shill = tl.other_ca_count >= 3;
    rows += `<div class="kv"><span class="k">recent tweets read</span><span>${tl.count}${tl.per_day ? ` · ~${tl.per_day}/day` : ""}</span></div>`;
    rows += `<div class="kv"><span class="k">posts this CA</span><span>${tl.mint_mentions ? `yes · ${tl.mint_mentions}×` : "never ⚠"}</span></div>`;
    rows += `<div class="kv"><span class="k">other token CAs shilled</span><span style="${shill ? "color:var(--red)" : ""}">${tl.other_ca_count}${shill ? " ⚑ serial shill" : ""}</span></div>`;
  }
  if (tw.id_joined_mismatch_days != null && tw.id_joined_mismatch_days > 30) {
    rows += `<div class="kv"><span class="k">identity metadata</span><span style="color:var(--red)">forged join date ⚑ (off by ${tw.id_joined_mismatch_days}d)</span></div>`;
  }
  if (tw.website_match === false) {
    rows += `<div class="kv"><span class="k">bio website</span><span style="color:var(--amber)">doesn't match token's site ⚠</span></div>`;
  } else if (tw.website_match === true) {
    rows += `<div class="kv"><span class="k">bio website</span><span>matches token's site ✓</span></div>`;
  }
  return rows ? `<div class="tw-tl" style="margin-top:10px">${rows}</div>` : "";
}

function _compact(v) { if (v == null) return "—"; if (v < 1000) return "" + v; if (v < 1e6) return (v / 1e3).toFixed(1) + "K"; return (v / 1e6).toFixed(1) + "M"; }

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
  // Jupiter (3rd independent source, ADR-046)
  const jp = (sources && sources.jupiter) || { available: false };
  if (jp.available) {
    const lab = (jp.organic_label || "").toLowerCase();
    const ok = lab !== "low" && jp.auth_consensus !== "mismatch";
    html += `<div class="src"><div class="src-h"><span>Jupiter</span><span class="src-dot" style="background:${ok ? "var(--lime)" : "var(--amber)"}"></span></div>` +
      `<div class="src-line">${jp.verified ? "Verified list ✓" : "Not verified (normal for new tokens)"}` +
      (jp.organic_score != null ? ` · organic ${Math.round(jp.organic_score)} (${esc(jp.organic_label || "?")})` : "") + `</div>` +
      ((jp.tags || []).length ? `<div class="srisks">${jp.tags.map((t) => `<span class="srisk" style="border-color:var(--line)">${esc(t)}</span>`).join("")}</div>` : "") +
      ((jp.holder_count != null || jp.dev_mints != null)
        ? `<div class="src-meta">${jp.holder_count != null ? num(jp.holder_count) + " holders" : ""}${jp.holder_count != null && jp.dev_mints != null ? " · " : ""}${jp.dev_mints != null ? "dev launched " + jp.dev_mints + " tokens" : ""}</div>` : "") +
      (jp.auth_consensus === "mismatch" ? `<div class="srisks"><span class="srisk">authority data mismatch ⚑</span></div>` : "") +
      `</div>`;
  } else html += `<div class="src muted-note">Jupiter unavailable</div>`;
  // honeypot
  html += `<div class="kv"><span class="k">Honeypot sell-sim (Jupiter)</span><span>` +
    (hp.simulated ? (hp.sell_ok ? `sellable${hp.round_trip_tax_pct != null ? " · tax " + hp.round_trip_tax_pct + "%" : ""}` : "CANNOT SELL ⚑") : "not run (off by default)") +
    `</span></div>`;
  el.innerHTML = html;
}


/* ---------- PRO pass (ADR-047): one-time payment → unlimited scans. NON-CUSTODIAL ----------
   The buyer's own wallet signs ONE SystemProgram transfer straight to the site's payout address,
   tagged with a server-issued reference pubkey; the backend verifies it on-chain and mints an
   HMAC pass token. No keys, no funds, no accounts ever touch this code. */
const PASS_KEY = "rc_pass";
function getPass() { try { return JSON.parse(localStorage.getItem(PASS_KEY) || "null"); } catch { return null; } }
function setPass(p) { try { localStorage.setItem(PASS_KEY, JSON.stringify(p)); } catch { /* private mode */ } }
function _passHeaders() { const p = getPass(); return p && p.wallet && p.token ? { headers: { "X-Pass": p.wallet + "." + p.token } } : {}; }

let PAY_INFO = null;
(async () => {  // probe once: the PRO button exists only when the server actually sells passes
  try {
    const s = await (await fetch("/api/pay/status")).json();
    if (!s.enabled) return;
    PAY_INFO = s;
    const btn = $("#proBtn");
    btn.hidden = false;
    _syncProBtn();
    btn.addEventListener("click", openPay);
    $("#payClose").addEventListener("click", closePay);
    $("#payModal").addEventListener("click", (e) => { if (e.target.id === "payModal") closePay(); });
    document.addEventListener("keydown", (e) => { if (e.key === "Escape") closePay(); });
    $("#payGo").addEventListener("click", buyFlow);
    $("#payRestore").addEventListener("click", restoreFlow);
  } catch { /* payments off / offline */ }
})();

function _syncProBtn() {
  const btn = $("#proBtn"), p = getPass();
  if (!btn || !PAY_INFO) return;
  btn.textContent = p ? "★ PRO ✓" : `★ PRO · ${PAY_INFO.price_sol} SOL`;
  btn.title = p ? `PRO active on ${short(p.wallet)} — unlimited scans` : "One-time payment → unlimited scans, forever";
  btn.classList.toggle("active", !!p);
}
function openPay(fromLimit) {
  // fromLimit === true ONLY from the daily-limit path (as a click handler the arg is an Event object)
  $("#payTitle").textContent = fromLimit === true ? "Today's free scan is used" : "PRO pass";
  $("#payPrice").textContent = `${PAY_INFO.price_sol} SOL`;
  $("#payErr").hidden = true;
  const p = getPass();
  $("#payOk").hidden = !p;
  $("#payGo").style.display = p ? "none" : "";
  $("#payRestore").style.display = p ? "none" : "";
  _payStep("");
  $("#payModal").hidden = false;
}
function closePay() { $("#payModal").hidden = true; }
function _payStep(t) { $("#payStep").textContent = t; }
function _paySuccess() { _payStep(""); $("#payOk").hidden = false; $("#payGo").style.display = "none"; $("#payRestore").style.display = "none"; }

function walletProvider() {
  return (window.phantom && window.phantom.solana) || window.solana || window.solflare ||
         (window.backpack && window.backpack.solana) || null;
}
// web3.js is SELF-HOSTED (static/vendor/, sha256-verified against two independent CDNs at vendoring
// time) — zero third-party JS in the payment path, so a CDN compromise can't touch the payout flow,
// and the CSP allows no external scripts at all. Still lazy: loads only when someone clicks pay.
let _w3p = null;
function loadWeb3() {
  if (window.solanaWeb3) return Promise.resolve(window.solanaWeb3);
  if (_w3p) return _w3p;
  _w3p = new Promise((res, rej) => {
    const s = document.createElement("script");
    s.src = "/static/vendor/web3-1.95.8.min.js";
    s.onload = () => (window.solanaWeb3 ? res(window.solanaWeb3) : rej(new Error("wallet library failed to load")));
    s.onerror = () => rej(new Error("wallet library failed to load"));
    document.head.appendChild(s);
  });
  return _w3p;
}
async function _pj(url, opts) {  // fetch JSON; "pending" 200s pass through, hard errors throw with detail
  const res = await fetch(url, opts);
  const j = await res.json().catch(() => ({}));
  if (!res.ok && !j.pending) throw new Error(j.detail || ("HTTP " + res.status));
  return j;
}

async function buyFlow() {
  const err = $("#payErr");
  err.hidden = true;
  const prov = walletProvider();
  if (!prov) {
    err.textContent = "No Solana wallet found — install Phantom/Solflare, or open this page inside your wallet app's browser.";
    err.hidden = false;
    return;
  }
  try {
    _payStep("connecting wallet…");
    const w3 = await loadWeb3();
    const conn = await prov.connect();
    const payer = new w3.PublicKey(((conn && conn.publicKey) || prov.publicKey).toString());
    _payStep("preparing payment…");
    const intent = await _pj("/api/pay/intent", { method: "POST" });
    const bh = await _pj("/api/pay/blockhash");
    const tx = new w3.Transaction({ feePayer: payer, recentBlockhash: bh.blockhash });
    const ix = w3.SystemProgram.transfer({
      fromPubkey: payer, toPubkey: new w3.PublicKey(intent.payout), lamports: intent.lamports,
    });
    // the server-issued reference rides as a read-only key — it's how the backend finds & binds THIS payment
    ix.keys.push({ pubkey: new w3.PublicKey(intent.reference), isSigner: false, isWritable: false });
    tx.add(ix);
    _payStep("waiting for your signature…");
    const sent = await prov.signAndSendTransaction(tx);
    const sig = String((sent && sent.signature) || sent);
    _payStep("confirming on-chain… (a few seconds)");
    const deadline = Date.now() + ((PAY_INFO.confirm_wait_s || 90) * 1000);
    while (Date.now() < deadline) {
      const r = await _pj("/api/pay/confirm", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ intent_id: intent.intent_id, sig }),
      });
      if (r.ok) { setPass({ wallet: r.wallet, token: r.token }); _syncProBtn(); _paySuccess(); return; }
      if (!r.pending) throw new Error(r.detail || "Payment rejected.");
      await sleep(5000);
    }
    throw new Error("Confirmation timed out — if the transfer DID go through, use “restore with wallet” in a minute.");
  } catch (e) {
    err.textContent = (e && e.message) || "Payment failed.";
    err.hidden = false;
    _payStep("");
  }
}

async function restoreFlow() {  // new device / cleared storage: prove ownership via signMessage
  const err = $("#payErr");
  err.hidden = true;
  const prov = walletProvider();
  if (!prov) { err.textContent = "No Solana wallet found."; err.hidden = false; return; }
  try {
    _payStep("proving wallet ownership…");
    await prov.connect();
    const wallet = prov.publicKey.toString();
    const ts = Math.floor(Date.now() / 1000);
    const signed = await prov.signMessage(new TextEncoder().encode(`rugcheck-pass:${wallet}:${ts}`), "utf8");
    const bytes = new Uint8Array((signed && signed.signature) || signed);
    const sig_hex = [...bytes].map((b) => b.toString(16).padStart(2, "0")).join("");
    const r = await _pj("/api/pay/restore", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ wallet, ts, sig_hex }),
    });
    setPass({ wallet: r.wallet, token: r.token });
    _syncProBtn();
    _paySuccess();
  } catch (e) {
    err.textContent = (e && e.message) || "Restore failed — no pass found on that wallet?";
    err.hidden = false;
    _payStep("");
  }
}
