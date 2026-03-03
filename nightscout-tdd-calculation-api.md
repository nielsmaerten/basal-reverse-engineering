# Calculating Total Daily Dose from the Nightscout REST API

This document describes how to calculate insulin TDD (Total Daily Dose) broken down
into basal and bolus components using only the Nightscout REST API (v1). No direct
MongoDB access is required — everything is done over HTTPS.

The implementation is in `insulin_totals.py`, function `get_ns_tdd_api()`.

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

## Authentication

The v1 API authenticates via the `api-secret` HTTP header. The value must be the
**SHA-1 hex digest** of the plaintext API secret:

```python
import hashlib
hashed = hashlib.sha1("your api secret".encode()).hexdigest()
headers = {"api-secret": hashed}
```

All requests below use this header.

## Day Boundaries and the `created_at` Gotcha

All calculations use **local-timezone midnight-to-midnight** boundaries. For
`Europe/Amsterdam`, the day `2026-02-24` runs from `2026-02-24T00:00:00+01:00` to
`2026-02-25T00:00:00+01:00`.

**Important:** The v1 API does not support filtering on the `date` field (which stores
milliseconds since epoch as a number). Queries like `find[date][$gte]=1771887600000`
silently return zero results — likely a type coercion bug where the numeric value is
compared as a string.

Instead, filter on **`created_at`** using UTC ISO 8601 strings. Convert local midnight
to UTC first:

```
Europe/Amsterdam midnight Feb 24 = 2026-02-23T23:00:00Z  (UTC+1 in winter)
Europe/Amsterdam midnight Feb 25 = 2026-02-24T23:00:00Z
```

The `date` field (ms epoch) is still returned in the response and used for all
timestamp calculations — only the *query filter* uses `created_at`.

## API Query Syntax

The v1 API uses MongoDB-style query operators embedded in query parameters:

```
find[field][$operator]=value
```

Supported operators: `$gte`, `$gt`, `$lte`, `$lt`, `$ne`, `$eq`, `$in`, `$nin`.

Pagination: `count=N` (default 100, max ~1000). Results are sorted by `created_at`
descending (most recent first) by default.

---

## Step 1: Load the Profile Basal Schedule

```
GET /api/v1/profile/current
```

Returns the most recent profile document:

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

---

## Step 2: Build the Profile Switch Timeline

AAPS allows temporary profile adjustments via "Profile Switch" treatments. These scale
the base profile rate by a percentage. The `percentage` field represents the scaling
factor directly (100 = normal, 120 = 20% increase, 50 = half rate).

### Fetch the initial state (most recent switch before day start)

```
GET /api/v1/treatments.json
    ?count=1
    &find[created_at][$lt]=2026-02-23T23:00:00Z
    &find[eventType]=Profile Switch
```

The API returns most recent first by default, so `count=1` gives the latest switch
before the day.

### Fetch switches during the day

These come from the day's treatments fetched in the next step — just filter for
`eventType == "Profile Switch"` client-side.

### Example profile switches from 2026-02-25

```json
{
  "date": 1771987988000,
  "eventType": "Profile Switch",
  "percentage": 120,
  "profile": "NR Profil (120%)",
  "isValid": true,
  "created_at": "2026-02-25T02:53:08.000Z"
}
```

```json
{
  "date": 1772056004000,
  "eventType": "Profile Switch",
  "percentage": 100,
  "profile": "NR Profil",
  "isValid": true,
  "created_at": "2026-02-25T21:46:44.000Z"
}
```

On this day, the profile was at 120% from 03:53 to 22:46 (local time), meaning the
effective base rate was `1.55 × 1.20 = 1.86 U/h` during that window.

The effective profile rate at time `t` is:

```
effective_profile_rate = base_schedule_rate × (percentage / 100)
```

Default to 100% if no profile switch is found.

---

## Step 3: Fetch All Treatments for the Day

A single request retrieves everything needed for bolus, carbs, and profile switch
extraction:

```
GET /api/v1/treatments.json
    ?count=1000
    &find[created_at][$gte]=2026-02-23T23:00:00Z
    &find[created_at][$lt]=2026-02-24T23:00:00Z
```

A typical day returns ~150 treatments. All client-side filtering (by `eventType`,
`isValid`, etc.) is done on this response.

---

## Step 4: Calculate Bolus Total

From the day's treatments, select bolus entries:

```python
bolus_event_types = {"Meal Bolus", "Correction Bolus", "Snack Bolus", "Combo Bolus"}

for t in day_treatments:
    if (t["eventType"] in bolus_event_types
            and t.get("isValid") is not False
            and t.get("insulin") and float(t["insulin"]) > 0):
        bolus_total += float(t["insulin"])
```

### Event types

AAPS uploads two main bolus types:

- **`Meal Bolus`** (type=`NORMAL`) — Manual boluses, usually with meals.
  Example: `{"eventType": "Meal Bolus", "insulin": 2.52, "type": "NORMAL"}`

- **`Correction Bolus`** (type=`SMB`) — Super Micro Boluses, automated by the loop.
  Example: `{"eventType": "Correction Bolus", "insulin": 0.32, "type": "SMB"}`

### Null insulin and carbs fields

A `Meal Bolus` treatment may have `"insulin": null` (carbs-only entry) or
`"carbs": null` (insulin-only entry). Always check for null/missing before converting
to float.

### The `isValid` filter

