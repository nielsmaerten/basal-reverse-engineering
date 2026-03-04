"""Output formatters for therapy day data."""

import json
from dataclasses import asdict
from datetime import datetime
from zoneinfo import ZoneInfo

from nightscout.models import DayData


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _pct(part: float, total: float) -> str:
    if total == 0:
        return "-"
    return f"{part / total * 100:.0f}%"


def _cgm_avg(day: DayData) -> str:
    if not day.cgm:
        return "-"
    return f"{sum(e.sgv for e in day.cgm) / len(day.cgm):.0f}"


def _cgm_range(day: DayData) -> str:
    if not day.cgm:
        return "-"
    sgvs = [e.sgv for e in day.cgm]
    return f"{min(sgvs)}-{max(sgvs)}"


# ---------------------------------------------------------------------------
# Formatters
# ---------------------------------------------------------------------------

def format_summary(days: list[DayData], tz: ZoneInfo) -> str:
    """Compact text summary (one block per day)."""
    parts = []
    for day in days:
        lines = []
        lines.append(f"{'=' * 60}")
        lines.append(f"  {day.date}  ({day.timezone})")
        lines.append(f"{'=' * 60}")
        lines.append(f"  TDD:   {day.tdd:6.1f} U")
        if day.tdd:
            lines.append(f"  Bolus: {day.total_bolus:6.1f} U  ({_pct(day.total_bolus, day.tdd)})")
            lines.append(f"  Basal: {day.total_basal:6.1f} U  ({_pct(day.total_basal, day.tdd)})")
        else:
            lines.append(f"  Bolus:    0.0 U")
            lines.append(f"  Basal:    0.0 U")
        lines.append(f"  Carbs: {day.total_carbs:6.0f} g")
        lines.append(f"  CGM:   {len(day.cgm):4d} readings  avg {_cgm_avg(day)}  range {_cgm_range(day)} mg/dl")
        lines.append(f"  Boluses: {len(day.boluses)}  Basal slots: {len(day.basal)}  Events: {len(day.events)}")
        parts.append("\n".join(lines))
    return "\n\n".join(parts) + "\n"


def format_markdown(days: list[DayData], tz: ZoneInfo) -> str:
    """Markdown output: summary table + per-day detail sections."""
    lines = []

    # --- Summary table ---
    lines.append("## Summary\n")
    lines.append("| Date | TDD | Bolus | Basal | B% | Carbs | CGM avg | CGM range | Boluses | Events |")
    lines.append("|------|-----|-------|-------|----|-------|---------|-----------|---------|--------|")
    for day in days:
        lines.append(
            f"| {day.date} "
            f"| {day.tdd:.1f} "
            f"| {day.total_bolus:.1f} "
            f"| {day.total_basal:.1f} "
            f"| {_pct(day.total_basal, day.tdd)} "
            f"| {day.total_carbs:.0f} "
            f"| {_cgm_avg(day)} "
            f"| {_cgm_range(day)} "
            f"| {len(day.boluses)} "
            f"| {len(day.events)} |"
        )
    lines.append("")

    # --- Per-day detail sections ---
    for day in days:
        lines.append(f"## {day.date}\n")

        # Boluses
        if day.boluses:
            lines.append("### Boluses\n")
            lines.append("| Time | Amount | Type | Event |")
            lines.append("|------|--------|------|-------|")
            for b in day.boluses:
                t = datetime.fromtimestamp(b.timestamp_ms / 1000, tz=tz)
                lines.append(f"| {t:%H:%M} | {b.amount:.2f} U | {b.bolus_type} | {b.event_type} |")
            lines.append("")

        # Carbs
        if day.carbs:
            lines.append("### Carbs\n")
            lines.append("| Time | Amount |")
            lines.append("|------|--------|")
            for c in day.carbs:
                t = datetime.fromtimestamp(c.timestamp_ms / 1000, tz=tz)
                lines.append(f"| {t:%H:%M} | {c.amount:.0f} g |")
            lines.append("")

        # Temp targets
        if day.temp_targets:
            lines.append("### Temp Targets\n")
            lines.append("| Time | Duration | Low | High | Reason |")
            lines.append("|------|----------|-----|------|--------|")
            for tt in day.temp_targets:
                t = datetime.fromtimestamp(tt.timestamp_ms / 1000, tz=tz)
                dur_min = tt.duration_ms / 60000
                lines.append(f"| {t:%H:%M} | {dur_min:.0f}min | {tt.target_low:.0f} | {tt.target_high:.0f} | {tt.reason} |")
            lines.append("")

        # Profile switches
        if day.profile_switches:
            lines.append("### Profile Switches\n")
            lines.append("| Time | Percentage | Profile |")
            lines.append("|------|------------|---------|")
            for ps in day.profile_switches:
                t = datetime.fromtimestamp(ps.timestamp_ms / 1000, tz=tz)
                lines.append(f"| {t:%H:%M} | {ps.percentage}% | {ps.profile_name} |")
            lines.append("")

        # Events
        if day.events:
            lines.append("### Events\n")
            lines.append("| Time | Type | Notes |")
            lines.append("|------|------|-------|")
            for e in day.events:
                t = datetime.fromtimestamp(e.timestamp_ms / 1000, tz=tz)
                lines.append(f"| {t:%H:%M} | {e.event_type} | {e.notes} |")
            lines.append("")

    return "\n".join(lines)


