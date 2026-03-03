#!/usr/bin/env python3
"""
CLI for fetching and displaying therapy day data from Nightscout.

Usage:
  python cli.py --date 2026-02-27
  python cli.py --start 2026-02-25 --end 2026-02-27
  python cli.py --start 2026-02-25 -n 3
  python cli.py --end 2026-02-27 -n 3
  python cli.py                          # today
  python cli.py --format json
  python cli.py --format markdown
  python cli.py --format debug
"""

import argparse
import os
import sys
from datetime import datetime, date, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None

from models import DayData
from nightscout import get_day
from formatters import FORMATTERS


# ---------------------------------------------------------------------------
# Date range resolution
# ---------------------------------------------------------------------------

def resolve_dates(args) -> list[str]:
    """Resolve CLI date arguments into a list of YYYY-MM-DD strings."""
    today = date.today().isoformat()

    if args.date:
        return [args.date]

    start = args.start
    end = args.end
    n = args.n

    if start and end:
        d = datetime.strptime(start, "%Y-%m-%d").date()
        end_d = datetime.strptime(end, "%Y-%m-%d").date()
        dates = []
        while d <= end_d:
            dates.append(d.isoformat())
            d += timedelta(days=1)
        return dates

    if start and n:
        d = datetime.strptime(start, "%Y-%m-%d").date()
        return [(d + timedelta(days=i)).isoformat() for i in range(n)]

    if end and n:
        end_d = datetime.strptime(end, "%Y-%m-%d").date()
        start_d = end_d - timedelta(days=n - 1)
        d = start_d
        dates = []
        while d <= end_d:
            dates.append(d.isoformat())
            d += timedelta(days=1)
        return dates

    if start:
        return [start]
    if end:
        return [end]

    return [today]


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    if load_dotenv is not None:
        env_path = Path(__file__).parent / ".env"
        if env_path.exists():
            load_dotenv(env_path)

    parser = argparse.ArgumentParser(
        description="Fetch therapy day data from Nightscout API",
        epilog="Date selection: use --date for a single day, or combine "
               "--start/--end/-n for ranges. Defaults to today.",
    )

    # Date selection (all optional)
    date_group = parser.add_argument_group("date selection")
    date_group.add_argument("--date", help="Single date (YYYY-MM-DD)")
    date_group.add_argument("--start", help="Range start date (YYYY-MM-DD)")
    date_group.add_argument("--end", help="Range end date (YYYY-MM-DD)")
    date_group.add_argument("-n", type=int, help="Number of days (with --start or --end)")

    # Output
    parser.add_argument("--format", choices=FORMATTERS.keys(), default="summary",
                        help="Output format (default: summary)")

    # Connection
    parser.add_argument("--ns-api", default=os.environ.get("NS_API_URL"),
                        help="Nightscout API URL (default: $NS_API_URL)")
    parser.add_argument("--ns-api-secret", default=os.environ.get("NS_API_SECRET"),
                        help="Nightscout API secret (default: $NS_API_SECRET)")
    parser.add_argument("--timezone", default=os.environ.get("TIMEZONE", "Europe/Amsterdam"),
                        help="Timezone (default: Europe/Amsterdam)")

    args = parser.parse_args()

    # Validate date arg combinations
    if args.date and (args.start or args.end or args.n):
        parser.error("--date cannot be combined with --start/--end/-n")
    if args.n is not None and args.n < 1:
        parser.error("-n must be >= 1")
    if args.n and not args.start and not args.end:
        parser.error("-n requires --start or --end")

    if not args.ns_api or not args.ns_api_secret:
        print("Error: NS_API_URL and NS_API_SECRET required "
              "(set env vars or use --ns-api/--ns-api-secret)", file=sys.stderr)
        sys.exit(1)

    tz = ZoneInfo(args.timezone)
    dates = resolve_dates(args)

    # Fetch all days
    days: list[DayData] = []
    for i, date_str in enumerate(dates):
        if args.format != "json":
            print(f"Fetching {date_str}... ({i+1}/{len(dates)})", file=sys.stderr)
        days.append(get_day(date_str, args.ns_api, args.ns_api_secret, tz))

    # Format and print
    formatter = FORMATTERS[args.format]
    print(formatter(days, tz))


if __name__ == "__main__":
    main()
