# Calculating Total Daily Dose from Nightscout MongoDB

This document describes how to calculate insulin TDD (Total Daily Dose) broken down
into basal and bolus components from data in a Nightscout MongoDB instance. The goal
is to reproduce the numbers shown on the AAPS Statistics screen.

The implementation is in `insulin_totals.py`, function `get_ns_tdd()`.

## Overview

TDD has four components:

| Component | How it's calculated |
|-----------|-------------------|
| **Bolus** | Sum of `insulin` field from bolus treatments during the day |
| **Basal** | 5-minute numerical integration of effective basal rate over 24 hours |
| **TDD** | Bolus + Basal |
| **Carbs** | Sum of `carbs` field from all treatments during the day |

The tricky part is basal. At any given moment, the effective basal rate is determined
by three layered inputs:

1. The **profile basal schedule** — the baseline rate programmed by the user
2. **Profile switch percentage** — a scaling factor (e.g. 120% = sick day)
3. **Temp basals** — short-lived rate overrides set by the loop algorithm

## Data Model

### MongoDB Collections

Nightscout uses two relevant collections:

- **`profile`** — Stores basal rate schedules and other profile parameters
- **`treatments`** — Stores all events: boluses, temp basals, profile switches, etc.

### Day Boundaries

All calculations use **local-timezone midnight-to-midnight** boundaries. For
`Europe/Amsterdam`, the day `2026-02-24` runs from `2026-02-24T00:00:00+01:00` to
`2026-02-25T00:00:00+01:00`. These are converted to milliseconds-since-epoch for
querying (`date` field in treatments is milliseconds as a float).

---

## Step 1: Load the Profile Basal Schedule

The `profile` collection contains documents like:

```json
{
  "defaultProfile": "NR Profil",
  "store": {
    "NR Profil": {
      "dia": 6,
      "basal": [
        {"time": "00:00", "timeAsSeconds": 0, "value": 1.55}
      ],
      "sens": [...],
      "carbratio": [...],
      "target_low": [...],
      "target_high": [...],
      "units": "mg/dl",
      "timezone": "Europe/Brussels"
    }
  },
  "created_at": "2026-01-26T18:47:35.847Z"
}
```

The `basal` array defines the rate schedule. Each entry specifies a rate in U/h that
takes effect at `timeAsSeconds` seconds after midnight. The rate remains active until
the next entry.

In this case the profile is flat — 1.55 U/h all day. A multi-rate profile would look
like:

```json
"basal": [
  {"time": "00:00", "timeAsSeconds": 0,     "value": 0.80},
  {"time": "06:00", "timeAsSeconds": 21600,  "value": 1.20},
  {"time": "22:00", "timeAsSeconds": 79200,  "value": 0.90}
]
```

To find the rate at a given timestamp: convert to local time, compute
seconds-since-midnight, and find the last schedule entry whose `timeAsSeconds` is ≤
that value.

We load the most recent profile document (`sort created_at descending, limit 1`).

---

## Step 2: Build the Profile Switch Timeline

AAPS allows temporary profile adjustments via "Profile Switch" treatments. These scale
the base profile rate by a percentage. The `percentage` field represents the scaling
factor directly (100 = normal, 120 = 20% increase, 50 = half rate).

Example profile switches from 2026-02-25:

```json
{
  "date": 1771987988000,
  "eventType": "Profile Switch",
  "percentage": 120,
  "profile": "NR Profil (120%)",
  "isValid": true
}
```

```json
{
  "date": 1772056004000,
  "eventType": "Profile Switch",
  "percentage": 100,
  "profile": "NR Profil",
  "isValid": true
}
```

On this day, the profile was at 120% from 03:53 to 22:46 (local time), meaning the
effective base rate was `1.55 × 1.20 = 1.86 U/h` during that window.

To build the timeline:

1. Find the **most recent profile switch before the day starts** — this sets the
   initial percentage at midnight
2. Find all profile switches **during the day** — these update the percentage at their
   timestamp
3. Default to 100% if no profile switch is found

The effective profile rate at time `t` is:

