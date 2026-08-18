#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import unicodedata
from pathlib import Path

SCOPES = ("live", "today", "early")


def dump(path: Path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


def title_meta(title: str):
    clean = str(title or "").strip().lstrip("*").strip()
    season = None
    m = re.search(r"\b(20\d{2}/20\d{2})\b", clean)
    if m:
        season = m.group(1)
    competition = None
    market_name = None
    if " - " in clean:
        left, right = clean.rsplit(" - ", 1)
        market_name = right.strip()
        competition = re.sub(r"\s*20\d{2}/20\d{2}\s*", " ", left).strip()
    return competition, season, market_name


def base_slug(value: str):
    low = re.sub(r"\s+", " ", str(value or "").strip()).casefold()
    if "english premier league" in low:
        return "epl"
    if re.search(r"\bitaly\s+serie\s+a\b", low) or low == "serie a":
        return "serie-a"
    normalized = unicodedata.normalize("NFKD", low)
    ascii_text = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_text).strip("-")
    return slug or "competition"


def unique_slugs(names):
    used = {}
    out = {}
    for name in sorted(names, key=str.casefold):
        root = base_slug(name)
        slug = root
        n = 2
        while slug in used and used[slug] != name:
            slug = f"{root}-{n}"
            n += 1
        used[slug] = name
        out[name] = slug
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True)
    ap.add_argument("--output-dir", required=True)
    args = ap.parse_args()

    db = json.loads(Path(args.input).read_text(encoding="utf-8"))
    if db.get("operator") != "M88" or db.get("canonical") is not True:
        raise SystemExit("Input is not canonical M88 database")

    root = Path(args.output_dir)
    root.mkdir(parents=True, exist_ok=True)
    master = root / "ai-odds.json"
    if Path(args.input).resolve() != master.resolve():
        master.write_text(Path(args.input).read_text(encoding="utf-8"), encoding="utf-8")

    matches_ds = ((db.get("datasets") or {}).get("match_odds") or {})
    matches = matches_ds.get("matches") or []
    match_freshness = (db.get("freshness") or {}).get("match_odds") or {}
    match_index = {
        "schema_version": 1,
        "operator": "M88",
        "generated_at_utc": db.get("generated_at_utc"),
        "source_captured_at": match_freshness.get("captured_at"),
        "freshness": match_freshness,
        "scopes": {},
    }
    for scope in SCOPES:
        rows = [m for m in matches if (m.get("scope") or "") == scope]
        path = f"matches/{scope}.json"
        payload = {
            "schema_version": 1,
            "operator": "M88",
            "sport": db.get("sport"),
            "odds_format": db.get("odds_format"),
            "scope": scope,
            "generated_at_utc": db.get("generated_at_utc"),
            "source_captured_at": match_freshness.get("captured_at"),
            "freshness": match_freshness,
            "count": len(rows),
            "matches": rows,
        }
        dump(root / path, payload)
        match_index["scopes"][scope] = {"path": path, "count": len(rows)}
    dump(root / "matches/index.json", match_index)

    outright_ds = ((db.get("datasets") or {}).get("outrights") or {})
    markets = outright_ds.get("markets") or []
    failures = outright_ds.get("failures") or []
    out_freshness = (db.get("freshness") or {}).get("outrights") or {}

    competition_markets = {}
    for market in markets:
        comp = (market.get("competition_guess") or "").strip()
        if not comp:
            comp, _, _ = title_meta(market.get("title") or "")
        comp = (comp or "Unclassified").strip()
        competition_markets.setdefault(comp, []).append(market)

    competition_failures = {}
    for failure in failures:
        comp, season, market_name = title_meta(failure.get("title") or "")
        comp = (comp or "Unclassified").strip()
        item = dict(failure)
        item.setdefault("competition_guess", comp if comp != "Unclassified" else None)
        item.setdefault("season_guess", season)
        item.setdefault("market_name_guess", market_name)
        competition_failures.setdefault(comp, []).append(item)

    names = set(competition_markets) | set(competition_failures)
    slugs = unique_slugs(names)
    competition_entries = []
    for comp in sorted(names, key=str.casefold):
        slug = slugs[comp]
        ms = competition_markets.get(comp, [])
        fs = competition_failures.get(comp, [])
        seasons = sorted({str(m.get("season_guess")) for m in ms if m.get("season_guess")}, reverse=True)
        path = f"outrights/{slug}.json"
        payload = {
            "schema_version": 1,
            "operator": "M88",
            "provider": outright_ds.get("provider"),
            "exact_operator_odds": outright_ds.get("exact_operator_odds"),
            "competition": comp,
            "slug": slug,
            "generated_at_utc": db.get("generated_at_utc"),
            "source_captured_at": out_freshness.get("captured_at"),
            "freshness": out_freshness,
            "seasons": seasons,
            "market_count": len(ms),
            "failed_market_count": len(fs),
            "markets": ms,
            "failures": fs,
        }
        dump(root / path, payload)
        competition_entries.append({
            "competition": comp,
            "slug": slug,
            "path": path,
            "seasons": seasons,
            "market_count": len(ms),
            "failed_market_count": len(fs),
        })

    outright_index = {
        "schema_version": 1,
        "operator": "M88",
        "provider": outright_ds.get("provider"),
        "exact_operator_odds": outright_ds.get("exact_operator_odds"),
        "generated_at_utc": db.get("generated_at_utc"),
        "source_captured_at": out_freshness.get("captured_at"),
        "freshness": out_freshness,
        "competition_count": len(competition_entries),
        "market_count": len(markets),
        "failed_market_count": len(failures),
        "competitions": competition_entries,
    }
    dump(root / "outrights/index.json", outright_index)

    index = {
        "schema_version": 1,
        "operator": "M88",
        "sport": db.get("sport"),
        "odds_format": db.get("odds_format"),
        "canonical_master": "ai-odds.json",
        "health": "health.json",
        "generated_at_utc": db.get("generated_at_utc"),
        "status": db.get("status"),
        "freshness": db.get("freshness"),
        "matches": {"index": "matches/index.json", "scopes": match_index["scopes"]},
        "outrights": {"index": "outrights/index.json", "competitions": competition_entries},
    }
    dump(root / "index.json", index)
    print(json.dumps({
        "match_scopes": {s: match_index["scopes"][s]["count"] for s in SCOPES},
        "outright_competitions": len(competition_entries),
        "outright_markets": len(markets),
        "outright_failures": len(failures),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
