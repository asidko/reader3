"""
Book info summarization module.

Fetches compact one-paragraph summaries of books using Claude API.
Caches results for future requests.
"""

import json
import os
from pathlib import Path
from typing import Optional

import anthropic

# Cache directory for book summaries
CACHE_DIR = Path.home() / ".reader3_cache"
CACHE_DIR.mkdir(exist_ok=True)


def _get_cache_path(book_id: str) -> Path:
    """Get the cache file path for a book summary."""
    return CACHE_DIR / f"{book_id}_summary.json"


def _load_cached_summary(book_id: str) -> Optional[str]:
    """Load cached summary if it exists."""
    cache_path = _get_cache_path(book_id)
    if cache_path.exists():
        try:
            with open(cache_path) as f:
                data = json.load(f)
                return data.get("summary")
        except Exception:
            pass
    return None


def _save_summary(book_id: str, summary: str) -> None:
    """Save summary to cache."""
    cache_path = _get_cache_path(book_id)
    try:
        with open(cache_path, "w") as f:
            json.dump({"summary": summary}, f)
    except Exception:
        pass


def get_book_summary(book_id: str, title: str, author: str, content_sample: str) -> str:
    """
    Get or fetch a one-paragraph summary of a book.

    Args:
        book_id: Unique identifier for the book (for caching)
        title: Book title
        author: Book author
        content_sample: First 1000 characters of book content

    Returns:
        One-paragraph summary (2-3 sentences)
    """
    # Check cache first
    cached = _load_cached_summary(book_id)
    if cached:
        return cached

    # Fetch from Claude
    try:
        # Use API key from environment if available
        api_key = os.getenv("ANTHROPIC_API_KEY")
        client = anthropic.Anthropic(api_key=api_key)
        message = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=256,
            messages=[
                {
                    "role": "user",
                    "content": f"""Summarize this book in one compact paragraph (2-3 sentences).

Book: {title} by {author}

First 1000 characters:
{content_sample}

Provide only the summary, no other text.""",
                }
            ],
        )

        summary = message.content[0].text.strip()
        _save_summary(book_id, summary)
        return summary

    except Exception as e:
        # Return user-friendly message if no API key
        error_msg = str(e).lower()
        if "auth" in error_msg or "api_key" in error_msg:
            return "Set ANTHROPIC_API_KEY to see book summaries"
        return "Unable to generate summary"
