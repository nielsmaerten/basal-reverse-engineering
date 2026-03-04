---
name: nightscout
description: Fetch, analyze, and visualize Nightscout diabetes therapy data (CGM, insulin, carbs)
---

# Nightscout Data Analysis Skill

You help the user explore their Nightscout diabetes therapy data — fetching it via the
CLI, generating visualizations, and spotting patterns. You operate in **observe and
report** mode by default.

You have two visualization modes:
- **matplotlib** — Quick inline PNG charts shown in the terminal. Best for single
  charts, quick checks, and one-off analysis.
- **slidedeck** — Rich browser-based presentation with interactive Plotly charts,
  stat cards, TIR bars, and markdown commentary. Best for comprehensive reviews,
  multi-day analysis, and anything the user wants to present or share.

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

## Default Behavior

When the user gives a generic or open-ended prompt — e.g., "help me understand my
data", "analyze my nightscout", "how am I doing?", "diabetes review" — without
specifying a date range or visualization mode:

1. **Default to the slidedeck** with the standard 30-day report
2. **Date range:** last 30 days ending yesterday (yesterday is the last complete day)
3. **Slide structure:** follow the typical slide structure below (title → headline
   stats → daily TIR → AGP → TDD → carbs → variability → weekly trends → notable
   days → summary)
4. **Open the deck immediately**, then build slides progressively so the user sees
   them appear in real time
5. **Navigate to the title slide** after adding the first batch, then let the user
   browse as more slides arrive

This is the "standard report" — the kind of overview a user would bring to an
endo appointment. Only deviate from this default when the user gives specific
instructions (a different date range, a specific metric, a particular chart style).

## Visualization: Choosing a Mode

**Use matplotlib** when the user wants:
- A quick look at a single chart (e.g., "show me yesterday's CGM")
- Inline terminal output for fast iteration
- A one-off plot they don't need to interact with

**Use the slidedeck** when the user wants:
- A presentation, briefing, review, or slide deck
- Comprehensive multi-day or multi-metric analysis
- Interactive hover/zoom charts (Plotly)
- Stat cards, TIR bars, and rich HTML layouts
- Something they might present to a healthcare provider or share
- **Any open-ended or generic request** (this is the default — see above)

