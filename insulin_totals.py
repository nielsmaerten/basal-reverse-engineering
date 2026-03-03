#!/usr/bin/env python3
"""
Compare insulin TDD (Total Daily Dose) from AAPS database and Nightscout MongoDB.

Three calculation methods:
  1. AAPS cached TDD  — read directly from totalDailyDoses table (pumpType=35)
  2. AAPS raw          — recalculated from boluses + temp basals + profile
  3. Nightscout        — calculated from treatments + profile in MongoDB

Usage:
  python insulin_totals.py --date 2026-02-24
  python insulin_totals.py --date 2026-02-24 --aaps-db ./androidaps.db
  python insulin_totals.py --date 2026-02-24 --ns-only
"""

import argparse
import hashlib
import json
import os
import sqlite3
import sys
from datetime import datetime, timedelta, time as dtime
from pathlib import Path
from zoneinfo import ZoneInfo

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None

try:
    from pymongo import MongoClient
except ImportError:
    MongoClient = None

try:
    import requests as _requests
except ImportError:
    _requests = None


STEP_MS = 5 * 60 * 1000  # 5 minutes in milliseconds
STEP_MIN = 5

# AAPS Bolus.Type enum
BOLUS_TYPE_PRIMING = 2

# AAPS PumpType enum — CACHE ordinal
PUMP_TYPE_CACHE = 35


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def align_down(ts_ms: int, step_ms: int = STEP_MS) -> int:
    """Align timestamp down to the nearest step boundary."""
    return ts_ms - (ts_ms % step_ms)


def day_bounds_ms(date_str: str, tz: ZoneInfo) -> tuple[int, int]:
    """Return (start_ms, end_ms) for midnight-to-midnight in the given tz."""
    d = datetime.strptime(date_str, "%Y-%m-%d")
    start = datetime.combine(d.date(), dtime.min, tzinfo=tz)
    end = start + timedelta(days=1)
    return int(start.timestamp() * 1000), int(end.timestamp() * 1000)


def day_bounds_utc_iso(date_str: str, tz: ZoneInfo) -> tuple[str, str]:
    """Return (start_iso, end_iso) as UTC ISO strings for the given local day."""
    d = datetime.strptime(date_str, "%Y-%m-%d")
    start = datetime.combine(d.date(), dtime.min, tzinfo=tz)
    end = start + timedelta(days=1)
    fmt = "%Y-%m-%dT%H:%M:%SZ"
    return start.astimezone(ZoneInfo("UTC")).strftime(fmt), end.astimezone(ZoneInfo("UTC")).strftime(fmt)


def format_pct(part: float, total: float) -> str:
    if total == 0:
        return "—"
    return f"{part / total * 100:.0f}%"


# ---------------------------------------------------------------------------
# AAPS: Profile basal rate lookup
# ---------------------------------------------------------------------------

def parse_basal_blocks(blocks_json: str) -> list[dict]:
    """Parse basalBlocks JSON from effectiveProfileSwitches.

    Expected format: list of objects with 'duration' (ms) and 'amount' (U/h).
    The AAPS Block data class stores duration in milliseconds and amount as a double.
    """
    blocks = json.loads(blocks_json)
    # Build a schedule with cumulative offset
    schedule = []
    offset_ms = 0
    for b in blocks:
        duration_ms = b["duration"]
        rate = b["amount"]
        schedule.append({
            "offset_ms": offset_ms,
            "duration_ms": duration_ms,
            "rate": rate,
        })
        offset_ms += duration_ms
    return schedule


def aaps_profile_rate_at(schedule: list[dict], ts_ms: int, day_start_ms: int) -> float:
    """Get the profile basal rate (U/h) at a given timestamp.

    schedule entries have offset_ms from start of day and rate in U/h.
    """
    offset = ts_ms - day_start_ms
    rate = schedule[0]["rate"]  # fallback to first block
    for block in schedule:
        if offset >= block["offset_ms"]:
            rate = block["rate"]
        else:
            break
    return rate


# ---------------------------------------------------------------------------
# AAPS: Cached TDD
# ---------------------------------------------------------------------------

