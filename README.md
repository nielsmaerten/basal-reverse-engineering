# Nightscout Therapy Day CLI

Fetches daily therapy data (insulin, carbs, CGM, temp targets, events) from a
Nightscout instance via its REST API and displays it in various formats. Designed
for use with AAPS (AndroidAPS) and the Nightscout v1 API.

## Prerequisites

- Python 3.11+
- A Nightscout instance with API access enabled

## Setup

```bash
git clone <repo-url> && cd basal-reverse-engineering
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Create a `.env` file (or export the variables):

```
NS_API_URL=https://your-nightscout.example.com
NS_API_SECRET=your-api-secret
TIMEZONE=Europe/Amsterdam
```

## Usage

```bash
# Today's summary
python cli.py

# Specific date
python cli.py --date 2026-02-27

# Date range
python cli.py --start 2026-02-25 --end 2026-02-27

# Last 7 days ending today
python cli.py --end $(date +%Y-%m-%d) -n 7

# 3 days starting from a date
python cli.py --start 2026-02-25 -n 3

# Different output formats
python cli.py --format markdown
python cli.py --format json
python cli.py --format debug
```

## Output Formats

**summary** (default) — compact text block per day:
```
============================================================
  2026-02-27  (Europe/Amsterdam)
============================================================
  TDD:    58.3 U
  Bolus:  28.1 U  (48%)
  Basal:  30.2 U  (52%)
  Carbs:   145 g
  CGM:    288 readings  avg 132  range 72-245 mg/dl
  Boluses: 42  Basal slots: 87  Events: 2
```

**markdown** — summary table + per-day detail tables (boluses, carbs, temp targets, events)

**json** — full day data as JSON (single object for one day, array for multiple)

**debug** — verbose per-slot / per-bolus output for troubleshooting

## Legacy: TDD Comparison

`insulin_totals.py` is a standalone script that compares TDD calculations across
multiple sources (AAPS SQLite database, Nightscout MongoDB, Nightscout REST API).
It requires additional dependencies (`pymongo`) and access to the AAPS database.

```bash
python insulin_totals.py --date 2026-02-24
python insulin_totals.py --date 2026-02-24 --ns-only
```

## Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `NS_API_URL` | yes | — | Nightscout base URL |
| `NS_API_SECRET` | yes | — | Nightscout API secret (plain text, hashed automatically) |
| `TIMEZONE` | no | `Europe/Amsterdam` | Timezone for day boundaries |
| `NS_MONGO_URI` | no | — | MongoDB URI (legacy `insulin_totals.py` only) |
| `AAPS_DB_PATH` | no | — | Path to AAPS SQLite DB (legacy `insulin_totals.py` only) |