If the user says "create slides", "make a presentation", "briefing", or "slide deck",
use the slidedeck. If the user asks for a specific single chart (e.g., "show me
yesterday's CGM"), use matplotlib. For everything else — especially generic requests
like "analyze my data" or "how am I doing?" — **default to the slidedeck**.

---

## Matplotlib Visualizations

Use matplotlib to create quick PNG charts shown inline in the terminal. Save PNGs to
the project directory. Always use the project venv which has matplotlib installed.

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

---

## Slidedeck Presentations

The slidedeck MCP server provides a live browser-based presentation at
`http://localhost:8765`. Use it for rich, multi-slide analysis with interactive charts.

### MCP Tools

| Tool | Purpose |
|------|---------|
| `deck_open(title?)` | Open browser and set deck title |
| `deck_close()` | Clear all slides and reset |
| `slide_add(id, type, content, title?, position?)` | Add a slide |
| `slide_update(id, content?, title?, type?)` | Update existing slide |
| `slide_remove(id)` | Remove a slide |
| `slide_navigate(id)` | Navigate the browser to a slide |

### Slide Types

- **`html`** / **`stats`** — Raw HTML. Use the built-in CSS classes (stat cards, TIR
  bars, title cards, split layouts). `stats` is identical to `html` but gets a
  different sidebar icon.
- **`markdown`** — Parsed with marked.js, wrapped in `.slide-commentary`. Supports
  `.highlight` and `.warning` callout divs.
- **`image`** — Absolute file path to a PNG/JPG. The server copies it to assets.
  Use for matplotlib charts or AGP overlays that are hard to do in Plotly.
- **`plotly`** — JSON string of a Plotly figure `{"data": [...], "layout": {...}}`.
  Interactive: hover, zoom, pan. Preferred for most charts in the slidedeck.

### Typical Slide Structure for a Data Review

1. **Title slide** (`html`) — Title card with date range
2. **Headline stats** (`stats`) — KPI cards (TIR, GMI, CV, lows) + TIR bar
3. **Daily TIR** (`plotly`) — Stacked bar chart of daily TIR breakdown
4. **AGP profile** (`image`) — Matplotlib-generated percentile overlay (hard to do
   well in Plotly due to the 288-slot percentile computation; generate as PNG)
5. **AGP commentary** (`markdown`) — Written observations on the AGP
6. **TDD breakdown** (`plotly`) — Stacked basal/bolus bar with avg line
7. **Daily carbs** (`plotly`) — Bar chart with average line
8. **Variability** (`plotly`) — Dual-axis: mean glucose line + CV bars
9. **Weekly trends** (`stats`) — Week-by-week comparison cards
10. **Notable days** (`stats`) — Best/worst/outlier day breakdowns
11. **Summary** (`markdown`) — Key patterns + healthcare disclaimer

Not every analysis needs all slides — adapt to the scope and date range.

### CSS Classes Reference

See the slidedeck skill (`/.claude/skills/slidedeck/SKILL.md`) for the full CSS class
reference. Key classes:

- `.stats-grid` + `.stat-card` — Grid of KPI cards with `.stat-value.good|warn|bad|neutral`
- `.tir-bar-container` + `.tir-bar` + `.tir-segment.very-low|low|in-range|high|very-high`
- `.slide-title-card` with `h1`, `.subtitle`, `.date-range`
- `.slide-commentary` with `.highlight` (green) and `.warning` (red) callout divs
- `.slide-split` — Two-column grid
- `.disclaimer` — Footer disclaimer with icon

### Plotly Chart Patterns

Build Plotly figures as Python dicts, then `json.dumps()` them to pass as slide
content. Generate the data in a Python script via Bash, write JSON to a temp file,
then read it back to pass to `slide_add`.

**TIR stacked bar:**
```python
import json
tir_spec = {
    "data": [
        {"x": dates, "y": very_low_pcts, "name": "<54", "type": "bar", "marker": {"color": "#8B0000"}},
        {"x": dates, "y": low_pcts,      "name": "54–69", "type": "bar", "marker": {"color": "#ea4335"}},
        {"x": dates, "y": in_range_pcts, "name": "70–180", "type": "bar", "marker": {"color": "#34a853"}},
        {"x": dates, "y": high_pcts,     "name": "181–250", "type": "bar", "marker": {"color": "#fbbc04"}},
        {"x": dates, "y": very_high_pcts, "name": ">250", "type": "bar", "marker": {"color": "#ff8c42"}},
    ],
    "layout": {
        "barmode": "stack",
        "yaxis": {"title": "% of readings", "range": [0, 100]},
        "legend": {"orientation": "h", "y": -0.15, "x": 0.5, "xanchor": "center"},
        "margin": {"t": 50, "b": 80, "l": 50, "r": 20},
        "height": 450,
        "hovermode": "x unified",
    }
}
print(json.dumps(tir_spec))
```

**TDD stacked bar with average line:**
```python
tdd_spec = {
    "data": [
        {"x": dates, "y": basal_vals, "name": "Basal", "type": "bar",
         "marker": {"color": "#4682b4"}, "hovertemplate": "%{y:.1f} U"},
        {"x": dates, "y": bolus_vals, "name": "Bolus", "type": "bar",
         "marker": {"color": "#ff7f50"}, "hovertemplate": "%{y:.1f} U"},
        {"x": dates, "y": [avg_tdd]*n, "name": f"Avg ({avg_tdd:.0f}U)", "type": "scatter",
         "mode": "lines", "line": {"color": "gray", "dash": "dash", "width": 1}},
    ],
    "layout": {
        "barmode": "stack",
        "yaxis": {"title": "Units"},
        "height": 450,
        "hovermode": "x unified",
    }
}
```

**Dual-axis variability (mean glucose + CV):**
```python
variability_spec = {
    "data": [
        {"x": dates, "y": means, "name": "Mean glucose", "type": "scatter",
         "mode": "lines+markers", "marker": {"color": "#4682b4", "size": 6},
         "yaxis": "y", "hovertemplate": "%{y:.0f} mg/dl"},
        {"x": dates, "y": cvs, "name": "CV %", "type": "bar",
         "marker": {"color": cv_colors},  # green <36%, orange ≥36%
         "yaxis": "y2", "hovertemplate": "%{y:.0f}%"},
    ],
    "layout": {
        "yaxis": {"title": "Mean glucose (mg/dl)", "side": "left", "range": [70, 180]},
        "yaxis2": {"title": "CV (%)", "side": "right", "overlaying": "y", "range": [0, 55]},
        "height": 450,
        "hovermode": "x unified",
        "shapes": [{"type": "line", "y0": 36, "y1": 36, "x0": 0, "x1": 1,
                     "xref": "paper", "yref": "y2",
                     "line": {"color": "#ea4335", "width": 1, "dash": "dot"}}],
    }
}
```

**Plotly layout tips:**
- Omit `paper_bgcolor` and `plot_bgcolor` — the client auto-themes them
- Set `height` in layout (400–500 works well); width is responsive
- Use `hovermode: "x unified"` for multi-trace charts
- Use `legend.orientation: "h"` with `y: -0.15` to put the legend below the chart
- Use `shapes` for reference lines (e.g., CV 36% threshold, TIR 70% target)

### Slidedeck Workflow

1. **Fetch data** with the CLI (same as always)
2. **Compute metrics** in a Python script — write Plotly JSON to temp files
3. **`deck_open(title)`** to launch the browser. **If the response contains a
   WARNING about a missing tmux session**, stop and inform the user — see the
   slidedeck skill's "Tmux Session Warning" section. Wait for their choice before
   continuing.
4. **Add slides progressively** — the user sees them appear in real time
5. **Use `slide_navigate()`** to direct attention after adding a batch
6. **Mix slide types** — Plotly for interactive charts, `image` for complex
   matplotlib renders (AGP overlays), `stats` for KPI dashboards, `markdown`
   for written analysis
7. Include a healthcare **disclaimer** on the summary slide

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

1. **Parse the user's request** — determine date range, what they want to see, and
   which visualization mode to use. If the request is generic or open-ended, apply
   the default behavior (30-day slidedeck report — see above).
2. **Fetch data** — run the CLI with appropriate date arguments and `--format json`.
   For the default 30-day report: `python cli.py --end <yesterday> -n 30 --format json`
3. **Open the slidedeck** (if using it) — `deck_open()` immediately so the browser
   opens while you compute. **If `deck_open` returns a tmux warning**, stop and
   follow the slidedeck skill's "Tmux Session Warning" instructions before continuing.
4. **Process** — parse the JSON, compute derived metrics (TIR, CV, GMI, etc.)
5. **Build slides progressively** — add slides one at a time so the user sees
   progress in real time. Navigate to the title slide after the first batch.
6. **Summarize** — the final slide should be a markdown summary with key patterns
   and the healthcare disclaimer

For multi-day requests, fetch all days in one CLI call using `--start`/`--end` or `-n`.
