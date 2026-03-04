"""Nightscout REST API client and therapy day data fetching."""

import hashlib
from datetime import datetime, timedelta, time as dtime
from zoneinfo import ZoneInfo

import requests

from nightscout.models import (
    CGMEntry, BasalSlot, Bolus, Carbs, TempTarget, ProfileSwitch, Event, DayData,
)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

STEP_MS = 5 * 60 * 1000  # 5 minutes in milliseconds
STEP_MIN = 5

BOLUS_EVENT_TYPES = {"Meal Bolus", "Correction Bolus", "Snack Bolus", "Combo Bolus"}
EVENT_TYPES = {"Site Change", "Sensor Change", "Sensor Start",
               "OpenAPS Offline", "Note", "Announcement"}


# ---------------------------------------------------------------------------
# Time helpers
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


# ---------------------------------------------------------------------------
# API client
# ---------------------------------------------------------------------------

def _ns_api_get(api_url: str, api_secret: str, endpoint: str,
                params: dict | None = None) -> list | dict:
    """Make an authenticated GET request to the Nightscout v1 API."""
    hashed = hashlib.sha1(api_secret.encode()).hexdigest()
    headers = {"api-secret": hashed}
    url = f"{api_url.rstrip('/')}{endpoint}"
    resp = requests.get(url, headers=headers, params=params, timeout=30)
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


# ---------------------------------------------------------------------------
# Profile helpers
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
# Basal timeline resolution
# ---------------------------------------------------------------------------

def _resolve_basal_timeline(
    start_ms: int,
    end_ms: int,
    basal_schedule: list[dict],
    profile_switches: list[dict],
    temp_basals: list[dict],
    tz: ZoneInfo,
) -> list[BasalSlot]:
    """Run the 5-min integration loop and emit merged BasalSlots."""
    start_aligned = align_down(start_ms)
    end_aligned = align_down(end_ms)

    # Build per-tick rates
    ticks: list[tuple[int, float]] = []
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

        ticks.append((t, round(effective_rate, 4)))

    # Merge consecutive ticks with same rate
    if not ticks:
        return []

    slots: list[BasalSlot] = []
    run_start, run_rate = ticks[0]
    run_end = run_start + STEP_MS

    for t, rate in ticks[1:]:
        if rate == run_rate:
            run_end = t + STEP_MS
        else:
            slots.append(BasalSlot(
                timestamp_ms=run_start,
                duration_ms=run_end - run_start,
                rate=run_rate,
            ))
            run_start = t
            run_rate = rate
            run_end = t + STEP_MS

    slots.append(BasalSlot(
        timestamp_ms=run_start,
        duration_ms=run_end - run_start,
        rate=run_rate,
    ))

    return slots


# ---------------------------------------------------------------------------
# Treatment parsing
# ---------------------------------------------------------------------------

def _parse_treatments(treatments: list[dict], start_ms: int, end_ms: int) -> dict:
    """Classify day treatments into boluses, carbs, temp_targets, profile_switches, events."""
    boluses: list[Bolus] = []
    carbs_list: list[Carbs] = []
    temp_targets: list[TempTarget] = []
    profile_switches: list[ProfileSwitch] = []
    events: list[Event] = []

    for t in treatments:
        if t.get("isValid") is False:
            continue

        ts = int(t.get("date", 0))
        et = t.get("eventType", "")

        # Boluses
        if et in BOLUS_EVENT_TYPES:
            insulin = t.get("insulin")
            if insulin and float(insulin) > 0:
                if et == "Correction Bolus":
                    btype = "SMB"
                else:
                    btype = "NORMAL"
                boluses.append(Bolus(
                    timestamp_ms=ts,
                    amount=float(insulin),
                    bolus_type=btype,
                    event_type=et,
                ))

        # Carbs (any treatment with carbs > 0)
        carbs_val = t.get("carbs")
        if carbs_val and float(carbs_val) > 0:
            carbs_list.append(Carbs(timestamp_ms=ts, amount=float(carbs_val)))

        # Temp targets
        if et == "Temporary Target":
            dur = t.get("durationInMilliseconds")
            if dur is None:
                dur_min = t.get("duration", 0)
                dur = int(float(dur_min) * 60 * 1000) if dur_min else 0
            else:
                dur = int(dur)
            temp_targets.append(TempTarget(
                timestamp_ms=ts,
                duration_ms=dur,
                target_low=float(t.get("targetBottom", t.get("targetLow", 0))),
                target_high=float(t.get("targetTop", t.get("targetHigh", 0))),
                reason=t.get("reason", ""),
            ))

        # Profile switches
        if et == "Profile Switch":
            profile_switches.append(ProfileSwitch(
                timestamp_ms=ts,
                percentage=t.get("percentage", 100),
                profile_name=t.get("profile", ""),
            ))

        # Care portal events
        if et in EVENT_TYPES:
            dur = t.get("durationInMilliseconds")
            if dur is None:
                dur_min = t.get("duration", 0)
                dur = int(float(dur_min) * 60 * 1000) if dur_min else 0
            else:
                dur = int(dur)
            events.append(Event(
                timestamp_ms=ts,
                event_type=et,
                duration_ms=dur,
                notes=t.get("notes", ""),
            ))

    boluses.sort(key=lambda x: x.timestamp_ms)
    carbs_list.sort(key=lambda x: x.timestamp_ms)
    temp_targets.sort(key=lambda x: x.timestamp_ms)
    profile_switches.sort(key=lambda x: x.timestamp_ms)
    events.sort(key=lambda x: x.timestamp_ms)

    return {
        "boluses": boluses,
        "carbs": carbs_list,
        "temp_targets": temp_targets,
        "profile_switches": profile_switches,
        "events": events,
    }