def get_aaps_cached_tdd(db_path: str, date_str: str, tz: ZoneInfo) -> dict | None:
    """Read cached TDD from totalDailyDoses where pumpType = CACHE (35)."""
    start_ms, end_ms = day_bounds_ms(date_str, tz)

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    cur.execute("""
        SELECT timestamp, basalAmount, bolusAmount, totalAmount, carbs
        FROM totalDailyDoses
        WHERE pumpType = ?
          AND isValid = 1
          AND timestamp >= ? AND timestamp < ?
        ORDER BY timestamp DESC
        LIMIT 1
    """, (PUMP_TYPE_CACHE, start_ms, end_ms))

    row = cur.fetchone()
    conn.close()

    if not row:
        return None

    total = row["totalAmount"]
    if total == 0:
        total = row["basalAmount"] + row["bolusAmount"]

    return {
        "bolus": row["bolusAmount"],
        "basal": row["basalAmount"],
        "tdd": total,
        "carbs": row["carbs"],
    }


# ---------------------------------------------------------------------------
# AAPS: Raw recalculation
# ---------------------------------------------------------------------------

def get_aaps_raw_tdd(db_path: str, date_str: str, tz: ZoneInfo) -> dict | None:
    """Recalculate TDD from raw AAPS data (boluses + temp basals + profile)."""
    start_ms, end_ms = day_bounds_ms(date_str, tz)
    start_aligned = align_down(start_ms)
    end_aligned = align_down(end_ms)

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    # --- Bolus total (excluding PRIMING = type 2) ---
    bolus_total = 0.0
    for row in conn.execute("""
        SELECT amount FROM boluses
        WHERE isValid = 1
          AND type != ?
          AND timestamp >= ? AND timestamp < ?
    """, (BOLUS_TYPE_PRIMING, start_ms, end_ms)):
        bolus_total += row["amount"]

    # --- Carbs total ---
    carbs_total = 0.0
    for row in conn.execute("""
        SELECT amount FROM carbs
        WHERE isValid = 1
          AND timestamp >= ? AND timestamp < ?
    """, (start_ms, end_ms)):
        carbs_total += row["amount"]

    # --- Get effective profile (most recent before or at start of day) ---
    profile_row = conn.execute("""
        SELECT basalBlocks FROM effectiveProfileSwitches
        WHERE isValid = 1
          AND timestamp <= ?
        ORDER BY timestamp DESC
        LIMIT 1
    """, (start_ms,)).fetchone()

    if not profile_row:
        print("  [AAPS raw] No effectiveProfileSwitch found before day start", file=sys.stderr)
        conn.close()
        return None

    basal_schedule = parse_basal_blocks(profile_row["basalBlocks"])

    # --- Get temp basals overlapping the day ---
    # A temp basal overlaps if: timestamp < end_ms AND timestamp + duration > start_ms
    temp_basals = []
    for row in conn.execute("""
        SELECT timestamp, duration, isAbsolute, rate, type FROM temporaryBasals
        WHERE isValid = 1
          AND timestamp < ?
          AND (timestamp + duration) > ?
        ORDER BY timestamp
    """, (end_ms, start_ms)):
        temp_basals.append({
            "timestamp": row["timestamp"],
            "duration": row["duration"],
            "is_absolute": bool(row["isAbsolute"]),
            "rate": row["rate"],
            "type": row["type"],
        })

    # --- Get extended boluses overlapping the day ---
    # Insight pump does NOT fake temps via extended boluses, so we count them.
    extended_boluses = []
    for row in conn.execute("""
        SELECT timestamp, duration, amount FROM extendedBoluses
        WHERE isValid = 1
          AND timestamp < ?
          AND (timestamp + duration) > ?
        ORDER BY timestamp
    """, (end_ms, start_ms)):
        dur = row["duration"]
        amt = row["amount"]
        rate = amt * 3600000.0 / dur if dur > 0 else 0.0
        extended_boluses.append({
            "timestamp": row["timestamp"],
            "duration": dur,
            "rate": rate,
        })

    conn.close()

    # --- 5-minute integration loop ---
    basal_total = 0.0
    eb_total = 0.0

    for t in range(start_aligned, end_aligned, STEP_MS):
        profile_rate = aaps_profile_rate_at(basal_schedule, t, start_ms)

        # Check if a temp basal is active at time t
        effective_rate = profile_rate
        for tb in temp_basals:
            if tb["timestamp"] <= t < tb["timestamp"] + tb["duration"]:
                if tb["is_absolute"]:
                    effective_rate = tb["rate"]
                else:
                    # Percentage-based
                    effective_rate = profile_rate * tb["rate"] / 100.0
                break

        basal_total += effective_rate / 60.0 * STEP_MIN

        # Check if an extended bolus is active at time t
        for eb in extended_boluses:
            if eb["timestamp"] <= t < eb["timestamp"] + eb["duration"]:
                eb_total += eb["rate"] / 60.0 * STEP_MIN
                break

    # Extended boluses go into bolus total (per AAPS logic for non-fake-temp pumps)
    bolus_total += eb_total
    tdd = bolus_total + basal_total

    return {
        "bolus": bolus_total,
        "basal": basal_total,
        "tdd": tdd,
        "carbs": carbs_total,
    }


