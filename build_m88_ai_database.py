#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path


def parse_dt(value):
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def age_seconds(now, value):
    dt = parse_dt(value)
    return None if dt is None else max(0, int((now - dt).total_seconds()))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--matches", required=True)
    ap.add_argument("--outrights", required=True)
    ap.add_argument("--output", required=True)
    ap.add_argument("--health")
    args = ap.parse_args()

    matches = json.loads(Path(args.matches).read_text(encoding="utf-8"))
    outs = json.loads(Path(args.outrights).read_text(encoding="utf-8"))
    now = datetime.now(timezone.utc)
    vn = timezone(timedelta(hours=7))

    match_ts = matches.get("generated_at") or matches.get("live_generated_at") or matches.get("captured_at")
    out_ts = outs.get("captured_at_utc")
    match_age = age_seconds(now, match_ts)
    out_age = age_seconds(now, out_ts)

    match_list = matches.get("matches") or []
    out_list = outs.get("markets") or []
    out_failures = outs.get("failures") or []
    source_text = " ".join(str(matches.get(k, "")) for k in ("source", "source_mode", "source_provider", "source_provenance"))
    exact_match = matches.get("exact_operator_odds") is True and "m88" in source_text.lower() and len(match_list) >= 50
    exact_out = outs.get("exact_operator_odds") is True and len(out_list) >= 40
    ratio = float((outs.get("coverage") or {}).get("capture_ratio") or 0)

    if not exact_match:
        raise SystemExit("Direct M88 match feed validation failed")
    if not exact_out:
        raise SystemExit("M88 Outright validation failed")

    scopes = {}
    leagues = set()
    selection_count = 0
    for m in match_list:
        scope = m.get("scope") or "unknown"
        scopes[scope] = scopes.get(scope, 0) + 1
        league = ((m.get("league") or {}).get("name") or "").strip()
        if league:
            leagues.add(league)
        for lines in (m.get("markets") or {}).values():
            for line in lines or []:
                selection_count += len(line.get("prices") or [])

    outright_index = []
    competitions = {}
    for m in out_list:
        item = {k: m.get(k) for k in ("market_id", "title", "competition_guess", "season_guess", "market_name_guess")}
        item["selection_count"] = len(m.get("selections") or [])
        outright_index.append(item)
        comp = (m.get("competition_guess") or "").strip()
        if comp:
            competitions[comp] = competitions.get(comp, 0) + 1

    outright_failure_index = []
    for failure in out_failures:
        outright_failure_index.append({
            "market_id": failure.get("market_id"),
            "title": failure.get("title"),
            "reason": failure.get("reason"),
        })

    match_status = "fresh" if match_age is not None and match_age <= 1200 else "stale"
    if ratio >= 0.90 and out_age is not None and out_age <= 2700:
        out_status = "fresh"
    elif ratio >= 0.75:
        out_status = "partial"
    else:
        out_status = "stale"

    db = {
        "schema_version": 3,
        "operator": "M88",
        "sport": "soccer",
        "odds_format": "decimal",
        "canonical": True,
        "generated_at_utc": now.isoformat().replace("+00:00", "Z"),
        "generated_at_vn": now.astimezone(vn).isoformat(),
        "status": "fresh" if match_status == "fresh" and out_status == "fresh" else "degraded",
        "freshness": {
            "match_odds": {
                "status": match_status,
                "captured_at": match_ts,
                "age_seconds": match_age,
                "target_refresh_seconds": 300,
                "stale_after_seconds": 1200,
                "source": "direct M88/MSports guest feed",
            },
            "outrights": {
                "status": out_status,
                "captured_at": out_ts,
                "age_seconds": out_age,
                "target_refresh_seconds": 900,
                "stale_after_seconds": 2700,
                "source": "native M88 -> SABA Outright board",
                "capture_ratio": ratio,
            },
        },
        "ai_usage": {
            "primary_instruction": "Use this file as the canonical current M88 odds database. Never substitute another bookmaker for an M88 price.",
            "article_refresh": "Identify the article competition/season and requested market. Search index.outright_titles then datasets.outrights.markets. For match odds search datasets.match_odds.matches by league, home, away and scope.",
            "absence_rule": "Before saying an Outright market is not listed, search both datasets.outrights.markets and datasets.outrights.failures (or index.outright_failures). A market appearing in failures was discovered on M88 but its price capture failed; report it as capture_failed/temporarily unavailable, never as not listed. Only state not currently listed when freshness.outrights.status is fresh, capture_ratio is at least 0.90, and no matching title/market appears in either markets or failures.",
            "failure_rule": "If a requested Outright market appears in datasets.outrights.failures, preserve its title/market_id/reason and say the current M88 board exposed the market but this snapshot did not capture its prices. Do not reuse old odds and do not substitute another bookmaker.",
            "freshness_rule": "Always inspect freshness before quoting odds. If the relevant dataset is stale, disclose that instead of presenting it as current.",
            "preserve_structure_rule": "When refreshing an existing article, preserve valid structure and wording unless explicitly asked to rewrite; replace outdated facts and odds only.",
        },
        "index": {
            "match_scopes": scopes,
            "match_leagues": sorted(leagues, key=str.casefold),
            "match_count": len(match_list),
            "match_selection_count": selection_count,
            "outright_market_count": len(out_list),
            "outright_failed_market_count": len(out_failures),
            "outright_competitions": dict(sorted(competitions.items(), key=lambda x: x[0].casefold())),
            "outright_titles": outright_index,
            "outright_failures": outright_failure_index,
        },
        "datasets": {"match_odds": matches, "outrights": outs},
    }

    Path(args.output).write_text(json.dumps(db, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    if args.health:
        health = {
            "schema_version": db["schema_version"],
            "operator": db["operator"],
            "generated_at_utc": db["generated_at_utc"],
            "generated_at_vn": db["generated_at_vn"],
            "status": db["status"],
            "freshness": db["freshness"],
            "index": {
                "match_scopes": scopes,
                "match_count": len(match_list),
                "match_selection_count": selection_count,
                "outright_market_count": len(out_list),
                "outright_failed_market_count": len(out_failures),
                "outright_competition_count": len(competitions),
            },
        }
        Path(args.health).write_text(json.dumps(health, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"status": db["status"], "freshness": db["freshness"], "match_count": len(match_list), "outright_market_count": len(out_list), "outright_failed_market_count": len(out_failures)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