def _parse_temp_basals(tb_treatments: list[dict], start_ms: int) -> list[dict]:
    """Parse temp basal treatments into the dict format used by the integration loop."""
    temp_basals = []
    for doc in tb_treatments:
        if doc.get("isValid") is False:
            continue
        ts = int(doc["date"])

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
    return temp_basals


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def get_day(date_str: str, api_url: str, api_secret: str,
            tz: ZoneInfo) -> DayData:
    """Fetch all therapy data for a single day from the Nightscout REST API."""
    start_ms, end_ms = day_bounds_ms(date_str, tz)
    start_utc, end_utc = day_bounds_utc_iso(date_str, tz)

    # Lookback for temp basals spanning midnight
    d = datetime.strptime(date_str, "%Y-%m-%d")
    lookback_start = datetime.combine(d.date(), dtime.min, tzinfo=tz) - timedelta(days=1)
    lookback_utc = lookback_start.astimezone(ZoneInfo("UTC")).strftime("%Y-%m-%dT%H:%M:%SZ")

    # Date strings for CGM dateString filter
    day_date = d.strftime("%Y-%m-%d")
    next_date = (d + timedelta(days=1)).strftime("%Y-%m-%d")

    # --- 1. Profile ---
    profile_doc = _ns_api_get(api_url, api_secret, "/api/v1/profile/current")
    default_profile_name = profile_doc.get("defaultProfile", "Default")
    store = profile_doc.get("store", {})
    profile_data = store.get(default_profile_name)
    if not profile_data and store:
        default_profile_name = next(iter(store))
        profile_data = store[default_profile_name]
    if not profile_data:
        raise ValueError(f"No profile data found (tried '{default_profile_name}')")

    basal_schedule = parse_ns_basal_schedule(profile_data.get("basal", []))
    if not basal_schedule:
        raise ValueError("No basal schedule in profile")

    # --- 2. Initial profile switch state ---
    ps_switches_raw = []
    prev_switches = _ns_api_fetch_treatments(
        api_url, api_secret,
        "2020-01-01T00:00:00Z", start_utc,
        event_type="Profile Switch", count=1)
    if prev_switches:
        ps = prev_switches[0]
        if ps.get("isValid") is not False:
            ps_switches_raw.append({
                "timestamp": int(ps["date"]),
                "percentage": ps.get("percentage", 100),
            })

    # --- 3. All treatments for the day ---
    day_treatments = _ns_api_fetch_treatments(api_url, api_secret, start_utc, end_utc)

    # Add intra-day profile switches to the raw list
    for t in day_treatments:
        if t.get("eventType") == "Profile Switch" and t.get("isValid") is not False:
            ps_switches_raw.append({
                "timestamp": int(t["date"]),
                "percentage": t.get("percentage", 100),
            })
    ps_switches_raw.sort(key=lambda x: x["timestamp"])

    # --- 4. Temp basals (lookback + day) ---
    tb_treatments = _ns_api_fetch_treatments(
        api_url, api_secret, lookback_utc, end_utc,
        event_type="Temp Basal")
    temp_basals = _parse_temp_basals(tb_treatments, start_ms)

    # --- 5. CGM entries ---
    cgm_raw = _ns_api_get(api_url, api_secret, "/api/v1/entries.json", {
        "count": 1000,
        "find[dateString][$gte]": day_date,
        "find[dateString][$lt]": next_date,
    })

    cgm_entries = []
    for e in cgm_raw:
        if e.get("type") != "sgv":
            continue
        cgm_entries.append(CGMEntry(
            timestamp_ms=int(e.get("date", 0)),
            sgv=int(e.get("sgv", 0)),
            direction=e.get("direction", ""),
            delta=float(e.get("delta", 0)),
        ))
    cgm_entries.sort(key=lambda x: x.timestamp_ms)

    # --- Parse treatments ---
    parsed = _parse_treatments(day_treatments, start_ms, end_ms)

    # --- Resolve basal timeline ---
    basal_slots = _resolve_basal_timeline(
        start_ms, end_ms, basal_schedule, ps_switches_raw, temp_basals, tz)

    # --- Compute TDD summaries ---
    total_bolus = sum(b.amount for b in parsed["boluses"])
    total_basal = sum(s.rate / 60.0 * (s.duration_ms / 60000.0) for s in basal_slots)
    total_carbs = sum(c.amount for c in parsed["carbs"])
    tdd = total_bolus + total_basal

    return DayData(
        date=date_str,
        timezone=str(tz),
        cgm=cgm_entries,
        basal=basal_slots,
        boluses=parsed["boluses"],
        carbs=parsed["carbs"],
        temp_targets=parsed["temp_targets"],
        profile_switches=parsed["profile_switches"],
        events=parsed["events"],
        tdd=tdd,
        total_bolus=total_bolus,
        total_basal=total_basal,
        total_carbs=total_carbs,
    )
