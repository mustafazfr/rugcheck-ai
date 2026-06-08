"""Local web dashboard — `solscout dashboard` serves http://localhost:<port> with everything we measure.

Zero new dependencies (stdlib http.server + sqlite3 via analytics). Read-only; auto-refreshes. Renders
KPIs, paper PnL/win-rate, verdict distribution, open positions, recent decisions, threshold sensitivity,
and the honest health notes (e.g. "watchlist empty → only quality-tier buys").
"""

from __future__ import annotations

import html
import json as _json
import time
import urllib.request
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import analytics

# live current prices for open positions — one BATCHED DexScreener call (free, no key), short-cached so the
# 5s auto-refresh mostly hits the cache instead of the network. Keeps the dashboard responsive.
_PRICE_TTL = 12.0
_prices_cache: dict[str, tuple[float, dict[str, float | None]]] = {}


def _live_prices(mints: list[str]) -> dict[str, float | None]:
    if not mints:
        return {}
    key = ",".join(sorted(mints))
    hit = _prices_cache.get(key)
    if hit and (time.monotonic() - hit[0]) < _PRICE_TTL:
        return hit[1]
    out: dict[str, float | None] = {m: None for m in mints}
    best_liq: dict[str, float] = {}
    try:
        url = "https://api.dexscreener.com/latest/dex/tokens/" + ",".join(mints)
        req = urllib.request.Request(url, headers={"User-Agent": "solscout-dashboard"})
        with urllib.request.urlopen(req, timeout=6) as r:
            data = _json.loads(r.read().decode())
        for p in (data or {}).get("pairs") or []:
            addr = (p.get("baseToken") or {}).get("address")
            pu = p.get("priceUsd")
            if addr in out and pu:
                liq = (p.get("liquidity") or {}).get("usd") or 0  # keep the most-liquid pair's price
                if liq >= best_liq.get(addr, -1):
                    best_liq[addr] = liq
                    out[addr] = float(pu)
    except Exception:
        pass
    _prices_cache[key] = (time.monotonic(), out)
    return out

_CSS = """
*{box-sizing:border-box} body{margin:0;font:14px/1.5 -apple-system,Segoe UI,Roboto,sans-serif;
background:#0b0e14;color:#cdd6f4} h1{font-size:18px;margin:0} h2{font-size:13px;text-transform:uppercase;
letter-spacing:.08em;color:#7f849c;margin:24px 0 8px} .wrap{max-width:1100px;margin:0 auto;padding:20px}
.head{display:flex;justify-content:space-between;align-items:center;border-bottom:1px solid #1e2230;padding-bottom:12px}
.tag{font-size:11px;padding:2px 8px;border-radius:10px;background:#1e2230;color:#89b4fa}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin-top:14px}
.card{background:#11151f;border:1px solid #1e2230;border-radius:10px;padding:14px}
.card .k{font-size:11px;color:#7f849c;text-transform:uppercase;letter-spacing:.05em}
.card .v{font-size:24px;font-weight:600;margin-top:4px} .pos{color:#a6e3a1}.neg{color:#f38ba8}.warn{color:#f9e2af}
table{width:100%;border-collapse:collapse;background:#11151f;border:1px solid #1e2230;border-radius:10px;overflow:hidden}
th,td{text-align:left;padding:8px 12px;border-bottom:1px solid #1e2230;font-variant-numeric:tabular-nums}
th{color:#7f849c;font-size:11px;text-transform:uppercase} tr:last-child td{border-bottom:none}
.BUY{color:#a6e3a1;font-weight:600}.WATCH{color:#f9e2af;font-weight:600}.REJECT{color:#f38ba8}
.note{background:#1a1d10;border:1px solid #45412a;color:#f9e2af;border-radius:8px;padding:10px 12px;margin:6px 0}
.bar{height:10px;border-radius:5px;background:#1e2230;overflow:hidden;display:flex}
.muted{color:#7f849c} a{color:#89b4fa}
"""


def _pct_bar(verdicts: dict) -> str:
    total = sum(verdicts.values()) or 1
    seg = {"BUY": "#a6e3a1", "WATCH": "#f9e2af", "REJECT": "#f38ba8", "PENDING": "#585b70"}
    parts = "".join(
        f'<div style="width:{100 * verdicts.get(k, 0) / total:.1f}%;background:{c}" title="{k}: {verdicts.get(k, 0)}"></div>'
        for k, c in seg.items()
    )
    return f'<div class="bar">{parts}</div>'


def _kpi(k: str, v: str, cls: str = "") -> str:
    return f'<div class="card"><div class="k">{k}</div><div class="v {cls}">{v}</div></div>'


def _mint_links(mint: str) -> str:
    """Full, clickable mint — short visible label, real links to DexScreener + Solscan token pages."""
    m = html.escape(mint)
    short = html.escape(mint[:6] + "…" + mint[-4:])
    return (
        f'<span title="{m}">{short}</span> '
        f'<a href="https://dexscreener.com/solana/{m}" target="_blank">dex↗</a> '
        f'<a href="https://app.bubblemaps.io/sol/token/{m}" target="_blank">bubble↗</a> '
        f'<a href="https://solscan.io/token/{m}" target="_blank">scan↗</a>'
    )


