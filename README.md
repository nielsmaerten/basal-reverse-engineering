# Nightflux 3 🎉

An AI-powered tool for understanding your Nightscout diabetes data. It connects to
your Nightscout instance, fetches CGM, insulin, and carb data, and presents
interactive visual reports — the kind you'd bring to an endo appointment.

[![Watch the demo](https://img.youtube.com/vi/pJwGmjV5dok/maxresdefault.jpg)](https://youtu.be/pJwGmjV5dok)

Built for [AAPS (AndroidAPS)](https://github.com/nightscout/AndroidAPS) users with a
[Nightscout](https://nightscout.github.io/) instance, but works with any Nightscout
setup that exposes the v1 REST API.

## What it does

You talk to Claude in natural language. Ask it to analyze your data and it builds a
live slidedeck in your browser with interactive charts, stat cards, and written
observations — all while you watch it come together in real time.

A default "help me understand my data" prompt produces a 30-day report with:

- Headline stats (TIR, GMI, CV, lows)
- Daily time-in-range breakdown
- AGP (Ambulatory Glucose Profile) overlay
- TDD and basal/bolus trends
- Carb intake patterns
- Glucose variability analysis
- Weekly comparisons and notable days
- Summary with key patterns

You can also ask specific questions ("show me overnight patterns this week",
"compare weekdays vs weekends", "what does my morning rise look like?") and Claude
will tailor the analysis.

## Prerequisites

- Python 3.11+
- [Claude Code](https://docs.anthropic.com/en/docs/claude-code) CLI
- A Nightscout instance with API access
- tmux (for browser-based terminal access)

## Setup

### 1. Clone and install dependencies

```bash
git clone <repo-url> && cd basal-reverse-engineering
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Configure your Nightscout connection

Create a `.env` file in the project root:

```
NS_API_URL=https://your-nightscout.example.com
NS_API_SECRET=your-api-secret
TIMEZONE=Europe/Amsterdam
```

| Variable | Required | Description |
|----------|----------|-------------|
| `NS_API_URL` | yes | Your Nightscout base URL |
| `NS_API_SECRET` | yes | API secret (plain text — hashed automatically) |
| `TIMEZONE` | no | Timezone for day boundaries (default: `Europe/Amsterdam`) |

### 3. Start a tmux session

The slidedeck includes a web-based terminal so you can interact with Claude from the
browser. This requires a tmux session:

```bash
tmux new-session -s claude
```

### 4. Launch Claude Code

Inside the tmux session:

```bash
claude
```

Claude automatically picks up the MCP server config (`.mcp.json`) and the Nightscout
skill. No extra configuration needed.

### 5. Start talking

```
> Help me understand my diabetes data
```

Claude will open a slidedeck in your browser at `http://localhost:8765`, fetch your
last 30 days of data, and start building slides. You can watch the presentation come
together in real time, and use the built-in terminal panel in the browser to continue
the conversation.

## Browser interface

The slidedeck opens at `http://localhost:8765` and includes:

- **Slide panel** — interactive Plotly charts, stat cards, and markdown commentary
- **Terminal panel** — a live terminal connected to your Claude session (toggle with
  the terminal button in the top bar)
- **Slide navigation** — sidebar thumbnails, arrow keys, or the bottom nav bar

The terminal panel supports mouse-wheel scrolling and has scroll buttons in the header
for paging through output.

## CLI reference

The underlying CLI can also be used directly for quick lookups:

```bash
source .venv/bin/activate

# Yesterday's summary (default)
python -m nightscout

# Specific date
python -m nightscout --date 2026-03-01

# Date range
python -m nightscout --start 2026-02-01 --end 2026-03-01

# Last 14 days
python -m nightscout --end 2026-03-01 -n 14

# Output formats: summary (default), markdown, json, debug
python -m nightscout --format json
```

## Project structure

```
.
├── pyproject.toml             # Project metadata + entry points
├── requirements.txt           # Python dependencies
├── src/
│   ├── nightscout/            # Nightscout data library + CLI
│   │   ├── __init__.py
│   │   ├── __main__.py        #   CLI entry (python -m nightscout)
│   │   ├── api.py             #   REST API client
│   │   ├── models.py          #   Data models (CGM, insulin, carbs, etc.)
│   │   └── formatters.py      #   Output formatters (summary, markdown, json)
│   └── slidedeck/             # MCP server for browser-based presentations
│       ├── __init__.py
│       ├── __main__.py        #   python -m slidedeck entry
│       ├── server.py          #   FastMCP server + slidedeck tools
│       ├── web.py             #   HTTP/WebSocket server
│       ├── terminal.py        #   PTY-to-WebSocket bridge (tmux)
│       ├── state.py           #   Slide state management
│       └── client.html        #   Browser frontend
├── scripts/
│   └── insulin_totals.py      # TDD comparison (AAPS vs Nightscout)
├── .claude/skills/            # Claude Code skill definitions
│   ├── nightscout/            #   Data analysis + visualization skill
│   └── slidedeck/             #   Presentation building skill
├── .mcp.json                  # MCP server configuration
├── .env                       # Nightscout credentials (create this)
└── docs/                      # Documentation
```

## Safety

This tool operates in **observe-and-report** mode. Claude describes patterns and
generates visualizations but does not proactively suggest therapy changes. If you
explicitly ask for suggestions, Claude will provide analytical observations with a
reminder to discuss any changes with your healthcare provider.

This is not a medical device and is not a substitute for professional medical advice.
