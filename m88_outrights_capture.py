#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from playwright.sync_api import sync_playwright

OUT = Path(os.environ.get("WORKSPACE", ".")) / "evidence"
OUT.mkdir(parents=True, exist_ok=True)


def pframe(page):
    frames = [f for f in page.frames if urlparse(f.url).netloc.startswith("i1x9gr.")]
    return frames[-1] if frames else None


def parse_post(value):
    if not value:
        return {}
    try:
        return json.loads(value)
    except Exception:
        pass
    try:
        return {k: v[-1] for k, v in parse_qs(value).items()}
    except Exception:
        return {}


def scroll_all(frame, pct):
    try:
        frame.evaluate(
            """pct => {
              const frac=Math.max(0,Math.min(1,pct/100));
              const root=document.scrollingElement||document.documentElement;
              if(root) root.scrollTop=(root.scrollHeight-root.clientHeight)*frac;
              for(const e of document.querySelectorAll('*')){
                const cs=getComputedStyle(e);
                if((cs.overflowY==='auto'||cs.overflowY==='scroll') && e.scrollHeight>e.clientHeight+180){
                  e.scrollTop=(e.scrollHeight-e.clientHeight)*frac;
                }
              }
            }""",
            pct,
        )
    except Exception:
        pass


def visible_title(frame, title):
    loc = frame.get_by_text(title, exact=True)
    for i in range(min(loc.count(), 30)):
        try:
            if loc.nth(i).is_visible():
                return loc.nth(i)
        except Exception:
            pass
    return None


def title_meta(title):
    clean = title.strip().lstrip("*").strip()
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
    return {
        "competition_guess": competition,
        "season_guess": season,
        "market_name_guess": market_name,
    }


def find_outright(frame):
    # Native selectors first; text is only a fallback.
    for selector in ('[data-market="outright"]', '[data-key="outright"]'):
        loc = frame.locator(selector)
        for i in range(min(loc.count(), 10)):
            try:
                if loc.nth(i).is_visible():
                    return loc.nth(i)
            except Exception:
                pass
    loc = frame.get_by_text(re.compile(r"^\s*Outright(?:\s+\d+)?\s*$", re.I))
    for i in range(min(loc.count(), 20)):
        try:
            if loc.nth(i).is_visible():
                return loc.nth(i)
        except Exception:
            pass
    return None


def click_outright(frame, element):
    try:
        element.evaluate("e=>e.scrollIntoView({block:'nearest',inline:'center'})")
    except Exception:
        pass
    for action in (
        lambda: element.click(timeout=5000),
        lambda: element.evaluate("e=>e.click()"),
        lambda: element.evaluate("e=>e.parentElement && e.parentElement.click()"),
    ):
        try:
            action()
            return True
        except Exception:
            pass
    return False