def _price(v) -> str:
    """Format a (often tiny) meme-coin USD price."""
    if v is None:
        return "—"
    if v == 0:
        return "$0"
    if v < 0.001:
        return f"${v:.8f}"
    if v < 1:
        return f"${v:.6f}"
    return f"${v:,.4f}"


def _dur(a: str | None, b: str | None = None) -> str:
    """Human held-duration between two ISO timestamps; b=None means 'until now' (open position)."""
    if not a:
        return "—"
    try:
        start = datetime.fromisoformat(a)
        end = datetime.fromisoformat(b) if b else datetime.now(timezone.utc)
        m = (end - start).total_seconds() / 60
        return f"{m:.0f} dk" if m < 90 else f"{m / 60:.1f} sa"
    except (ValueError, TypeError):
        return "—"


def _open_row(p: dict, now: float | None) -> str:
    entry = p.get("avg_price_usd")
    pct = (now / entry - 1) * 100 if entry and now else None
    cls = "pos" if (pct or 0) >= 0 else "neg"
    now_s = f'<span class="{cls}">{_price(now)}</span>' if now is not None else '<span class="muted">—</span>'
    pct_s = f'<span class="{cls}">{pct:+.1f}%</span>' if pct is not None else '<span class="muted">…</span>'
    return (
        f'<tr><td>{_mint_links(p["mint"])}</td>'
        f'<td>{(p.get("sol_invested") or 0):.4f} ◎</td>'
        f"<td>{_price(entry)}</td><td>{now_s}</td><td>{pct_s}</td>"
        f'<td class="muted">{_dur(p.get("opened_at"))}</td></tr>'
    )


def _trade_row(t: dict) -> str:
    entry, exit_ = t.get("entry"), t.get("exit_price")
    pnl = t.get("realized_pnl_sol") or 0.0
    pct = (exit_ / entry - 1) * 100 if entry and exit_ else None
    cls = "pos" if pnl >= 0 else "neg"
    pct_s = f'<span class="{cls}">{pct:+.1f}%</span>' if pct is not None else "—"
    return (
        f'<tr><td>{_mint_links(t["mint"])}</td>'
        f"<td>{_price(entry)}</td><td>{_price(exit_)}</td>"
        f'<td>{(t.get("sol_invested") or 0):.4f} ◎</td>'
        f'<td class="{cls}">{pnl:+.4f} ◎</td><td>{pct_s}</td>'
        f'<td class="muted">{_dur(t.get("opened_at"), t.get("closed_at"))}</td>'
        f'<td class="muted">{html.escape((t.get("closed_at") or "")[:19])}</td></tr>'
    )