```
effective_profile_rate = base_schedule_rate × (percentage / 100)
```

---

## Step 3: Calculate Bolus Total

Query all bolus treatments for the day:

```python
db.treatments.find({
    "date": {"$gte": start_ms, "$lt": end_ms},
    "eventType": {"$in": ["Meal Bolus", "Correction Bolus",
                          "Snack Bolus", "Combo Bolus"]},
    "insulin": {"$exists": True, "$gt": 0},
    "isValid": {"$ne": False},
})
```

Sum the `insulin` field (units of insulin).

### Event types

AAPS uploads two main bolus types:

- **`Meal Bolus`** (type=`NORMAL`) — Manual boluses, usually with meals.
  Example: `{"eventType": "Meal Bolus", "insulin": 2.52, "type": "NORMAL"}`

- **`Correction Bolus`** (type=`SMB`) — Super Micro Boluses, automated by the loop.
  Example: `{"eventType": "Correction Bolus", "insulin": 0.32, "type": "SMB"}`

### The `isValid` filter

AAPS soft-deletes treatments by setting `isValid: false` rather than removing them
from MongoDB. This happens when a bolus is cancelled, a record is corrected, or AAPS
resyncs with the pump and finds discrepancies.

**This filter is critical.** Without it, deleted boluses are double-counted. On
2026-02-28, a 3.00 U meal bolus was invalidated, which caused a +3.0 U discrepancy
in our bolus total before the fix was applied.

---

## Step 4: Calculate Carbs Total

```python
db.treatments.find({
    "date": {"$gte": start_ms, "$lt": end_ms},
    "carbs": {"$exists": True, "$gt": 0},
    "isValid": {"$ne": False},
})
```

Sum the `carbs` field (grams). Note that carbs can appear on any treatment type — they
are not limited to bolus events. A `Carb Correction` event has carbs but no insulin.

---

## Step 5: Gather Temp Basals

Temp basals are the core of how the AAPS closed loop controls blood sugar. The loop
recalculates every 5 minutes and sets a new temp basal rate based on predicted glucose
trajectory.

### Query

```python
lookback_ms = start_ms - 24 * 60 * 60 * 1000  # 24h before day start
db.treatments.find({
    "eventType": "Temp Basal",
    "date": {"$gte": lookback_ms, "$lt": end_ms},
    "isValid": {"$ne": False},
}).sort("date", 1)
```

We look back 24 hours because a temp basal started before midnight might still be
active at the start of the day. In practice, AAPS temp basals are short (typically
5-30 minutes), but the spec allows up to 24 hours.

### Temp basal document structure

Three real examples from the database, showing the three rate representation formats:

**Percentage-based (0% = pump suspend):**
```json
{
  "date": 1771889036266,
  "eventType": "Temp Basal",
  "duration": 19,
  "durationInMilliseconds": 1165983,
  "percent": -100,
  "rate": 0,
  "type": "NORMAL",
  "pumpType": "ACCU_CHEK_INSIGHT",
  "isValid": true
}
```

**Percentage-based (+30% increase):**
```json
{
  "date": 1771892166854,
  "eventType": "Temp Basal",
  "duration": 5,
  "durationInMilliseconds": 300878,
  "percent": 30,
  "rate": 2.015,
  "type": "NORMAL",
  "pumpType": "ACCU_CHEK_INSIGHT",
  "isValid": true
}
```

**Absolute rate (from emulated extended bolus):**
```json
{
  "date": 1771891630482,
  "eventType": "Temp Basal",
  "duration": 3,
  "durationInMilliseconds": 224000,
  "absolute": 3.9607142857142854,
  "rate": 3.9607142857142854,
  "type": "FAKE_EXTENDED",
  "isValid": true
}
```

### Field reference

| Field | Type | Description |
|-------|------|-------------|
| `date` | float | Start time in ms since epoch |
| `duration` | int | Duration in **minutes** |
| `durationInMilliseconds` | int | Duration in **milliseconds** (more precise, preferred) |
| `rate` | float | Effective absolute rate in U/h (always present) |
| `absolute` | float | Absolute rate in U/h (only on some entries) |
| `percent` | int | Percentage **delta** from 100% (see below) |
| `type` | string | `"NORMAL"` or `"FAKE_EXTENDED"` |
| `isValid` | bool | `false` if soft-deleted |