def format_json(days: list[DayData], tz: ZoneInfo) -> str:
    """JSON array of day objects."""
    out = []
    for day in days:
        d = asdict(day)
        # Round floats for readability
        for key in ("tdd", "total_bolus", "total_basal", "total_carbs"):
            d[key] = round(d[key], 2)
        out.append(d)
    if len(out) == 1:
        return json.dumps(out[0], indent=2)
    return json.dumps(out, indent=2)


def format_debug(days: list[DayData], tz: ZoneInfo) -> str:
    """Verbose per-slot / per-bolus debug output."""
    parts = []
    for day in days:
        lines = []
        lines.append(f"{'=' * 60}")
        lines.append(f"  {day.date}  ({day.timezone})")
        lines.append(f"{'=' * 60}")
        lines.append(f"  TDD: {day.tdd:.1f}  Bolus: {day.total_bolus:.1f}  Basal: {day.total_basal:.1f}  Carbs: {day.total_carbs:.0f}")

        lines.append(f"\n--- Basal slots ({len(day.basal)}) ---")
        for s in day.basal:
            t = datetime.fromtimestamp(s.timestamp_ms / 1000, tz=tz)
            dur_min = s.duration_ms / 60000
            lines.append(f"  {t:%H:%M}  {dur_min:5.0f}min  {s.rate:.3f} U/h")

        lines.append(f"\n--- Boluses ({len(day.boluses)}) ---")
        for b in day.boluses:
            t = datetime.fromtimestamp(b.timestamp_ms / 1000, tz=tz)
            lines.append(f"  {t:%H:%M}  {b.amount:5.2f} U  {b.bolus_type:<6} ({b.event_type})")

        lines.append(f"\n--- Carbs ({len(day.carbs)}) ---")
        for c in day.carbs:
            t = datetime.fromtimestamp(c.timestamp_ms / 1000, tz=tz)
            lines.append(f"  {t:%H:%M}  {c.amount:.0f} g")

        if day.temp_targets:
            lines.append(f"\n--- Temp targets ({len(day.temp_targets)}) ---")
            for tt in day.temp_targets:
                t = datetime.fromtimestamp(tt.timestamp_ms / 1000, tz=tz)
                dur_min = tt.duration_ms / 60000
                lines.append(f"  {t:%H:%M}  {dur_min:.0f}min  {tt.target_low:.0f}-{tt.target_high:.0f}  {tt.reason}")

        if day.profile_switches:
            lines.append(f"\n--- Profile switches ({len(day.profile_switches)}) ---")
            for ps in day.profile_switches:
                t = datetime.fromtimestamp(ps.timestamp_ms / 1000, tz=tz)
                lines.append(f"  {t:%H:%M}  {ps.percentage}%  {ps.profile_name}")

        if day.events:
            lines.append(f"\n--- Events ({len(day.events)}) ---")
            for e in day.events:
                t = datetime.fromtimestamp(e.timestamp_ms / 1000, tz=tz)
                extra = f"  {e.notes}" if e.notes else ""
                lines.append(f"  {t:%H:%M}  {e.event_type}{extra}")

        if day.cgm:
            sgvs = [e.sgv for e in day.cgm]
            lines.append(f"\n--- CGM ({len(day.cgm)} readings) ---")
            lines.append(f"  range: {min(sgvs)}-{max(sgvs)} mg/dl, avg {sum(sgvs)/len(sgvs):.0f}")

        parts.append("\n".join(lines))
    return "\n\n".join(parts) + "\n"


FORMATTERS = {
    "summary": format_summary,
    "markdown": format_markdown,
    "json": format_json,
    "debug": format_debug,
}
