---
name: nightscout
description: Fetch, analyze, and visualize Nightscout diabetes therapy data (CGM, insulin, carbs)
---

# Nightscout Data Analysis Skill

You help the user explore their Nightscout diabetes therapy data — fetching it via the
CLI, generating matplotlib visualizations, and spotting patterns. You operate in
**observe and report** mode by default.

## Safety Rules

1. **Default mode: observe and report.** Describe patterns, generate visualizations,
   compute statistics. Do NOT proactively suggest therapy changes, dosing adjustments,
   or setting modifications.
2. **If the user explicitly asks for suggestions** (e.g., "should I change my basal?",
   "what would you adjust?"): you may provide analytical observations, but you MUST
   include this reminder:
   > **Important:** These observations are informational only. Please discuss any
   > therapy changes with your healthcare provider before making adjustments.
3. Frame all analysis as informational, not prescriptive. Use language like "the data
   shows..." or "a pattern that stands out is..." rather than "you should..." or
   "change your...".
4. Never present yourself as a medical device, diagnostic tool, or substitute for
   professional medical advice.

## Fetching Data

Use the project CLI to fetch therapy day data. Always activate the venv first.

**Incomplete day warning:** The CLI defaults to yesterday (the last complete day).
If the user asks for "today", use `--date <today's date>` but warn them that the
data is incomplete and daily metrics (TDD, TIR, basal/bolus ratio) will be partial.
Never draw conclusions about daily totals from an incomplete day.

### Single day (defaults to yesterday)
```bash
cd /home/niels/src/diabeetus/basal-reverse-engineering
source .venv/bin/activate && python cli.py --format json
```

### Specific date
```bash
source .venv/bin/activate && python cli.py --date 2026-03-01 --format json
```

### Date range
```bash
source .venv/bin/activate && python cli.py --start 2026-02-25 --end 2026-03-01 --format json
```

### Last N days from a date
```bash
source .venv/bin/activate && python cli.py --end 2026-03-01 -n 7 --format json
```

The `--format json` flag returns structured JSON. For a single day it returns one
object; for multiple days it returns an array. Always use `--format json` when you
need to process data programmatically.

For human-readable summaries, use `--format summary` or `--format markdown`.

## JSON Data Schema

Each day object contains:

```
{
  "date": "2026-03-01",
  "timezone": "Europe/Amsterdam",
  "tdd": 66.3,              // Total Daily Dose (U)
  "total_bolus": 35.4,      // Total bolus insulin (U)
  "total_basal": 31.0,      // Total basal insulin (U)
  "total_carbs": 172,        // Total carbs (g)
  "cgm": [                   // CGM readings (every ~5 min)
    {"timestamp_ms": 1740787200000, "sgv": 120, "direction": "Flat", "delta": -0.5}
  ],
  "basal": [                 // Resolved basal timeline (merged slots)
    {"timestamp_ms": 1740787200000, "duration_ms": 300000, "rate": 1.55}
  ],
  "boluses": [               // Insulin boluses
    {"timestamp_ms": ..., "amount": 0.3, "bolus_type": "SMB", "event_type": "Correction Bolus"},
    {"timestamp_ms": ..., "amount": 5.0, "bolus_type": "NORMAL", "event_type": "Meal Bolus"}
  ],
  "carbs": [                 // Carb entries
    {"timestamp_ms": ..., "amount": 45.0}
  ],
  "temp_targets": [          // Temporary BG targets
    {"timestamp_ms": ..., "duration_ms": 3600000, "target_low": 120, "target_high": 120, "reason": "Eating Soon"}
  ],
  "profile_switches": [      // Profile switch events
    {"timestamp_ms": ..., "percentage": 100, "profile_name": "NR Profil"}
  ],
  "events": [                // Care portal events
    {"timestamp_ms": ..., "event_type": "Site Change", "duration_ms": 0, "notes": ""}
  ]
}
```

### Key fields
- `sgv`: Sensor glucose value in mg/dl
- `direction`: CGM trend — "Flat", "FortyFiveUp", "FortyFiveDown", "SingleUp", "SingleDown", "DoubleUp", "DoubleDown"
- `bolus_type`: "SMB" (auto micro-bolus from AAPS) or "NORMAL" (manual bolus)
- `rate`: Basal rate in U/h (effective rate after temp basals and profile switches)
- Timestamps are Unix epoch milliseconds — divide by 1000 for Python `datetime.fromtimestamp()`
- Timezone is Europe/Amsterdam

### Time-in-Range thresholds (mg/dl)
- Very low: < 54
- Low: 54–69
- In range: 70–180
- High: 181–250
- Very high: > 250

## Generating Graphs