def render(db_path: str) -> str:
    s = analytics.compute_stats(db_path)
    pnl_cls = "pos" if s.realized_pnl_total > 0 else ("neg" if s.realized_pnl_total < 0 else "")
    wr = f"{s.win_rate}%" if s.win_rate is not None else "—"
    cards = "".join(
        [
            _kpi("Coins seen", str(s.candidates_total)),
            _kpi("Decisions", str(s.decisions_total)),
            _kpi("Paper buys", str(s.buys)),
            _kpi("BUY smart/quality", f"{s.buy_smart}/{s.buy_quality}"),
            _kpi("Open positions", f"{s.open_count}"),
            _kpi("Open exposure", f"{s.open_exposure_sol:.3f} ◎"),
            _kpi("Realized PnL", f"{s.realized_pnl_total:+.4f} ◎", pnl_cls),
            _kpi("Win rate", wr),
            _kpi("Closed trades", f"{s.wins}W / {s.losses}L"),
            _kpi("Watchlist", str(s.watchlist_size), "warn" if s.watchlist_size == 0 else ""),
            _kpi("Re-eval queue", str(s.queue_size)),
            _kpi("Helius credits (est., mo)", f"{s.credits_used_month:,.0f}"),
        ]
    )
    notes = "".join(f'<div class="note">⚠️ {html.escape(n)}</div>' for n in s.health_notes)

    dec_rows = "".join(
        f'<tr><td class="{html.escape(d["verdict"])}">{html.escape(d["verdict"])}</td>'
        f"<td>{d['composite_score']:.1f}</td><td>{_mint_links(d['mint'])}</td>"
        f'<td class="muted">{html.escape((d["created_at"] or "")[:19])}</td></tr>'
        for d in analytics.recent_decisions(db_path, 20)
    )
    open_pos = analytics.open_positions(db_path)
    live = _live_prices([p["mint"] for p in open_pos])
    pos_rows = (
        "".join(_open_row(p, live.get(p["mint"])) for p in open_pos)
        or '<tr><td colspan="6" class="muted">açık pozisyon yok</td></tr>'
    )
    closed_rows = (
        "".join(_trade_row(t) for t in analytics.closed_trades(db_path, 50))
        or '<tr><td colspan="8" class="muted">henüz kapanan işlem yok</td></tr>'
    )
    thr_rows = "".join(
        f'<tr><td>≥ {t["threshold"]}</td><td>{t["bought"]}</td><td class="muted">{t.get("score_reached", 0)}</td></tr>'
        for t in analytics.threshold_sensitivity(db_path)
    )
    rej_rows = (
        "".join(
            f'<tr><td class="REJECT">{html.escape(r["flag"])}</td><td>{r["n"]}</td></tr>'
            for r in analytics.reject_reasons(db_path, 12)
        )
        or '<tr><td colspan="2" class="muted">none</td></tr>'
    )
    watch_rows = (
        "".join(
            f"<tr><td>{w['score']:.1f}</td><td>{_mint_links(w['mint'])}</td>"
            f'<td class="muted" style="max-width:480px">{html.escape(w.get("llm") or "—")}</td></tr>'
            for w in analytics.top_watch(db_path, 15)
        )
        or '<tr><td colspan="3" class="muted">none yet</td></tr>'
    )

    span = f"{(s.first_decision or '—')[:19]} → {(s.last_decision or '—')[:19]}"
    return f"""<!doctype html><html><head><meta charset="utf-8">
<meta http-equiv="refresh" content="5"><title>SolScout</title><style>{_CSS}</style></head><body><div class="wrap">
<div class="head"><h1>🛰️ SolScout <span class="muted">paper dashboard</span></h1>
<span class="tag">auto-refresh 5s · read-only</span></div>
<div class="muted" style="margin-top:6px">decision span: {span}</div>
{notes}
<div class="cards">{cards}</div>
<h2>🏆 Top clean coins (AI-vetted, ranked) — with Qwen take</h2>
<table><tr><th>score</th><th>token</th><th>🤖 Qwen analysis</th></tr>{watch_rows}</table>
<div class="muted" style="margin-top:4px">Coins that cleared rug + maturity + opportunity filters AND weren't flagged as likely rugs by Qwen, ranked by score. A short/empty list is NORMAL — most coins are junk; the bot is still working (see Recent decisions). Click to inspect.</div>
<h2>Verdict distribution</h2>{_pct_bar(s.verdicts)}
<div class="muted" style="margin-top:6px">BUY {s.verdicts.get("BUY", 0)} · WATCH {s.verdicts.get("WATCH", 0)} · REJECT {s.verdicts.get("REJECT", 0)}</div>
<div style="display:grid;grid-template-columns:2fr 1fr;gap:20px;margin-top:8px">
<div><h2>Recent decisions</h2><table><tr><th>verdict</th><th>score</th><th>mint</th><th>time</th></tr>{dec_rows or "<tr><td colspan=4 class=muted>none</td></tr>"}</table></div>
<div><h2>Buys by threshold</h2><table><tr><th>threshold</th><th>bought</th><th>score≥</th></tr>{thr_rows}</table>
<div class="muted" style="margin-top:4px">"bought" = actual BUY verdicts at score≥T (tier bar + opportunity + maturity). "score≥" = merely reached the score.</div>
<h2>🛡️ Neden elendi (rug/manipülasyon)</h2><table><tr><th>sebep</th><th>adet</th></tr>{rej_rows}</table>
<div class="muted" style="margin-top:4px">Funnel'ın seni koruduğu yer: wash_volume/lopsided_flow/hyper_pump = manipülasyon · holder_concentration_extreme/single_wallet_dominant/freeze = rug riski.</div></div>
</div>
<h2>📈 Açık pozisyonlar (canlı)</h2>
<table><tr><th>token</th><th>yatırılan</th><th>giriş</th><th>şu an</th><th>değişim</th><th>açık süre</th></tr>{pos_rows}</table>
<div class="muted" style="margin-top:4px">"şu an" = DexScreener canlı fiyatı (~12 sn cache) · "değişim" = girişe göre anlık kâr/zarar. Çıkış kuralları: SL −%35 · TP +%50/100/300 · trailing −%25 · time-stop 240 dk.</div>
<h2>📜 Geçmiş işlemler (kapanan paper-trade'ler)</h2>
<table><tr><th>token</th><th>giriş</th><th>çıkış</th><th>yatırılan</th><th>PnL</th><th>%</th><th>süre</th><th>kapandı</th></tr>{closed_rows}</table>
<div class="muted" style="margin-top:4px">Her satır: nereden alındı (giriş) → kaçtan satıldı (çıkış), gerçekleşen kâr/zarar ve ne kadar tutulduğu. En yeni en üstte. Token'a tıkla → DexScreener/Solscan.</div>
<p class="muted" style="margin-top:24px">Paper mode · not financial advice. PnL is simulated (slippage+fee modeled).</p>
</div></body></html>"""


def serve(db_path: str, port: int = 8787) -> None:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path not in ("/", "/index.html"):
                self.send_error(404)
                return
            body = render(db_path).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_):  # quiet
            pass

    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
