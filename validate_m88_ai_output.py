#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path


def validate(root: Path) -> dict:
    db = json.loads((root / "ai-odds.json").read_text(encoding="utf-8"))
    if db.get("schema_version") != 3 or db.get("operator") != "M88":
        raise RuntimeError("AI database schema/operator mismatch")
    index = db.get("index") or {}
    match_count = int(index.get("match_count") or 0)
    outright_count = int(index.get("outright_market_count") or 0)
    required = int(index.get("outright_required_captured_count") or 25)
    ratio = float((((db.get("freshness") or {}).get("outrights") or {}).get("capture_ratio") or 0))
    if match_count < 50:
        raise RuntimeError(f"Unsafe match_count={match_count}")
    if outright_count < max(25, required):
        raise RuntimeError(f"Unsafe outright_market_count={outright_count} required={required}")
    if ratio < 0.90:
        raise RuntimeError(f"Unsafe outright capture_ratio={ratio}")

    nav = json.loads((root / "index.json").read_text(encoding="utf-8"))
    outright_index_path = (nav.get("outrights") or {}).get("index")
    if not outright_index_path:
        raise RuntimeError("Outright index path missing")
    outright_index = json.loads((root / outright_index_path).read_text(encoding="utf-8"))
    competitions = outright_index.get("competitions") or []
    if not competitions:
        raise RuntimeError("Outright competition index empty")

    evidence = None
    for competition in competitions:
        path = competition.get("path")
        if not path:
            continue
        shard_path = root / path
        if not shard_path.exists():
            continue
        shard = json.loads(shard_path.read_text(encoding="utf-8"))
        for market in shard.get("markets") or []:
            for selection in market.get("selections") or []:
                odds = selection.get("odds")
                if odds is not None:
                    evidence = {
                        "competition": competition.get("competition") or competition.get("slug"),
                        "market": market.get("title"),
                        "selection": selection.get("name"),
                        "odds": odds,
                    }
                    break
            if evidence:
                break
        if evidence:
            break
    if not evidence:
        raise RuntimeError("No priced Outright selection found in shards")
    return {
        "smoke": "PASS",
        "match_count": match_count,
        "outright_market_count": outright_count,
        "capture_ratio": ratio,
        "evidence": evidence,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("root")
    args = ap.parse_args()
    print(json.dumps(validate(Path(args.root)), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