def main():
    showall = []
    getmarkets = []

    with sync_playwright() as p:
        browser = p.chromium.launch(
            channel="chrome",
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )
        ctx = browser.new_context(
            viewport={"width": 390, "height": 844},
            screen={"width": 390, "height": 844},
            device_scale_factor=3,
            is_mobile=True,
            has_touch=True,
            locale="en-US",
            timezone_id="Asia/Bangkok",
            user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 26_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/26.0 Mobile/15E148 Safari/604.1",
        )
        page = ctx.new_page()

        def onresp(resp):
            try:
                if "BFOdds/ShowAllOdds" in resp.url:
                    showall.append(json.loads(resp.text()))
                elif "BFOdds/GetMarket" in resp.url:
                    getmarkets.append(
                        {
                            "post": resp.request.post_data or "",
                            "body": resp.text(),
                            "url": resp.url,
                        }
                    )
            except Exception:
                pass

        page.on("response", onresp)
        page.goto(
            "https://www.m88.com/sports/Saba%20Sports?Language=en-US",
            wait_until="domcontentloaded",
            timeout=45000,
        )
        page.wait_for_timeout(22000)
        frame = pframe(page)
        if not frame:
            raise RuntimeError("No SABA frame")

        sports = frame.get_by_text("Sports", exact=True)
        for i in range(min(sports.count(), 20)):
            try:
                if sports.nth(i).is_visible():
                    sports.nth(i).click(timeout=5000)
                    break
            except Exception:
                pass
        page.wait_for_timeout(8000)
        frame = pframe(page) or frame

        outright = find_outright(frame)
        if outright is None:
            raise RuntimeError("Outright navigation not found")

        # Critical session boundary: do NOT mix ShowAllOdds from the normal Sports board
        # with the Outright board. This was the old 86-target / 58-real-market bug.
        showall.clear()
        getmarkets.clear()

        if not click_outright(frame, outright):
            raise RuntimeError("Unable to click Outright")
        page.wait_for_timeout(9000)
        frame = pframe(page) or frame
        route = frame.url

        if "/outright" not in route.lower():
            # One fallback navigation attempt after frame refresh.
            outright = find_outright(frame)
            if outright is not None:
                showall.clear()
                getmarkets.clear()
                click_outright(frame, outright)
                page.wait_for_timeout(7000)
                frame = pframe(page) or frame
                route = frame.url
        if "/outright" not in route.lower():
            raise RuntimeError(f"Outright route not reached: {route}")

        # Only payloads generated after the Outright navigation boundary are merged.
        for pct in range(0, 101, 10):
            scroll_all(frame, pct)
            page.wait_for_timeout(350)
        scroll_all(frame, 0)
        page.wait_for_timeout(1000)

        league_names = {}
        team_names = {}
        for obj in showall:
            data = (obj or {}).get("Data") or {}
            for k, v in (data.get("LeagueN") or {}).items():
                league_names[str(k)] = v
            for k, v in (data.get("TeamN") or {}).items():
                team_names[str(k)] = v

        targets = []
        for key, title in league_names.items():
            if str(key).isdigit() and isinstance(title, str) and title.strip():
                targets.append((int(key), title.strip()))
        targets.sort(key=lambda x: x[0])
        if len(targets) < 40:
            raise RuntimeError(
                f"Unsafe Outright discovery count={len(targets)} showall={len(showall)} route={route}"
            )

        markets = []
        failures = []
        unresolved = 0

        for idx, (market_id, title) in enumerate(targets, 1):
            el = visible_title(frame, title)
            if el is None:
                for pct in range(0, 101, 10):
                    scroll_all(frame, pct)
                    page.wait_for_timeout(120)
                    el = visible_title(frame, title)
                    if el is not None:
                        break
            if el is None:
                failures.append(
                    {"market_id": market_id, "title": title, "reason": "title_not_in_dom"}
                )
                continue

            try:
                el.scroll_into_view_if_needed(timeout=3500)
                page.wait_for_timeout(100)
            except Exception:
                pass

            captured = None
            for _ in range(2):
                before = len(getmarkets)
                try:
                    el.click(timeout=4500)
                except Exception:
                    try:
                        el.evaluate("e=>e.click()")
                    except Exception:
                        pass
                deadline = time.time() + 4.5
                while time.time() < deadline and captured is None:
                    for item in getmarkets[before:]:
                        post = parse_post(item["post"])
                        try:
                            got = int(post.get("Matchid", -1))
                        except Exception:
                            got = -1
                        if got == market_id:
                            captured = item
                            break
                    if captured is None:
                        page.wait_for_timeout(120)
                if captured is not None:
                    break

            if captured is None:
                failures.append(
                    {"market_id": market_id, "title": title, "reason": "no_native_getmarket"}
                )
                continue

            try:
                obj = json.loads(captured["body"])
            except Exception:
                failures.append(
                    {"market_id": market_id, "title": title, "reason": "non_json_getmarket"}
                )
                continue

            data = (obj or {}).get("Data") or {}
            for k, v in (data.get("TeamN") or {}).items():
                team_names[str(k)] = v
            odds = data.get("NewOdds") or []
            selections = []
            for odd in odds:
                team_id = odd.get("TeamId")
                price = odd.get("Price")
                if price is None:
                    continue
                name = team_names.get(str(team_id))
                if not name:
                    name = f"ID:{team_id}"
                    unresolved += 1
                selections.append(
                    {
                        "name": name,
                        "odds": price,
                        "team_id": team_id,
                        "market_id": odd.get("MarketId"),
                    }
                )
            if not selections:
                failures.append(
                    {"market_id": market_id, "title": title, "reason": "empty_selections"}
                )
                continue

            markets.append(
                {
                    "market_id": market_id,
                    "title": title,
                    "normalized_title": re.sub(r"\s+", " ", title).strip().casefold(),
                    **title_meta(title),
                    "selections": selections,
                }
            )
            if idx % 10 == 0:
                print(
                    f"CAPTURE_PROGRESS {idx}/{len(targets)} ok={len(markets)} fail={len(failures)}"
                )
            try:
                el.click(timeout=1200)
                page.wait_for_timeout(60)
            except Exception:
                pass

        now = datetime.now(timezone.utc)
        vn = timezone(timedelta(hours=7))
        coverage = len(markets) / len(targets) if targets else 0
        result = {
            "schema_version": 2,
            "operator": "M88",
            "provider": "SABA Sports",
            "exact_operator_odds": True,
            "sport": "soccer",
            "dataset": "all_outrights",
            "source_kind": "public_guest_board_native_capture",
            "source_url": "https://www.m88.com/sports/Saba%20Sports?Language=en-US",
            "provider_route": route,
            "captured_at_utc": now.isoformat().replace("+00:00", "Z"),
            "captured_at_vn": now.astimezone(vn).isoformat(),
            "refresh_target_minutes": 15,
            "status": "fresh" if coverage >= 0.90 else "partial",
            "coverage": {
                "showall_responses": len(showall),
                "discovered_markets": len(targets),
                "captured_markets": len(markets),
                "failed_markets": len(failures),
                "capture_ratio": round(coverage, 4),
                "unresolved_selection_names": unresolved,
            },
            "usage": {
                "instruction": "Search title/normalized_title/competition_guess. Use only these M88 odds. A discovered market in failures is capture_failed, not not_listed. Never substitute another bookmaker.",
                "not_listed_requires_capture_ratio": 0.90,
                "stale_after_minutes": 45,
            },
            "markets": markets,
            "failures": failures,
        }

        # Publish partial snapshots only above the safety floor; unified builder still
        # requires >=0.90 before treating absence as reliable.
        if coverage < 0.75:
            raise RuntimeError(
                f"Unsafe capture coverage {coverage:.1%}: {len(markets)}/{len(targets)}"
            )

        (OUT / "m88-outrights-all.json").write_text(
            json.dumps(result, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
        (OUT / "m88-outrights-health.json").write_text(
            json.dumps(
                {k: result[k] for k in ["captured_at_utc", "captured_at_vn", "status", "coverage"]},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        page.screenshot(path=str(OUT / "outrights-final.png"), full_page=False)
        print(json.dumps(result["coverage"], ensure_ascii=False))
        ctx.close()
        browser.close()


if __name__ == "__main__":
    main()
