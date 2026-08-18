#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import re
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SIMPLE_MARKETS = {
    "a": ("ft_asian_handicap", "asian_handicap", ("home", "away"), "a"),
    "b": ("ft_over_under", "over_under", ("over", "under"), "b"),
    "c": ("ft_odd_even", "odd_even", ("odd", "even"), "c"),
    "d": ("ft_1x2", "1x2", ("home", "draw", "away"), "d"),
    "e": ("fh_asian_handicap", "asian_handicap", ("home", "away"), "e"),
    "f": ("fh_over_under", "over_under", ("over", "under"), "f"),
    "g": ("fh_1x2", "1x2", ("home", "draw", "away"), "g"),
    "h": ("fh_odd_even", "odd_even", ("odd", "even"), "h"),
    "i": ("double_chance", "double_chance", ("1X", "12", "X2"), "i"),
    "j": ("first_last_goal", "first_last_goal", ("first_goal_home", "first_goal_away", "last_goal_home", "last_goal_away", "no_goal"), "j"),
    "k": ("ht_ft", "ht_ft", ("home_home", "home_draw", "home_away", "draw_home", "draw_draw", "draw_away", "away_home", "away_draw", "away_away"), "K"),
    "n": ("ft_total_goals", "total_goals", ("0-1", "2-3", "4-6", "7+"), "n"),
    "o": ("fh_total_goals", "total_goals", ("0-1", "2-3", "4+"), "o"),
}
SCOPE_FILES = ("live", "today", "early")
EXTRA_GROUPS = (4, 5, 6, 7, 8)
GROUP_MARKETS = {
    4: {"ft_total_goals", "fh_total_goals"},
    5: {"double_chance"},
    6: {"ht_ft"},
    7: {"first_last_goal"},
    8: {"correct_score"},
}
SCORE_RE = re.compile(r"^\d+-\d+$")


def text(v: Any) -> str:
    return "" if v is None else str(v).strip()


def number(v: Any):
    s = text(v)
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return s


def parse_price(raw: Any) -> dict[str, Any] | None:
    s = text(raw)
    if not s:
        return None
    parts = s.split("|")
    primary = text(parts[0]) if parts else ""
    if not primary:
        return None
    secondary = text(parts[1]) if len(parts) > 1 else ""
    choice_id = text(parts[2]) if len(parts) > 2 else ""
    return {"value": number(primary), "secondary": number(secondary), "choice_id": choice_id or None, "raw": s}


def league_from_row(row, previous):
    raw = text(row.get("event_name"))
    if not raw:
        return previous
    return {"id": text(row.get("no_event")) or previous.get("id"), "name": text(raw.split("|")[0]), "raw": raw}


def simple_market_line(row, suffix):
    market, family, selections, odds_suffix = SIMPLE_MARKETS[suffix]
    game_type = text(row.get(f"game_type_{suffix}"))
    if not game_type:
        return None
    prices = []
    for idx, selection in enumerate(selections, 1):
        p = parse_price(row.get(f"odds_{idx}_{odds_suffix}"))
        if p is not None:
            prices.append({"selection": selection, **p})
    if not prices:
        return None
    item = {
        "market": market,
        "family": family,
        "game_type": game_type,
        "sub_partai": text(row.get(f"sub_partai_{suffix}")) or None,
        "status_raw": text(row.get(f"status_{suffix}")) or None,
        "cash_out_raw": text(row.get(f"cash_out_{suffix}")) or None,
        "prices": prices,
    }
    if family in {"asian_handicap", "over_under"}:
        item["line"] = number(row.get(f"hdc_ori_{suffix}"))
        item["line_display_raw"] = text(row.get(f"hdc_display_{suffix}")) or None
    return item