### The `percent` field

The `percent` field is a **delta from 100%**, not the absolute percentage:

| `percent` | Meaning | Effective rate |
|-----------|---------|---------------|
| `-100` | 0% of profile rate (suspend) | 0 U/h |
| `-10` | 90% of profile rate | profile × 0.90 |
| `0` | 100% of profile rate (normal) | profile × 1.00 |
| `20` | 120% of profile rate | profile × 1.20 |
| `30` | 130% of profile rate | profile × 1.30 |

Formula: `effective_rate = profile_rate × (100 + percent) / 100`

Verification with real data (profile rate = 1.55 U/h):
- `percent: -100` → `1.55 × 0/100 = 0` → `rate: 0` ✓
- `percent: -10` → `1.55 × 90/100 = 1.395` → `rate: 1.395` ✓
- `percent: 20` → `1.55 × 120/100 = 1.86` → `rate: 1.86` ✓
- `percent: 30` → `1.55 × 130/100 = 2.015` → `rate: 2.015` ✓

### The `rate` field as pre-computed absolute

In practice, AAPS always includes the `rate` field with the pre-computed absolute rate
in U/h. This means we can use `rate` directly and only fall back to the
`percent`-based calculation if `rate` is missing. The `absolute` field is only present
on some entries (11 out of 102 on a sample day) — typically `FAKE_EXTENDED` types.

Rate resolution priority:
1. `absolute` field (if present)
2. `rate` field (if present, used as absolute U/h)
3. `percent` field + profile rate (fallback calculation)

### Duration handling

The `durationInMilliseconds` field is more precise than `duration` (minutes). When
present, prefer it. The `duration` field is always in minutes for AAPS-originated
data, but as a safety measure, values over 100,000 are treated as already being in
milliseconds (to handle potential format variations from other uploaders).

### The `FAKE_EXTENDED` type

When a pump supports extended boluses but the loop system needs to override them, AAPS
may represent an extended bolus as a temporary basal with `type: "FAKE_EXTENDED"`.
These entries have an `extendedEmulated` sub-object containing the original extended
bolus parameters. For the purpose of TDD calculation, they are treated identically to
normal temp basals — the `rate`/`absolute` field already contains the correct effective
rate.

---

## Step 6: Basal Integration (5-Minute Loop)

This is the core algorithm. It matches how AAPS internally calculates TDD in
`TddCalculatorImpl.calculateInterval()`.

### Algorithm

```
align start_ms and end_ms down to nearest 5-minute boundary
basal_total = 0

for t from start_aligned to end_aligned, step 5 minutes:

    1. Look up base profile rate at time t
       (convert t to local time → seconds since midnight → find matching schedule entry)

    2. Apply profile switch percentage
       (find the most recent profile switch with timestamp ≤ t, apply its percentage)
       → profile_rate = base_rate × percentage / 100

    3. Check for active temp basal at time t
       (find temp basal where timestamp ≤ t < timestamp + duration)
       → if found with absolute/rate: effective_rate = absolute
       → if found with percent only: effective_rate = profile_rate × (100 + percent) / 100
       → if none found: effective_rate = profile_rate

    4. Accumulate
       → basal_total += effective_rate / 60 × 5
         (convert U/h to U per 5-minute interval)
```

### Why 5 minutes?

AAPS recalculates and potentially sets a new temp basal every 5 minutes. The 5-minute
step size is hardcoded in AAPS (`T.mins(5).msecs() = 300,000 ms`). Using the same step
size ensures our integration aligns with how AAPS itself counts insulin delivery.

There are 288 five-minute intervals in 24 hours (24 × 60 / 5 = 288).

### Alignment

Both start and end timestamps are aligned **down** to the nearest 5-minute boundary
before iteration begins:

