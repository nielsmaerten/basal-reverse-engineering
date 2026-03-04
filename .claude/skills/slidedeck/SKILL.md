# Slidedeck — Live Presentation Skill

Present data as a live browser-based slidedeck that the user watches while you narrate in the terminal. The slidedeck runs at `http://localhost:8765` and is controlled via MCP tools.

## MCP Tools

| Tool | Purpose |
|------|---------|
| `deck_open(title?)` | Open browser, set deck title |
| `deck_close()` | Clear all slides and reset |
| `slide_add(id, type, content, title?, position?)` | Add a slide |
| `slide_update(id, content?, title?, type?)` | Update a slide |
| `slide_remove(id)` | Remove a slide |
| `slide_navigate(id)` | Navigate browser to a slide |
| `slide_clear()` | Remove all slides, keep deck open |

## Tmux Session Warning

When `deck_open()` returns a WARNING about a missing tmux session, you **must stop
building slides** and inform the user:

1. Explain that the slidedeck has an embedded terminal panel that mirrors the Claude
   Code session, but it requires running inside tmux to work.
2. Tell them the deck is open and will work fine for slides and charts, but the
   built-in terminal panel won't be available in this session.
3. Offer two options:
   - **Continue without terminal support** — the presentation works normally, just
     without the embedded terminal view in the browser.
   - **Restart in tmux** — exit, run `tmux new -s claude`, then start Claude Code
     again. The terminal panel will then work.
4. **Wait for the user's choice** before proceeding with any slides.

Do NOT silently ignore this warning or continue building the presentation.

## Slide Types

### `html` — Raw HTML
Content is inserted directly. Use the CSS classes below to build rich layouts.

### `markdown` — Markdown text
Content is parsed with marked.js and wrapped in `.slide-commentary`.

### `image` — PNG/JPG image
Content = **absolute file path** to the image. The server copies it to `.slidedeck/assets/` automatically.

### `stats` — Raw HTML (stat cards)
Same as `html` but tagged as stats type for sidebar icon differentiation.

### `plotly` — Interactive Plotly chart
Content = JSON string of a Plotly figure: `{"data": [...], "layout": {...}}`

**Plotly tips:**
- Use `paper_bgcolor: "rgba(0,0,0,0)"` and `plot_bgcolor: "rgba(0,0,0,0)"` for theme compatibility
- The client auto-applies dark/light font and grid colors if not set
- Keep `responsive: true` and set height in layout

## CSS Classes for HTML Slides

### Stat Cards
```html
<div class="stats-grid">
  <div class="stat-card">
    <div class="stat-label">LABEL</div>
    <div class="stat-value good">78%</div>  <!-- good|warn|bad|neutral -->
    <div class="stat-detail">detail text</div>
  </div>
</div>
```

### TIR Bar
```html
<div class="tir-bar-container">
  <div class="tir-bar-label">Label</div>
  <div class="tir-bar">
    <div class="tir-segment very-low" style="width: 0.5%"></div>
    <div class="tir-segment low" style="width: 2%">2%</div>
    <div class="tir-segment in-range" style="width: 78%">78%</div>
    <div class="tir-segment high" style="width: 15%">15%</div>
    <div class="tir-segment very-high" style="width: 4.5%">5%</div>
  </div>
</div>
```

### Title Card
```html
<div class="slide-title-card">
  <h1>Title</h1>
  <p class="subtitle">Subtitle</p>
  <div class="date-range">Date range text</div>
</div>
```

### Commentary / Callouts
```html
<div class="slide-commentary">
  <h2>Heading</h2>
  <p>Paragraph with <strong>bold</strong>.</p>
  <div class="highlight">Green callout box</div>
  <div class="warning">Red warning box</div>
</div>
```

### Split Layout
```html
<div class="slide-split">
  <div>Left column content</div>
  <div>Right column content</div>
</div>
```

### Section Title
```html
<div class="slide-section-title">Section Name</div>
```

### Disclaimer
```html
<div class="disclaimer">
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
    <circle cx="12" cy="12" r="10"/><path d="M12 16v-4M12 8h.01"/>
  </svg>
  <div>Disclaimer text here.</div>
</div>
```

## Workflow: Diabetes Data Briefing

1. Fetch data with `/nightscout` or CLI tools
2. `deck_open("Weekly Diabetes Briefing — Feb 25–Mar 3")`
3. Add title slide: `slide_add("title", "html", "<div class='slide-title-card'>...")`
4. Add summary stats: `slide_add("stats", "stats", "<div class='stats-grid'>...")`
5. Generate matplotlib charts → `slide_add("cgm", "image", "/path/to/chart.png")`
6. Add Plotly interactive charts with JSON specs
7. Add analysis in markdown: `slide_add("analysis", "markdown", "## Key Findings\n...")`
8. Add closing/takeaways slide
9. Narrate each slide in the terminal as you go
10. `slide_navigate(id)` to guide the user's view

## Tips

- Add slides progressively — the user sees each one appear in real-time
- Use `slide_navigate()` to direct attention after adding multiple slides
- The building indicator shows automatically when slides are added
- Use short IDs: `title`, `stats`, `cgm-overlay`, `analysis`, `summary`
- For images, generate the chart first (save to project dir), then pass the absolute path
- The browser auto-reconnects if the server restarts
