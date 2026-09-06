#!/usr/bin/env python3
from __future__ import annotations

import math

MIN_OUTRIGHT_DISCOVERY = 25
DEFAULT_OUTRIGHT_DISCOVERY = 40


def configured_min_discovery(value=None) -> int:
    raw = str(DEFAULT_OUTRIGHT_DISCOVERY if value is None else value).strip()
    try:
        parsed = int(raw)
    except ValueError as exc:
        raise ValueError(f"Invalid Outright discovery floor={raw!r}") from exc
    return max(MIN_OUTRIGHT_DISCOVERY, parsed)


def adaptive_discovery_floor(previous: int, ratio: float = 0.80) -> int:
    previous = int(previous)
    if previous < MIN_OUTRIGHT_DISCOVERY:
        raise ValueError(f"Unsafe last-good discovery baseline={previous}")
    if not (0.50 <= float(ratio) <= 1.0):
        raise ValueError(f"Unsafe adaptive discovery ratio={ratio}")
    return max(MIN_OUTRIGHT_DISCOVERY, math.ceil(previous * float(ratio)))


def outright_quality(outs: dict) -> dict:
    markets = outs.get("markets") or []
    failures = outs.get("failures") or []
    coverage = outs.get("coverage") or {}
    discovered = int(coverage.get("discovered_markets") or (len(markets) + len(failures)))
    captured = len(markets)
    ratio = float(coverage.get("capture_ratio") or (captured / discovered if discovered else 0.0))
    required_captured = max(MIN_OUTRIGHT_DISCOVERY, math.ceil(discovered * 0.90)) if discovered else MIN_OUTRIGHT_DISCOVERY
    ok = (
        outs.get("exact_operator_odds") is True
        and discovered >= MIN_OUTRIGHT_DISCOVERY
        and ratio >= 0.90
        and captured >= required_captured
    )
    return {
        "ok": ok,
        "discovered": discovered,
        "captured": captured,
        "failed": len(failures),
        "capture_ratio": ratio,
        "required_captured": required_captured,
    }