# ---------------------------------------------------------------------------
# Nightscout: Profile basal rate lookup
# ---------------------------------------------------------------------------

def parse_ns_basal_schedule(basal_entries: list[dict]) -> list[dict]:
    """Parse Nightscout profile basal schedule.

    Each entry: {time: "HH:MM", value: "0.6", timeAsSeconds: "0"}
    Returns sorted list of {seconds: int, rate: float}.
    """
    schedule = []
    for entry in basal_entries:
        seconds = int(entry.get("timeAsSeconds", 0))
        rate = float(entry["value"])
        schedule.append({"seconds": seconds, "rate": rate})
    schedule.sort(key=lambda x: x["seconds"])
    return schedule


def ns_profile_rate_at(schedule: list[dict], ts_ms: int, tz: ZoneInfo) -> float:
    """Get profile basal rate (U/h) at a given timestamp from NS schedule."""
    dt = datetime.fromtimestamp(ts_ms / 1000, tz=tz)
    seconds_since_midnight = dt.hour * 3600 + dt.minute * 60 + dt.second

    rate = schedule[0]["rate"]
    for entry in schedule:
        if seconds_since_midnight >= entry["seconds"]:
            rate = entry["rate"]
        else:
            break
    return rate


# ---------------------------------------------------------------------------
# Nightscout: TDD calculation
# ---------------------------------------------------------------------------