def correct_score_line(row):
    raw = text(row.get("odds_1_l")) or text(row.get("game_type_l"))
    if not raw or "_" not in raw:
        return None
    prices = []
    for chunk in raw.split("_"):
        parts = chunk.split("|")
        if not parts or not SCORE_RE.match(text(parts[0])):
            continue
        score = text(parts[0])
        primary = text(parts[1]) if len(parts) > 1 else ""
        if not primary:
            continue
        secondary = text(parts[2]) if len(parts) > 2 else ""
        choice = text(parts[3]) if len(parts) > 3 else ""
        prices.append({"selection": score, "value": number(primary), "secondary": number(secondary), "choice_id": choice or None, "raw": "|".join(parts)})
    if not prices:
        return None
    return {"market": "correct_score", "family": "correct_score", "game_type": raw.split("_")[0], "sub_partai": None, "status_raw": None, "cash_out_raw": None, "prices": prices}


def line_key(line):
    return json.dumps({
        "market": line.get("market"),
        "game_type": line.get("game_type"),
        "sub_partai": line.get("sub_partai"),
        "line": line.get("line"),
        "prices": [(p.get("selection"), p.get("value"), p.get("choice_id")) for p in line.get("prices", [])],
    }, sort_keys=True, ensure_ascii=False)


def add_row_markets(match, row):
    seen = match.setdefault("_market_keys", set())
    for suffix in SIMPLE_MARKETS:
        line = simple_market_line(row, suffix)
        if line is None:
            continue
        k = line_key(line)
        if k not in seen:
            seen.add(k)
            match["markets"].setdefault(line["market"], []).append(line)
    cs = correct_score_line(row)
    if cs is not None:
        k = line_key(cs)
        if k not in seen:
            seen.add(k)
            match["markets"].setdefault("correct_score", []).append(cs)


def new_match(row, scope, sport_id, league):
    return {
        "scope": scope,
        "sport_id": sport_id,
        "league": dict(league),
        "match_id": text(row.get("no_partai")) or None,
        "match_date": text(row.get("match_date")) or None,
        "home": text(row.get("club_home")),
        "away": text(row.get("club_away")),
        "home_score": text(row.get("home_score")) or None,
        "away_score": text(row.get("away_score")) or None,
        "live_timer": text(row.get("live_timer")) or None,
        "event_round": text(row.get("event_round")) or None,
        "is_live": text(row.get("is_live")) or None,
        "is_neutral": text(row.get("is_neutral")) or None,
        "markets": {},
        "_market_keys": set(),
    }


def parse_payload(payload, scope):
    if payload.get("status") != 1:
        return []
    matches = []
    league = {"id": None, "name": "", "raw": ""}
    current = None
    for block in payload.get("data") or []:
        if not isinstance(block, dict):
            continue
        sport_id = text(block.get("spid")) or None
        for row in block.get("data") or []:
            if not isinstance(row, dict):
                continue
            league = league_from_row(row, league)
            home = text(row.get("club_home"))
            away = text(row.get("club_away"))
            if home or away:
                current = new_match(row, scope, sport_id, league)
                matches.append(current)
            elif current is None:
                continue
            elif league.get("name") and current.get("league", {}).get("name") != league.get("name"):
                continue
            add_row_markets(current, row)
    return matches


def match_key(m):
    if m.get("match_id"):
        return ("id", str(m["match_id"]))
    return ("names", m.get("scope"), m.get("home"), m.get("away"), (m.get("league") or {}).get("name"))


def merge_match(dst, src):
    for field in ("match_date", "home_score", "away_score", "live_timer", "event_round", "is_live", "is_neutral"):
        if src.get(field) not in (None, ""):
            dst[field] = src[field]
    seen = dst.setdefault("_market_keys", set())
    for name, lines in (src.get("markets") or {}).items():
        for line in lines:
            k = line_key(line)
            if k in seen:
                continue
            seen.add(k)
            dst["markets"].setdefault(name, []).append(line)


def load_json(path):
    if not path.exists():
        return None
    try:
        d = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        return d if isinstance(d, dict) else None
    except Exception:
        return None


def starts_as_special_name(special_name, base_name):
    s = text(special_name)
    b = text(base_name)
    return bool(b and (s == b or s.startswith(b + " ")))


