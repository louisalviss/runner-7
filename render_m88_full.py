#!/usr/bin/env python3
import argparse
import html
import json
import re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

VIRTUAL_RE = re.compile(r"virtual|esoccer|e-soccer|pes\s?\d|simulated|cyber|\(v\)", re.I)
SCOPES = {"live": "Trực tiếp", "today": "Hôm nay", "early": "Sắp tới"}
MARKET_TITLES = {
    "ft_asian_handicap": "CƯỢC CHẤP TOÀN TRẬN",
    "ft_over_under": "TÀI / XỈU TOÀN TRẬN",
    "ft_1x2": "1X2 TOÀN TRẬN",
    "fh_asian_handicap": "CƯỢC CHẤP HIỆP 1",
    "fh_over_under": "TÀI / XỈU HIỆP 1",
    "fh_1x2": "1X2 HIỆP 1",
    "ft_odd_even": "TOÀN TRẬN LẺ / CHẴN",
    "fh_odd_even": "HIỆP 1 LẺ / CHẴN",
    "double_chance": "CƠ HỘI KÉP",
    "ht_ft": "H1 / T.T",
    "correct_score": "CƯỢC TỈ SỐ",
    "ft_total_goals": "TỔNG BÀN THẮNG TOÀN TRẬN",
    "fh_total_goals": "TỔNG BÀN THẮNG HIỆP 1",
    "first_last_goal": "BÀN THẮNG ĐẦU / CUỐI",
}
SPECIAL_BASE_TITLES = {
    "ft_asian_handicap": "CƯỢC CHẤP",
    "ft_over_under": "TÀI / XỈU",
    "ft_1x2": "1X2",
    "fh_asian_handicap": "CƯỢC CHẤP H1",
    "fh_over_under": "TÀI / XỈU H1",
    "fh_1x2": "1X2 H1",
    "ft_odd_even": "LẺ / CHẴN",
    "fh_odd_even": "LẺ / CHẴN H1",
}
MARKET_ORDER = [
    "ft_asian_handicap", "ft_over_under", "ft_1x2",
    "fh_asian_handicap", "fh_over_under", "fh_1x2",
    "double_chance", "ht_ft", "correct_score",
    "ft_total_goals", "fh_total_goals", "first_last_goal",
    "ft_odd_even", "fh_odd_even",
]


def esc(v):
    return html.escape("" if v is None else str(v), quote=True)


def fmt_price(v):
    try:
        return f"{float(v):.2f}"
    except Exception:
        return "—"


def league_name(m):
    return (m.get("league") or {}).get("name") or "Khác"


def is_virtual(m):
    s = " ".join([league_name(m), m.get("home") or "", m.get("away") or ""])
    return bool(VIRTUAL_RE.search(s))


def fmt_updated(value):
    try:
        return datetime.fromisoformat(value).astimezone(ZoneInfo("Asia/Ho_Chi_Minh")).strftime("%H:%M:%S")
    except Exception:
        return value or "—"


def fmt_match_date(value):
    s = str(value or "")
    try:
        return datetime.strptime(s[:12], "%Y%m%d%H%M").strftime("%d/%m %H:%M")
    except Exception:
        return s or "Prematch"


def home_score(raw):
    s = str(raw or "")
    if "_" in s:
        p = s.split("_")
        return p[1] if len(p) > 1 and p[1] != "" else p[0]
    return s or "0"


def away_score(raw):
    s = str(raw or "")
    return (s.split("_")[0] if "_" in s else s) or "0"


def live_clock(m):
    raw = str(m.get("live_timer") or "LIVE").replace("`", "'")
    round_id = str(m.get("event_round") or "")
    mm = re.search(r"(\d{1,3})", raw)
    base = mm.group(1) if mm else ""
    if not round_id and base.isdigit():
        round_id = "1" if int(base) <= 45 else "3"
    prefix = {"1": "1H", "2": "HT", "3": "2H"}.get(round_id, "")
    shown = "HT" if round_id == "2" else f"{prefix} {raw}".strip()
    attrs = f'data-live-clock data-raw="{esc(raw)}" data-round="{esc(round_id)}"'
    if base and "+" not in raw:
        attrs += f' data-base-min="{esc(base)}"'
    return f'<span class="clock" {attrs}>{esc(shown)}</span>'