def get_ns_tdd(mongo_uri: str, date_str: str, tz: ZoneInfo) -> dict | None:
    """Calculate TDD from Nightscout MongoDB (treatments + profile)."""
    if MongoClient is None:
        print("  [NS] pymongo not installed — skipping Nightscout", file=sys.stderr)
        return None

    client = MongoClient(mongo_uri)
    db = client.get_default_database()

    start_ms, end_ms = day_bounds_ms(date_str, tz)

    # --- Get active profile ---
    profile_doc = db.profile.find_one(
        {"created_at": {"$exists": True}},
        sort=[("created_at", -1)],
    )
    if not profile_doc:
        # Try finding by mills or startDate
        profile_doc = db.profile.find_one(sort=[("startDate", -1)])

    if not profile_doc:
        print("  [NS] No profile found in MongoDB", file=sys.stderr)
        client.close()
        return None

    default_profile_name = profile_doc.get("defaultProfile", "Default")
    store = profile_doc.get("store", {})
    profile_data = store.get(default_profile_name)
    if not profile_data:
        # Try first available profile
        if store:
            default_profile_name = next(iter(store))
            profile_data = store[default_profile_name]
        else:
            print(f"  [NS] Profile '{default_profile_name}' not found in store", file=sys.stderr)
            client.close()
            return None

    basal_schedule = parse_ns_basal_schedule(profile_data.get("basal", []))
    if not basal_schedule:
        print("  [NS] No basal schedule in profile", file=sys.stderr)
        client.close()
        return None

    # --- Build profile switch timeline for the day ---
    # Each profile switch has a percentage that scales the base basal rate.
    # We need to know which percentage is active at each 5-min tick.
    # Find the most recent switch before the day, plus all switches during the day.
    profile_switches = []

    # Most recent switch before day start (sets initial state)
    prev_switch = db.treatments.find_one(
        {
            "eventType": "Profile Switch",
            "date": {"$lt": start_ms},
            "isValid": {"$ne": False},
        },
        sort=[("date", -1)],
    )
    if prev_switch:
        profile_switches.append({
            "timestamp": int(prev_switch["date"]),
            "percentage": prev_switch.get("percentage", 100),
        })

    # All switches during the day
    for doc in db.treatments.find({
        "eventType": "Profile Switch",
        "date": {"$gte": start_ms, "$lt": end_ms},
        "isValid": {"$ne": False},
    }).sort("date", 1):
        profile_switches.append({
            "timestamp": int(doc["date"]),
            "percentage": doc.get("percentage", 100),
        })

    # --- Bolus total (exclude isValid=false) ---
    bolus_event_types = ["Meal Bolus", "Correction Bolus", "Snack Bolus", "Combo Bolus"]
    bolus_total = 0.0
    bolus_cursor = db.treatments.find({
        "date": {"$gte": start_ms, "$lt": end_ms},
        "eventType": {"$in": bolus_event_types},
        "insulin": {"$exists": True, "$gt": 0},
        "isValid": {"$ne": False},
    })
    for doc in bolus_cursor:
        bolus_total += float(doc["insulin"])

    # --- Carbs total (exclude isValid=false) ---
    carbs_total = 0.0
    carbs_cursor = db.treatments.find({
        "date": {"$gte": start_ms, "$lt": end_ms},
        "carbs": {"$exists": True, "$gt": 0},
        "isValid": {"$ne": False},
    })
    for doc in carbs_cursor:
        carbs_total += float(doc["carbs"])

    # --- Temp basals overlapping the day ---
    # A temp basal overlaps if it starts before end and ends after start.
    # Max temp basal duration is typically 24h, so look back 24h before day start.
    # duration is in minutes in Nightscout, but check durationInMilliseconds too.
    temp_basals = []
    lookback_ms = start_ms - 24 * 60 * 60 * 1000
    tb_cursor = db.treatments.find({
        "eventType": "Temp Basal",
        "date": {"$gte": lookback_ms, "$lt": end_ms},
        "isValid": {"$ne": False},
    }).sort("date", 1)

    for doc in tb_cursor:
        ts = int(doc["date"])

        # Parse duration — prefer durationInMilliseconds if available
        dur_ms = None
        if "durationInMilliseconds" in doc and doc["durationInMilliseconds"]:
            dur_ms = int(doc["durationInMilliseconds"])
        elif "duration" in doc and doc["duration"]:
            dur_val = float(doc["duration"])
            # Heuristic: if duration > 100000, it's probably already in ms
            if dur_val > 100000:
                dur_ms = int(dur_val)
            else:
                dur_ms = int(dur_val * 60 * 1000)
        else:
            dur_ms = 0

        # Skip if it ends before the day starts
        if ts + dur_ms <= start_ms:
            continue

        # Parse rate
        absolute = None
        percent = None
        if "absolute" in doc and doc["absolute"] is not None:
            try:
                absolute = float(doc["absolute"])
            except (ValueError, TypeError):
                pass
        if "percent" in doc and doc["percent"] is not None:
            try:
                percent = float(doc["percent"])
            except (ValueError, TypeError):
                pass
        # Some entries use "rate" instead of "absolute"
        if absolute is None and "rate" in doc and doc["rate"] is not None:
            try:
                absolute = float(doc["rate"])
            except (ValueError, TypeError):
                pass

        temp_basals.append({
            "timestamp": ts,
            "duration": dur_ms,
            "absolute": absolute,
            "percent": percent,
        })

    # --- 5-minute integration loop ---
    start_aligned = align_down(start_ms)
    end_aligned = align_down(end_ms)
    basal_total = 0.0

    for t in range(start_aligned, end_aligned, STEP_MS):
        # Base profile rate from schedule
        base_rate = ns_profile_rate_at(basal_schedule, t, tz)

        # Apply profile switch percentage (e.g. 120% → multiply by 1.2)
        pct = 100
        for ps in profile_switches:
            if ps["timestamp"] <= t:
                pct = ps["percentage"]
            else:
                break
        profile_rate = base_rate * pct / 100.0

        # Find active temp basal at time t
        effective_rate = profile_rate
        for tb in temp_basals:
            if tb["timestamp"] <= t < tb["timestamp"] + tb["duration"]:
                if tb["absolute"] is not None:
                    effective_rate = tb["absolute"]
                elif tb["percent"] is not None:
                    # percent is delta from 100%: -100 = 0%, 0 = 100%, 20 = 120%
                    effective_rate = profile_rate * (100 + tb["percent"]) / 100.0
                break

        basal_total += effective_rate / 60.0 * STEP_MIN

    tdd = bolus_total + basal_total

    client.close()

    return {
        "bolus": bolus_total,
        "basal": basal_total,
        "tdd": tdd,
        "carbs": carbs_total,
    }