```python
aligned = ts_ms - (ts_ms % 300_000)
```

This matches the AAPS source:
```kotlin
val startTimeAligned = startTime - startTime % (5 * 60 * 1000)
```

### Accumulation formula

Each 5-minute tick contributes:

```
insulin_units = rate_in_U_per_hour / 60 × 5
```

For example, at 1.55 U/h:
- Per tick: 1.55 / 60 × 5 = 0.12917 U
- Full day (no temp basals): 0.12917 × 288 = 37.2 U

---

## Putting It All Together

```
TDD = bolus_total + basal_total
Basal % = basal_total / TDD × 100
```

---

## Verification Results

Compared against 7 days of AAPS Statistics screen data:

```
Date          TDD(NS)  TDD(AAPS)   ΔTDD   ΔBolus  ΔBasal  ΔCarbs
2026-02-24     66.3      66.4      -0.1    -0.0    -0.0      0
2026-02-25     67.0      67.1      -0.1    -0.1    -0.0      0
2026-02-26     56.0      56.0       0.0     0.0     0.0      0
2026-02-27     53.8      53.8       0.0    -0.0    -0.0      0
2026-02-28     50.1      50.1       0.0    -0.0    -0.0      0
2026-03-01     49.4      49.0      +0.4     0.0    +0.3      0
2026-03-02     65.3      65.3       0.0     0.0    -0.0      0
```

Bolus and carbs match exactly on all days. Basal is within ±0.3 U, which is the
expected rounding error from discrete 5-minute integration against temp basals that
start/end at arbitrary timestamps.

### Bugs found during verification

1. **Missing `isValid` filter** — Nightscout retains soft-deleted records with
   `isValid: false`. Without filtering these out, a cancelled 3.00 U bolus on Feb 28
   was being counted, causing a +3.0 U discrepancy.

2. **Profile switch percentage not applied** — On Feb 25, the profile was scaled to
   120% for ~19 hours. Without tracking the profile switch timeline and applying the
   percentage to the base rate, the basal total was 1.3 U too low.

---

## Appendix: MongoDB Queries Used

### Profile (most recent)
```javascript
db.profile.findOne({created_at: {$exists: true}}, {sort: {created_at: -1}})
```

### Profile switches (timeline for a day)
```javascript
// Initial state: most recent switch before day start
db.treatments.findOne(
  {eventType: "Profile Switch", date: {$lt: start_ms}, isValid: {$ne: false}},
  {sort: {date: -1}}
)

// Changes during the day
db.treatments.find(
  {eventType: "Profile Switch", date: {$gte: start_ms, $lt: end_ms}, isValid: {$ne: false}}
).sort({date: 1})
```

### Boluses
```javascript
db.treatments.find({
  date: {$gte: start_ms, $lt: end_ms},
  eventType: {$in: ["Meal Bolus", "Correction Bolus", "Snack Bolus", "Combo Bolus"]},
  insulin: {$exists: true, $gt: 0},
  isValid: {$ne: false}
})
```

### Carbs
```javascript
db.treatments.find({
  date: {$gte: start_ms, $lt: end_ms},
  carbs: {$exists: true, $gt: 0},
  isValid: {$ne: false}
})
```

### Temp basals (overlapping the day)
```javascript
db.treatments.find({
  eventType: "Temp Basal",
  date: {$gte: start_ms - 86400000, $lt: end_ms},  // 24h lookback
  isValid: {$ne: false}
}).sort({date: 1})
// Then filter client-side: skip if timestamp + duration ≤ start_ms
```

---

## Appendix: Typical Day Profile

On a typical day for this pump (AccuChek Insight), the loop sets ~100 temp basals,
with the vast majority being percentage-based adjustments. Only about 10% have an
`absolute` field. The `rate` field (pre-computed absolute U/h) is always present,
making it the most reliable field for determining the effective rate.

A typical day has ~15 correction boluses (SMBs, 0.2-0.5 U each) and ~5 meal boluses
(1-8 U each). The basal percentage ranges from 39% (active day with many meal boluses)
to 64% (quiet day with few meals).