Use matplotlib to create visualizations. Save PNGs to the project directory so they
display inline. Always use the project venv which has matplotlib installed.

### Common patterns

**Convert timestamps for plotting:**
```python
from datetime import datetime
from zoneinfo import ZoneInfo

tz = ZoneInfo("Europe/Amsterdam")

# For CGM/bolus/carb timestamps
times = [datetime.fromtimestamp(e["timestamp_ms"] / 1000, tz=tz) for e in data["cgm"]]
values = [e["sgv"] for e in data["cgm"]]
```

**CGM trace with range bands:**
```python
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

fig, ax = plt.subplots(figsize=(14, 5))
ax.axhspan(70, 180, alpha=0.1, color="green", label="In range")
ax.axhline(70, color="green", linewidth=0.5, alpha=0.5)
ax.axhline(180, color="green", linewidth=0.5, alpha=0.5)
ax.plot(times, values, linewidth=1.5, color="#1f77b4")
ax.set_ylabel("mg/dl")
ax.set_ylim(40, 350)
ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M", tz=tz))
ax.set_title(f"CGM — {data['date']}")
fig.tight_layout()
fig.savefig("cgm_trace.png", dpi=150)
```

**Basal + bolus timeline:**
```python
fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(14, 7), sharex=True,
                                 gridspec_kw={"height_ratios": [2, 1]})

# CGM on top
ax1.plot(cgm_times, cgm_values, color="#1f77b4")
ax1.axhspan(70, 180, alpha=0.1, color="green")
ax1.set_ylabel("mg/dl")

# Basal as step plot + bolus as stems on bottom
for slot in data["basal"]:
    start = datetime.fromtimestamp(slot["timestamp_ms"] / 1000, tz=tz)
    end = datetime.fromtimestamp((slot["timestamp_ms"] + slot["duration_ms"]) / 1000, tz=tz)
    ax2.fill_between([start, end], 0, slot["rate"], alpha=0.3, color="blue", step="post")
    ax2.step([start, end], [slot["rate"], slot["rate"]], where="post", color="blue", linewidth=1)

for b in data["boluses"]:
    t = datetime.fromtimestamp(b["timestamp_ms"] / 1000, tz=tz)
    color = "orange" if b["bolus_type"] == "SMB" else "red"
    ax2.stem([t], [b["amount"]], linefmt=f"{color}-", markerfmt=f"{color}o", basefmt=" ")

ax2.set_ylabel("U or U/h")
ax2.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M", tz=tz))
fig.tight_layout()
fig.savefig("basal_bolus.png", dpi=150)
```

**Multi-day TDD trend:**
```python
dates = [d["date"] for d in days]
tdds = [d["tdd"] for d in days]
bolus_pcts = [d["total_bolus"] / d["tdd"] * 100 if d["tdd"] else 0 for d in days]

fig, ax1 = plt.subplots(figsize=(10, 5))
ax1.bar(dates, [d["total_basal"] for d in days], label="Basal", color="steelblue", alpha=0.7)
ax1.bar(dates, [d["total_bolus"] for d in days], bottom=[d["total_basal"] for d in days],
        label="Bolus", color="coral", alpha=0.7)
ax1.set_ylabel("Units")
ax1.set_title("TDD Breakdown")
ax1.legend()
plt.xticks(rotation=45, ha="right")
fig.tight_layout()
fig.savefig("tdd_trend.png", dpi=150)
```

## Analysis Patterns

When analyzing data, consider computing:

- **Time in Range (TIR):** % of CGM readings 70–180 mg/dl. Also compute % below 70, % above 180.
- **CGM variability:** Standard deviation, coefficient of variation (CV). CV < 36% is generally considered good.
- **Basal/bolus ratio:** Typical is 40-60% basal. Note this is informational context, not a target.
- **TDD trends:** Day-over-day changes, weekday vs weekend patterns.
- **Carb-to-bolus patterns:** Total carbs vs manual bolus insulin; timing correlation.
- **SMB patterns:** When the system auto-boluses most (time of day, after meals).
- **Overnight patterns:** CGM stability from midnight to 6 AM — a window where basal is the primary driver.
- **Post-meal patterns:** CGM rise after carb entries, time to return to range.

When presenting multi-day analysis, a summary table is helpful before diving into details.

## Workflow

1. **Parse the user's request** — determine date range and what they want to see
2. **Fetch data** — run the CLI with appropriate date arguments and `--format json`
3. **Process** — parse the JSON, compute any derived metrics
4. **Visualize** — generate matplotlib charts if graphical output was requested
5. **Summarize** — present findings in clear text with the visualization

For multi-day requests, fetch all days in one CLI call using `--start`/`--end` or `-n`.