def special_target(special, bases):
    candidates = []
    sh, sa = text(special.get("home")), text(special.get("away"))
    sl = (special.get("league") or {}).get("name") or ""
    for base in bases:
        bh, ba = text(base.get("home")), text(base.get("away"))
        if starts_as_special_name(sh, bh) and starts_as_special_name(sa, ba):
            league_bonus = 100000 if ((base.get("league") or {}).get("name") or "") == sl else 0
            candidates.append((league_bonus + len(bh) + len(ba), base))
    if not candidates:
        return None
    candidates.sort(key=lambda x: x[0], reverse=True)
    return candidates[0][1]


def special_label(special, base=None):
    if base is None:
        return "Special"
    sh, sa = text(special.get("home")), text(special.get("away"))
    bh, ba = text(base.get("home")), text(base.get("away"))
    ht = sh[len(bh):].strip(" -–—") if sh.startswith(bh) else ""
    at = sa[len(ba):].strip(" -–—") if sa.startswith(ba) else ""
    if ht and at and ht.casefold() == at.casefold():
        return ht
    if ht and not at:
        return ht
    if at and not ht:
        return at
    if ht and at:
        return f"{ht} / {at}"
    return "Special"


def merge_special(dst, special, label):
    seen = dst.setdefault("_market_keys", set())
    for base_name, lines in (special.get("markets") or {}).items():
        # Group 2 reuses the normal a-h schemas. Preserve source wording as a label
        # and namespace the market so it cannot overwrite/duplicate standard odds.
        special_name = f"special::{label}::{base_name}"
        for original in lines:
            line = dict(original)
            line["market"] = special_name
            line["special_label"] = label
            line["base_market"] = base_name
            k = line_key(line)
            if k in seen:
                continue
            seen.add(k)
            dst["markets"].setdefault(special_name, []).append(line)


def merge_group2_specials(merged, payload, scope, path, sources):
    if payload is None:
        sources.append({"scope": scope, "group": 2, "file": str(path), "present": False})
        return
    parsed = parse_payload(payload, scope)
    sources.append({"scope": scope, "group": 2, "file": str(path), "present": True, "api_status": payload.get("status"), "matches": len(parsed), "bytes": path.stat().st_size})
    bases = list(merged.values())
    unmatched = 0
    for special in parsed:
        target = special_target(special, bases)
        label = special_label(special, target)
        if target is not None:
            merge_special(target, special, label)
            continue
        # Never discard a Special row. If no parent can be identified safely,
        # preserve the source's synthetic event as its own event.
        unmatched += 1
        special["special_only"] = True
        special["league"] = dict(special.get("league") or {})
        special["league"]["name"] = "SPECIAL · " + ((special["league"].get("name") or "Khác"))
        old_markets = special.get("markets") or {}
        special["markets"] = {}
        special["_market_keys"] = set()
        merge_special(special, {"markets": old_markets}, "Special")
        key = ("special", scope, special.get("match_id"), special.get("home"), special.get("away"), unmatched)
        merged[key] = special