# ---------------------------------------------------------------------------
# Nightscout: TDD calculation via REST API (v1)
# ---------------------------------------------------------------------------

def _ns_api_get(api_url: str, api_secret: str, endpoint: str,
                params: dict | None = None) -> list | dict:
    """Make an authenticated GET request to the Nightscout v1 API."""
    hashed = hashlib.sha1(api_secret.encode()).hexdigest()
    headers = {"api-secret": hashed}
    url = f"{api_url.rstrip('/')}{endpoint}"
    resp = _requests.get(url, headers=headers, params=params, timeout=30)
    resp.raise_for_status()
    return resp.json()


def _ns_api_fetch_treatments(api_url: str, api_secret: str,
                             created_at_gte: str, created_at_lt: str,
                             event_type: str | None = None,
                             count: int = 1000) -> list[dict]:
    """Fetch treatments from NS API v1 filtered by created_at range."""
    params = {
        "count": count,
        "find[created_at][$gte]": created_at_gte,
        "find[created_at][$lt]": created_at_lt,
    }
    if event_type:
        params["find[eventType]"] = event_type
    return _ns_api_get(api_url, api_secret, "/api/v1/treatments.json", params)


def get_ns_tdd_api(api_url: str, api_secret: str,
                   date_str: str, tz: ZoneInfo) -> dict | None:
    """Calculate TDD from Nightscout REST API v1 (treatments + profile)."""
    if _requests is None:
        print("  [NS API] requests not installed — skipping", file=sys.stderr)
        return None

    start_ms, end_ms = day_bounds_ms(date_str, tz)
    start_utc, end_utc = day_bounds_utc_iso(date_str, tz)

    # Lookback 24h for temp basals that span midnight
    d = datetime.strptime(date_str, "%Y-%m-%d")
    lookback_start = datetime.combine(d.date(), dtime.min, tzinfo=tz) - timedelta(days=1)
    lookback_utc = lookback_start.astimezone(ZoneInfo("UTC")).strftime("%Y-%m-%dT%H:%M:%SZ")

    # --- Get profile ---
    profile_doc = _ns_api_get(api_url, api_secret, "/api/v1/profile/current")
    if not profile_doc:
        print("  [NS API] No profile found", file=sys.stderr)
        return None

    default_profile_name = profile_doc.get("defaultProfile", "Default")
    store = profile_doc.get("store", {})
    profile_data = store.get(default_profile_name)
    if not profile_data:
        if store:
            default_profile_name = next(iter(store))
            profile_data = store[default_profile_name]
        else:
            print(f"  [NS API] Profile '{default_profile_name}' not found", file=sys.stderr)
            return None

    basal_schedule = parse_ns_basal_schedule(profile_data.get("basal", []))
    if not basal_schedule:
        print("  [NS API] No basal schedule in profile", file=sys.stderr)
        return None

    # --- Fetch all treatments for the day ---
    day_treatments = _ns_api_fetch_treatments(
        api_url, api_secret, start_utc, end_utc)

    # --- Build profile switch timeline ---
    profile_switches = []

    # Most recent switch before day start
    prev_switches = _ns_api_fetch_treatments(
        api_url, api_secret,
        "2020-01-01T00:00:00Z", start_utc,
        event_type="Profile Switch", count=1)
    if prev_switches:
        ps = prev_switches[0]
        if ps.get("isValid") is not False:
            profile_switches.append({
                "timestamp": int(ps["date"]),
                "percentage": ps.get("percentage", 100),
            })

    # Profile switches during the day
    for t in day_treatments:
        if t.get("eventType") == "Profile Switch" and t.get("isValid") is not False:
            profile_switches.append({
                "timestamp": int(t["date"]),
                "percentage": t.get("percentage", 100),
            })
    profile_switches.sort(key=lambda x: x["timestamp"])

    # --- Bolus total ---
    bolus_event_types = {"Meal Bolus", "Correction Bolus", "Snack Bolus", "Combo Bolus"}
    bolus_total = 0.0
    for t in day_treatments:
        if (t.get("eventType") in bolus_event_types
                and t.get("isValid") is not False
                and t.get("insulin") and float(t["insulin"]) > 0):
            bolus_total += float(t["insulin"])

    # --- Carbs total ---
    carbs_total = 0.0
    for t in day_treatments:
        if (t.get("isValid") is not False
                and t.get("carbs") and float(t["carbs"]) > 0):
            carbs_total += float(t["carbs"])

    # --- Temp basals overlapping the day ---
    # Fetch from lookback period through end of day
    tb_treatments = _ns_api_fetch_treatments(
        api_url, api_secret, lookback_utc, end_utc,
        event_type="Temp Basal")

    temp_basals = []
    for doc in tb_treatments:
        if doc.get("isValid") is False:
            continue
        ts = int(doc["date"])

        # Parse duration
        dur_ms = None
        if doc.get("durationInMilliseconds"):
            dur_ms = int(doc["durationInMilliseconds"])
        elif doc.get("duration"):
            dur_val = float(doc["duration"])
            if dur_val > 100000:
                dur_ms = int(dur_val)
            else:
                dur_ms = int(dur_val * 60 * 1000)
        else:
            dur_ms = 0

        if ts + dur_ms <= start_ms:
            continue

        # Parse rate
        absolute = None
        percent = None
        if doc.get("absolute") is not None:
            try:
                absolute = float(doc["absolute"])
            except (ValueError, TypeError):
                pass
        if doc.get("percent") is not None:
            try:
                percent = float(doc["percent"])
            except (ValueError, TypeError):
                pass
        if absolute is None and doc.get("rate") is not None:
            try:
                absolute = float(doc["rate"])
            except (ValueError, TypeError):
                pass

        temp_basals.append({
            "timestamp": ts,
            "duration": dur_ms,
            "absolute": absolute,
            "percent": percent,
        })

    temp_basals.sort(key=lambda x: x["timestamp"])

    # --- 5-minute integration loop (identical to MongoDB version) ---
    start_aligned = align_down(start_ms)
    end_aligned = align_down(end_ms)
    basal_total = 0.0

    for t in range(start_aligned, end_aligned, STEP_MS):
        base_rate = ns_profile_rate_at(basal_schedule, t, tz)

        pct = 100
        for ps in profile_switches:
            if ps["timestamp"] <= t:
                pct = ps["percentage"]
            else:
                break
        profile_rate = base_rate * pct / 100.0

        effective_rate = profile_rate
        for tb in temp_basals:
            if tb["timestamp"] <= t < tb["timestamp"] + tb["duration"]:
                if tb["absolute"] is not None:
                    effective_rate = tb["absolute"]
                elif tb["percent"] is not None:
                    effective_rate = profile_rate * (100 + tb["percent"]) / 100.0
                break

        basal_total += effective_rate / 60.0 * STEP_MIN

    tdd = bolus_total + basal_total

    return {
        "bolus": bolus_total,
        "basal": basal_total,
        "tdd": tdd,
        "carbs": carbs_total,
    }


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