def line_value(v):
    if v is None:
        return ""
    if isinstance(v, (int, float)):
        return f"{v:g}"
    return str(v)


def selection_label(sel, family, line=None):
    s = str(sel or "")
    low = s.lower()
    labels = {
        "home": "Nhà", "away": "Khách", "draw": "Hòa",
        "over": "Tài", "under": "Xỉu", "odd": "Lẻ", "even": "Chẵn",
        "1x": "1X", "12": "12", "x2": "X2",
        "home_home": "Nhà / Nhà", "home_draw": "Nhà / Hòa", "home_away": "Nhà / Khách",
        "draw_home": "Hòa / Nhà", "draw_draw": "Hòa / Hòa", "draw_away": "Hòa / Khách",
        "away_home": "Khách / Nhà", "away_draw": "Khách / Hòa", "away_away": "Khách / Khách",
        "first_goal_home": "Bàn đầu Nhà", "first_goal_away": "Bàn đầu Khách",
        "last_goal_home": "Bàn cuối Nhà", "last_goal_away": "Bàn cuối Khách", "no_goal": "Không bàn",
    }
    base = labels.get(low, s)
    if family in {"asian_handicap", "over_under"} and line is not None:
        return f"{base} {line_value(line)}"
    return base


def market_title(name):
    if name.startswith("special::"):
        parts = name.split("::", 2)
        label = parts[1] if len(parts) > 1 and parts[1] else "Special"
        base_name = parts[2] if len(parts) > 2 else ""
        base_title = SPECIAL_BASE_TITLES.get(base_name, MARKET_TITLES.get(base_name, base_name.replace("_", " ").upper()))
        return f"SPECIAL · {base_title}" if label.casefold() == "special" else f"{label} · {base_title}"
    return MARKET_TITLES.get(name, name.replace("_", " ").upper())


def ordered_market_names(markets):
    known = [name for name in MARKET_ORDER if markets.get(name)]
    special = [name for name, lines in markets.items() if lines and name.startswith("special::")]
    other = [name for name, lines in markets.items() if lines and name not in MARKET_ORDER and not name.startswith("special::")]
    return known + sorted(special) + sorted(other)


def market_group(name, lines):
    if not lines:
        return ""
    title = market_title(name)
    blocks = []
    for line in lines:
        family = line.get("family") or ""
        prices = [p for p in (line.get("prices") or []) if p.get("value") is not None]
        if not prices:
            continue
        cls = "grid3" if len(prices) == 3 or family in {"correct_score", "ht_ft"} else "grid2"
        cells = []
        for p in prices:
            label = selection_label(p.get("selection"), family, line.get("line"))
            cells.append(f'<div class="odd"><span>{esc(label)}</span><b>{fmt_price(p.get("value"))}</b></div>')
        blocks.append(f'<div class="odds-grid {cls}">{"".join(cells)}</div>')
    if not blocks:
        return ""
    special_cls = " special-market" if name.startswith("special::") else ""
    return f'<section class="market{special_cls}"><h3>{esc(title)}</h3>{"".join(blocks)}</section>'


def match_details(m, scope, idx):
    markets = m.get("markets") or {}
    names = ordered_market_names(markets)
    market_body = "".join(market_group(name, markets.get(name) or []) for name in names)
    if not market_body:
        market_body = '<div class="empty">Không có odds.</div>'
    if scope == "live":
        status = '<span class="live-tag">LIVE</span>' + f'<span class="score">{esc(home_score(m.get("home_score")))} : {esc(away_score(m.get("away_score")))}</span>' + live_clock(m)
    else:
        status = f'<span class="kickoff">{esc(fmt_match_date(m.get("match_date")))}</span>'
    return f'''
<details class="match" id="match-{scope}-{idx}">
  <summary class="match-summary">
    <span class="match-name"><b>{esc(m.get("home"))}</b><em>vs</em><span>{esc(m.get("away"))}</span></span>
    <span class="match-meta">{status}</span>
    <span class="more">+{len(names)}</span>
  </summary>
  <div class="detail">
    <div class="detail-head"><button class="close" type="button" data-close-details>‹</button><div><strong>{esc(m.get("home"))} vs {esc(m.get("away"))}</strong><small>{status} · {esc(league_name(m))}</small></div></div>
    <div class="markets">{market_body}</div>
  </div>
</details>'''