def load_scope(input_dir, scope):
    base_path = input_dir / f"{scope}.json"
    base_payload = load_json(base_path)
    sources = []
    merged = OrderedDict()
    if base_payload is not None:
        base_matches = parse_payload(base_payload, scope)
        sources.append({"scope": scope, "group": 1, "file": str(base_path), "present": True, "api_status": base_payload.get("status"), "matches": len(base_matches), "bytes": base_path.stat().st_size})
        for m in base_matches:
            merged[match_key(m)] = m
    else:
        sources.append({"scope": scope, "group": 1, "file": str(base_path), "present": False})

    # Group 2 is M88 "Special": interval, corner and other derivative rows.
    # It often changes club_home/club_away to "Team + Special label", so it needs
    # name-prefix parent matching rather than match-id merging.
    special_path = input_dir / f"{scope}_g2.json"
    merge_group2_specials(merged, load_json(special_path), scope, special_path, sources)

    # Other display groups are supplemental only: do not create events outside the
    # canonical group-1 scope, and only keep each group's unique market family.
    for group in EXTRA_GROUPS:
        path = input_dir / f"{scope}_g{group}.json"
        payload = load_json(path)
        if payload is None:
            sources.append({"scope": scope, "group": group, "file": str(path), "present": False})
            continue
        parsed = parse_payload(payload, scope)
        allowed = GROUP_MARKETS[group]
        sources.append({"scope": scope, "group": group, "file": str(path), "present": True, "api_status": payload.get("status"), "matches": len(parsed), "bytes": path.stat().st_size})
        for m in parsed:
            key = match_key(m)
            if key not in merged:
                continue
            m["markets"] = {n: ls for n, ls in (m.get("markets") or {}).items() if n in allowed}
            if m["markets"]:
                merge_match(merged[key], m)

    out = list(merged.values())
    for m in out:
        m.pop("_market_keys", None)
    return out, sources


def write_csv(path, matches, odds_format):
    fields = ["scope", "league", "match_id", "match_date", "live_timer", "event_round", "home", "away", "home_score", "away_score", "market", "family", "game_type", "sub_partai", "line", "selection", "price", "secondary", "choice_id", "raw_price", "odds_format"]
    with path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        for m in matches:
            for market, lines in (m.get("markets") or {}).items():
                for line in lines:
                    for p in line.get("prices") or []:
                        w.writerow({
                            "scope": m.get("scope"), "league": (m.get("league") or {}).get("name"),
                            "match_id": m.get("match_id"), "match_date": m.get("match_date"),
                            "live_timer": m.get("live_timer"), "event_round": m.get("event_round"),
                            "home": m.get("home"), "away": m.get("away"),
                            "home_score": m.get("home_score"), "away_score": m.get("away_score"),
                            "market": market, "family": line.get("family"), "game_type": line.get("game_type"),
                            "sub_partai": line.get("sub_partai"), "line": line.get("line"),
                            "selection": p.get("selection"), "price": p.get("value"), "secondary": p.get("secondary"),
                            "choice_id": p.get("choice_id"), "raw_price": p.get("raw"), "odds_format": odds_format,
                        })


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input-dir", default="evidence")
    ap.add_argument("--output-json", default="evidence/m88_full_odds.json")
    ap.add_argument("--output-csv", default="evidence/m88_full_odds.csv")
    ap.add_argument("--odds-format", default="decimal")
    args = ap.parse_args()
    input_dir = Path(args.input_dir)
    all_matches, sources = [], []
    for scope in SCOPE_FILES:
        ms, ss = load_scope(input_dir, scope)
        all_matches.extend(ms)
        sources.extend(ss)
    by_scope = {s: sum(1 for m in all_matches if m.get("scope") == s) for s in SCOPE_FILES}
    market_matches, selection_counts = {}, {}
    for m in all_matches:
        for name, lines in (m.get("markets") or {}).items():
            count = sum(len(x.get("prices") or []) for x in lines)
            if name.startswith("special::"):
                bucket = "special"
            else:
                bucket = name
            market_matches[bucket] = market_matches.get(bucket, 0) + 1
            selection_counts[bucket] = selection_counts.get(bucket, 0) + count
    output = {
        "source": "M88 / MSports public guest API soccer groups 1-8",
        "odds_format": args.odds_format,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "counts": {
            "matches": len(all_matches), "by_scope": by_scope,
            "market_matches": market_matches, "selections": selection_counts,
            "total_selections": sum(selection_counts.values()),
        },
        "sources": sources, "matches": all_matches,
    }
    jp, cp = Path(args.output_json), Path(args.output_csv)
    jp.parent.mkdir(parents=True, exist_ok=True)
    cp.parent.mkdir(parents=True, exist_ok=True)
    jp.write_text(json.dumps(output, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    write_csv(cp, all_matches, args.odds_format)
    print(json.dumps(output["counts"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