def print_comparison(date_str: str, results: dict[str, dict | None]):
    """Print a comparison table of TDD results."""
    sources = []
    for name in ["AAPS(cached)", "AAPS(raw)", "NS(mongo)", "NS(api)"]:
        if results.get(name) is not None:
            sources.append(name)

    if not sources:
        print(f"Date: {date_str}\n  No data available.")
        return

    # Column widths
    label_w = 12
    col_w = max(14, max(len(s) for s in sources) + 2)

    print(f"\nDate: {date_str}\n")

    # Header
    header = " " * label_w + "".join(s.rjust(col_w) for s in sources)
    print(header)
    print("-" * len(header))

    rows = [
        ("TDD", "tdd"),
        ("Bolus", "bolus"),
        ("Basal", "basal"),
        ("Basal %", None),
        ("Carbs", "carbs"),
    ]

    for label, key in rows:
        parts = [label.ljust(label_w)]
        for src in sources:
            r = results[src]
            if key is None:
                # Basal %
                val = format_pct(r["basal"], r["tdd"])
            elif key == "carbs":
                val = f"{r[key]:.0f}"
            else:
                val = f"{r[key]:.1f}"
            parts.append(val.rjust(col_w))
        print("".join(parts))

    # Show discrepancies
    if len(sources) > 1:
        ref_name = sources[0]
        ref = results[ref_name]
        print()
        for other_name in sources[1:]:
            other = results[other_name]
            diffs = []
            for key in ["tdd", "bolus", "basal", "carbs"]:
                d = abs(ref[key] - other[key])
                if d > 0.05:
                    diffs.append(f"{key}={ref[key]:.1f} vs {other[key]:.1f} (Δ{d:.1f})")
            if diffs:
                print(f"  Δ {ref_name} vs {other_name}:")
                for d in diffs:
                    print(f"    {d}")
            else:
                print(f"  {ref_name} vs {other_name}: match ✓")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    if load_dotenv is not None:
        env_path = Path(__file__).parent / ".env"
        if env_path.exists():
            load_dotenv(env_path)

    parser = argparse.ArgumentParser(
        description="Compare insulin TDD from AAPS database and Nightscout MongoDB"
    )
    parser.add_argument("--date", required=True, help="Date to query (YYYY-MM-DD)")
    parser.add_argument("--aaps-db", default=os.environ.get("AAPS_DB_PATH"),
                        help="Path to AAPS androidaps.db (default: $AAPS_DB_PATH)")
    parser.add_argument("--ns-mongo", default=os.environ.get("NS_MONGO_URI"),
                        help="MongoDB URI for Nightscout (default: $NS_MONGO_URI)")
    parser.add_argument("--ns-api", default=os.environ.get("NS_API_URL"),
                        help="Nightscout API URL (default: $NS_API_URL)")
    parser.add_argument("--ns-api-secret", default=os.environ.get("NS_API_SECRET"),
                        help="Nightscout API secret (default: $NS_API_SECRET)")
    parser.add_argument("--timezone", default=os.environ.get("TIMEZONE", "Europe/Amsterdam"),
                        help="Timezone for day boundaries (default: Europe/Amsterdam)")
    parser.add_argument("--ns-only", action="store_true",
                        help="Only calculate from Nightscout (mongo + api)")
    parser.add_argument("--aaps-only", action="store_true",
                        help="Only calculate from AAPS database")
    parser.add_argument("--debug", action="store_true",
                        help="Print debug info")

    args = parser.parse_args()
    tz = ZoneInfo(args.timezone)
    results: dict[str, dict | None] = {}

    # --- AAPS ---
    if not args.ns_only and args.aaps_db:
        if not Path(args.aaps_db).exists():
            print(f"  [AAPS] Database not found: {args.aaps_db}", file=sys.stderr)
        else:
            print(f"Reading AAPS database: {args.aaps_db}")

            cached = get_aaps_cached_tdd(args.aaps_db, args.date, tz)
            if cached:
                results["AAPS(cached)"] = cached
                if args.debug:
                    print(f"  Cached TDD: {cached}")
            else:
                print("  [AAPS] No cached TDD found for this date")

            raw = get_aaps_raw_tdd(args.aaps_db, args.date, tz)
            if raw:
                results["AAPS(raw)"] = raw
                if args.debug:
                    print(f"  Raw TDD: {raw}")
            else:
                print("  [AAPS] Could not recalculate raw TDD")
    elif not args.ns_only and not args.aaps_db:
        print("  [AAPS] No database path — set AAPS_DB_PATH or use --aaps-db")

    # --- Nightscout (MongoDB) ---
    if not args.aaps_only and args.ns_mongo:
        print("Querying Nightscout MongoDB...")
        ns = get_ns_tdd(args.ns_mongo, args.date, tz)
        if ns:
            results["NS(mongo)"] = ns
            if args.debug:
                print(f"  NS mongo TDD: {ns}")
        else:
            print("  [NS mongo] Could not calculate TDD")
    elif not args.aaps_only and not args.ns_mongo:
        print("  [NS mongo] No MongoDB URI — set NS_MONGO_URI or use --ns-mongo")

    # --- Nightscout (API) ---
    if not args.aaps_only and args.ns_api and args.ns_api_secret:
        print("Querying Nightscout API...")
        ns_api = get_ns_tdd_api(args.ns_api, args.ns_api_secret, args.date, tz)
        if ns_api:
            results["NS(api)"] = ns_api
            if args.debug:
                print(f"  NS API TDD: {ns_api}")
        else:
            print("  [NS API] Could not calculate TDD")
    elif not args.aaps_only and not args.ns_api:
        print("  [NS API] No API URL — set NS_API_URL or use --ns-api")

    # --- Output ---
    print_comparison(args.date, results)


if __name__ == "__main__":
    main()