def league_sections(rows, scope):
    if not rows:
        return '<div class="empty">Không có trận.</div>'
    out, current, items, idx = [], None, [], 0
    def flush(name, ms, start):
        if not ms:
            return "", start
        cards = []
        for x in ms:
            cards.append(match_details(x, scope, start)); start += 1
        return f'<section class="league"><div class="league-head"><span>{esc(name)}</span><b>{len(ms)}</b></div>{"".join(cards)}</section>', start
    for m in rows:
        ln = league_name(m)
        if current is None:
            current = ln
        if ln != current:
            block, idx = flush(current, items, idx); out.append(block); current, items = ln, []
        items.append(m)
    block, idx = flush(current, items, idx); out.append(block)
    return "".join(out)


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--input", required=True); ap.add_argument("--output", required=True); args = ap.parse_args()
    data = json.loads(Path(args.input).read_text(encoding="utf-8"))
    matches = [m for m in (data.get("matches") or []) if not is_virtual(m)]
    grouped = {scope: [m for m in matches if m.get("scope") == scope] for scope in SCOPES}
    counts = {scope: len(rows) for scope, rows in grouped.items()}
    sections = {scope: league_sections(rows, scope) for scope, rows in grouped.items()}
    generated = data.get("live_generated_at") or data.get("generated_at") or ""
    updated = fmt_updated(generated)
    total_selections = (data.get("counts") or {}).get("total_selections") or 0
    doc = f'''<!doctype html><html lang="vi"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover,maximum-scale=1"><meta name="theme-color" content="#172137"><meta name="apple-mobile-web-app-capable" content="yes"><title>Sports Odds</title>
<style>
:root{{--navy:#172137;--league:#566783;--peach:#faebe7;--red:#c92816;--yellow:#f1c927;--blue:#5e8be2;--ink:#273852}}
*{{box-sizing:border-box}}html,body{{margin:0;background:#fff;color:var(--ink);font-family:Arial,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;-webkit-text-size-adjust:100%}}button{{font:inherit}}summary{{list-style:none}}summary::-webkit-details-marker{{display:none}}input.scope{{position:absolute;opacity:0;pointer-events:none}}
.app{{max-width:900px;margin:auto;min-height:100vh;background:#fff}}.top{{position:sticky;top:0;z-index:50;background:var(--navy);color:#fff}}.scope-tabs{{height:50px;display:grid;grid-template-columns:repeat(3,1fr)}}.scope-tabs label{{display:flex;align-items:center;justify-content:center;gap:6px;font-weight:800;font-size:13px;color:#b9c0cc;border-right:1px solid #ffffff18}}.scope-tabs b{{font-size:10px;background:#ffffff17;padding:2px 6px;border-radius:8px}}#scope-live:checked~.app label[for=scope-live],#scope-today:checked~.app label[for=scope-today],#scope-early:checked~.app label[for=scope-early]{{color:var(--yellow);border-bottom:3px solid var(--yellow)}}.statusbar{{height:25px;display:flex;align-items:center;justify-content:space-between;padding:0 10px;background:#222f4a;color:#aeb8c8;font-size:10px}}
.panel{{display:none}}#scope-live:checked~.app .live-panel,#scope-today:checked~.app .today-panel,#scope-early:checked~.app .early-panel{{display:block}}.league{{margin-bottom:8px}}.league-head{{min-height:43px;background:var(--league);color:#fff;padding:8px 12px;display:flex;align-items:center;gap:8px;text-transform:uppercase}}.league-head span{{flex:1;font-size:13px}}.league-head b{{background:var(--red);min-width:27px;height:27px;display:flex;align-items:center;justify-content:center;font-size:11px}}
.match{{background:var(--peach);border-bottom:7px solid #fff}}.match-summary{{position:relative;min-height:93px;padding:15px 62px 14px 14px;cursor:pointer}}.match-name{{display:flex;align-items:baseline;gap:5px;line-height:1.4;font-size:15px;flex-wrap:wrap}}.match-name b{{color:var(--red);font-weight:500}}.match-name em{{font-style:normal;color:#4f5d73}}.match-meta{{display:flex;align-items:center;gap:8px;margin-top:10px;color:#5b6a82;font-size:13px}}.live-tag{{background:var(--red);color:#fff;font-size:10px;font-weight:800;padding:4px 7px}}.score{{font-weight:700}}.more{{position:absolute;right:14px;top:50%;transform:translateY(-50%);width:40px;height:40px;background:var(--blue);color:#fff;display:flex;align-items:center;justify-content:center;font-size:12px;font-weight:800}}details[open]>.match-summary{{display:none}}
.detail{{background:#fae8e4}}.detail-head{{min-height:85px;background:#182235;color:#fff;display:grid;grid-template-columns:34px 1fr;align-items:center;padding:10px 12px}}.detail-head .close{{border:0;background:none;color:#fff;font-size:33px;text-align:left;padding:0;cursor:pointer}}.detail-head strong{{display:block;font-size:15px;line-height:1.35}}.detail-head small{{display:flex;align-items:center;gap:6px;flex-wrap:wrap;color:#c0c7d2;margin-top:6px;font-size:11px;text-transform:uppercase}}.markets{{padding:12px 7px 18px}}.market{{margin-bottom:16px}}.market h3{{margin:0 0 4px;text-align:center;font-size:14px;font-weight:500;color:#2c3e5b}}.special-market h3{{color:#9a6517}}.odds-grid{{display:grid;gap:2px;margin-bottom:2px}}.grid2{{grid-template-columns:repeat(2,1fr)}}.grid3{{grid-template-columns:repeat(3,1fr)}}.odd{{min-height:58px;background:#fff;display:flex;flex-direction:column;align-items:center;justify-content:center;padding:5px;text-align:center}}.odd span{{font-size:12px;color:#7b8595;line-height:1.2}}.odd b{{font-size:16px;font-weight:500;color:#253750;margin-top:3px}}.empty{{padding:28px 12px;text-align:center;color:#7e8998}}@media(max-width:390px){{.match-name{{font-size:14px}}.odd b{{font-size:15px}}}}
</style></head>
<body data-snapshot="{esc(generated)}"><input class="scope" type="radio" name="scope" id="scope-live" checked><input class="scope" type="radio" name="scope" id="scope-today"><input class="scope" type="radio" name="scope" id="scope-early"><div class="app"><header class="top"><div class="scope-tabs"><label for="scope-live">LIVE <b>{counts['live']}</b></label><label for="scope-today">HÔM NAY <b>{counts['today']}</b></label><label for="scope-early">SẮP TỚI <b>{counts['early']}</b></label></div><div class="statusbar"><span>Decimal</span><span>{esc(total_selections)} odds · {esc(updated)}</span></div></header><main><section class="panel live-panel">{sections['live']}</section><section class="panel today-panel">{sections['today']}</section><section class="panel early-panel">{sections['early']}</section></main></div>
<script>(function(){{const snapshot=Date.parse(document.body.dataset.snapshot||'');function tick(){{if(!Number.isFinite(snapshot))return;const age=Math.max(0,Math.floor((Date.now()-snapshot)/60000));document.querySelectorAll('[data-live-clock]').forEach(el=>{{const raw=el.dataset.raw||'LIVE',round=el.dataset.round||'',base=parseInt(el.dataset.baseMin||'',10);if(!Number.isFinite(base)||raw.includes('+')||round==='2')return;let m=base+age;if(round==='1')m=Math.min(m,45);if(round==='3')m=Math.min(m,90);el.textContent=(round==='1'?'1H ':round==='3'?'2H ':'')+m+"'";}})}}tick();setInterval(tick,15000);document.addEventListener('click',e=>{{const close=e.target.closest('[data-close-details]');if(close){{const d=close.closest('details');if(d)d.open=false;}}}});async function fresh(){{try{{const r=await fetch('data.json?t='+Date.now(),{{cache:'no-store'}});if(!r.ok)return;const d=await r.json();const ts=Date.parse(d.live_generated_at||d.generated_at||'');if(Number.isFinite(ts)&&Number.isFinite(snapshot)&&ts>snapshot+5000)location.replace(location.pathname+'?v='+ts);}}catch(e){{}}}}setTimeout(fresh,5000);setInterval(fresh,20000);}})();</script></body></html>'''
    out = Path(args.output); out.parent.mkdir(parents=True, exist_ok=True); out.write_text(doc, encoding="utf-8")
    print(f"REAL_LIVE_MATCHES={counts['live']}"); print(f"REAL_TODAY_MATCHES={counts['today']}"); print(f"REAL_EARLY_MATCHES={counts['early']}"); print(f"FULL_SELECTIONS={total_selections}")


if __name__ == "__main__":
    main()