AAPS soft-deletes treatments by setting `isValid: false` rather than removing them.
This happens when a bolus is cancelled, a record is corrected, or AAPS resyncs with
the pump and finds discrepancies.

**This filter is critical.** Without it, deleted boluses are double-counted. On
2026-02-28, a 3.00 U meal bolus was invalidated, which caused a +3.0 U discrepancy
before the fix was applied.

---

## Step 5: Calculate Carbs Total

From the same day's treatments:

```python
for t in day_treatments:
    if (t.get("isValid") is not False
            and t.get("carbs") and float(t["carbs"]) > 0):
        carbs_total += float(t["carbs"])
```

Carbs can appear on any treatment type — a `Carb Correction` has carbs but no insulin,
while a `Meal Bolus` may have both or either.

---

## Step 6: Gather Temp Basals

Temp basals are the core of how the AAPS closed loop controls blood sugar. The loop
recalculates every 5 minutes and sets a new temp basal rate based on predicted glucose
trajectory.

### Query

We need temp basals from 24 hours before the day start (to catch any that span
midnight) through end of day:

```
GET /api/v1/treatments.json
    ?count=1000
    &find[created_at][$gte]=2026-02-22T23:00:00Z
    &find[created_at][$lt]=2026-02-24T23:00:00Z
    &find[eventType]=Temp Basal
```

Then filter client-side: skip any where `date + durationInMilliseconds ≤ day_start_ms`.

### Temp basal document structure

Three real examples showing the three rate representation formats:

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
  "isValid": true,
  "created_at": "2026-02-23T23:23:56.266Z"
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
  "isValid": true,
  "created_at": "2026-02-24T00:16:06.854Z"
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
  "isValid": true,
  "created_at": "2026-02-24T00:07:10.482Z"
}
```

### Field reference

| Field | Type | Description |
|-------|------|-------------|
| `date` | int | Start time in ms since epoch |
| `created_at` | string | ISO 8601 UTC timestamp (used for queries) |
| `duration` | int | Duration in **minutes** |
| `durationInMilliseconds` | int | Duration in **milliseconds** (more precise, preferred) |
| `rate` | float | Effective absolute rate in U/h (always present for AAPS data) |
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

## Step 7: Basal Integration (5-Minute Loop)

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

## Complete Request Summary

For a single day's TDD calculation, exactly **4 HTTP requests** are needed:

| # | Request | Purpose |
|---|---------|---------|
| 1 | `GET /api/v1/profile/current` | Load basal rate schedule |
| 2 | `GET /api/v1/treatments.json?count=1&find[created_at][$lt]={start_utc}&find[eventType]=Profile Switch` | Initial profile switch percentage |
| 3 | `GET /api/v1/treatments.json?count=1000&find[created_at][$gte]={start_utc}&find[created_at][$lt]={end_utc}` | All day's treatments (boluses, carbs, profile switches) |
| 4 | `GET /api/v1/treatments.json?count=1000&find[created_at][$gte]={lookback_utc}&find[created_at][$lt]={end_utc}&find[eventType]=Temp Basal` | Temp basals (24h lookback + day) |

Where:
- `start_utc` / `end_utc` = local midnight converted to UTC ISO strings
- `lookback_utc` = 24 hours before `start_utc`

All requests carry the `api-secret` header with the SHA-1 hash.

---

## Verification Results

Compared against both direct MongoDB access and the AAPS Statistics screen across 7
days:

```
Date          TDD(API) TDD(Mongo) TDD(AAPS)  ΔMongo  ΔAAPS
2026-02-24     66.3      66.3       66.4      0.000   -0.1
2026-02-25     67.0      67.0       67.1      0.000   -0.1
2026-02-26     56.0      56.0       56.0      0.000    0.0
2026-02-27     53.8      53.8       53.8      0.000    0.0
2026-02-28     50.1      50.1       50.1      0.000    0.0
2026-03-01     49.4      49.4       49.0      0.000   +0.4
2026-03-02     65.3      65.3       65.3      0.000    0.0
```

The API and MongoDB calculations produce **identical results** to floating-point
precision on all 7 days tested. Both match the AAPS Statistics screen within ±0.4 U
(from 5-minute integration rounding at temp basal boundaries).

### Bugs found during verification

1. **Missing `isValid` filter** — Nightscout retains soft-deleted records with
   `isValid: false`. Without filtering these out, a cancelled 3.00 U bolus on Feb 28
   was being counted, causing a +3.0 U discrepancy.

2. **Profile switch percentage not applied** — On Feb 25, the profile was scaled to
   120% for ~19 hours. Without tracking the profile switch timeline and applying the
   percentage to the base rate, the basal total was 1.3 U too low.

3. **Cannot filter by `date` field in v1 API** — Queries using
   `find[date][$gte]=<epoch_ms>` return zero results. Use `find[created_at]` with UTC
   ISO strings instead, then use the `date` field from the response for calculations.

---

## Appendix: Typical Day Profile

On a typical day for this pump (AccuChek Insight), the loop sets ~100 temp basals,
with the vast majority being percentage-based adjustments. Only about 10% have an
`absolute` field. The `rate` field (pre-computed absolute U/h) is always present,
making it the most reliable field for determining the effective rate.

A typical day has ~15 correction boluses (SMBs, 0.2-0.5 U each) and ~5 meal boluses
(1-8 U each). The basal percentage ranges from 39% (active day with many meal boluses)
to 64% (quiet day with few meals).

The total number of API-returned treatments per day is ~150, well within the 1000-count
limit of a single request.
