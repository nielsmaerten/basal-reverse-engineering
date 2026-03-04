"""MCP server for the slidedeck presentation layer."""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import webbrowser
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import asdict

from fastmcp import FastMCP

from .state import DeckState, Slide
from .terminal import TerminalManager
from .web import broadcast, set_deck, set_terminal, start_server, stop_server

logging.basicConfig(level=logging.INFO, format="%(name)s: %(message)s")
logger = logging.getLogger("slidedeck")

HOST = "127.0.0.1"
PORT = 8765


@asynccontextmanager
async def lifespan(mcp: FastMCP) -> AsyncIterator[dict]:
    """Start/stop the web server alongside the MCP server."""
    deck = DeckState.load()
    set_deck(deck)
    terminal = TerminalManager(session=os.environ.get("SLIDEDECK_TMUX_SESSION", "claude"))
    set_terminal(terminal)
    runner, site = await start_server(HOST, PORT)
    try:
        yield {"deck": deck, "terminal": terminal}
    finally:
        await terminal.stop()
        await stop_server(runner)


mcp = FastMCP("slidedeck", lifespan=lifespan)


def _deck(ctx=None) -> DeckState:
    """Get the deck state from lifespan context or fallback."""
    if ctx and hasattr(ctx, "request_context") and ctx.request_context:
        return ctx.request_context.lifespan_context["deck"]
    # Fallback: import from web module
    from .web import get_deck
    return get_deck()


# ── Tools ──

@mcp.tool()
async def deck_open(title: str = "Slidedeck") -> str:
    """Open the slidedeck in the browser and optionally set the title.

    Call this at the start of a presentation to launch the browser window.
    If slides already exist from a previous session they will be preserved.
    """
    deck = _deck()
    deck.title = title
    deck.save()
    await broadcast("deck:meta", {"title": title})
    url = f"http://{HOST}:{PORT}"
    try:
        webbrowser.open(url)
    except Exception:
        pass
    return f"Slidedeck opened at {url} with title '{title}'"


@mcp.tool()
async def deck_close() -> str:
    """Clear all slides and reset the deck.

    Use this when the presentation is finished.
    """
    deck = _deck()
    deck.reset()
    await broadcast("deck:cleared", {})
    return "Deck cleared and reset"


@mcp.tool()
async def slide_add(
    id: str,
    type: str,
    content: str,
    title: str = "",
    position: int = 0,
) -> str:
    """Add a new slide to the deck.

    Args:
        id: Unique slide identifier (e.g. 'title', 'cgm-chart', 'summary')
        type: Slide type — one of: html, markdown, image, plotly, stats
        content: Slide content. For 'html'/'stats': raw HTML string.
            For 'markdown': markdown text. For 'image': absolute file path to a
            PNG/JPG (will be copied to assets/). For 'plotly': JSON string of a
            Plotly figure spec ({data: [...], layout: {...}}).
        title: Display title shown in the sidebar thumbnail
        position: 1-based position (0 or omitted = append at end)
    """
    deck = _deck()

    # For image type, import the file and store just the filename
    actual_content = content
    if type == "image":
        filename = deck.import_image(content)
        actual_content = filename

    slide = Slide(
        id=id,
        type=type,
        content=actual_content,
        title=title,
        position=position,
    )
    deck.add_slide(slide)
    await broadcast("slide:added", asdict(slide))
    return f"Slide '{id}' added at position {slide.position}"


@mcp.tool()
async def slide_update(
    id: str,
    content: str | None = None,
    title: str | None = None,
    type: str | None = None,
) -> str:
    """Update an existing slide's content, title, or type.

    Args:
        id: The slide ID to update
        content: New content (see slide_add for format per type).
            For image type, pass the new file path.
        title: New sidebar title
        type: Change the slide type
    """
    deck = _deck()

    # Handle image import on update
    actual_content = content
    slide = deck.get_slide(id)
    target_type = type or (slide.type if slide else None)
    if actual_content is not None and target_type == "image":
        filename = deck.import_image(actual_content)
        actual_content = filename

    updated = deck.update_slide(id, content=actual_content, title=title, type_=type)
    await broadcast("slide:updated", asdict(updated))
    return f"Slide '{id}' updated"


@mcp.tool()
async def slide_remove(id: str) -> str:
    """Remove a slide from the deck.

    Args:
        id: The slide ID to remove
    """
    deck = _deck()
    deck.remove_slide(id)
    await broadcast("slide:removed", {"id": id})
    return f"Slide '{id}' removed"


@mcp.tool()
async def slide_navigate(id: str) -> str:
    """Navigate the browser to a specific slide.

    Args:
        id: The slide ID to navigate to
    """
    deck = _deck()
    slide = deck.get_slide(id)
    if not slide:
        return f"Slide '{id}' not found"
    deck.current_slide_id = id
    deck.save()
    await broadcast("slide:navigate", {"id": id})
    return f"Navigated to slide '{id}'"


@mcp.tool()
async def slide_clear() -> str:
    """Remove all slides but keep the deck open and title intact."""
    deck = _deck()
    deck.clear_slides()
    await broadcast("deck:cleared", {})
    return "All slides cleared"


if __name__ == "__main__":
    mcp.run()
