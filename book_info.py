"""
Book info summarization module.

Fetches compact one-paragraph summaries of books using Claude CLI.
Caches results for future requests.
"""

import json
import subprocess
from pathlib import Path
from typing import Optional

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


def get_ai_prephrase(book_id: str, title: str, author: str, content_sample: str) -> str:
    """
    Get or fetch an intriguing one-sentence hook before reading.

    Args:
        book_id: Unique identifier for the book (for caching)
        title: Book title
        author: Book author
        content_sample: First 1000 characters of book content

    Returns:
        One-sentence intriguing hook (10-20 words)
    """
    # Check cache first
    cache_key = f"{book_id}_prephrase"
    cached = _load_cached_summary(cache_key)
    if cached:
        return cached

    # Fetch from Claude CLI
    try:
        prompt = f"""Write a SHORT, punchy one-sentence summary that makes someone want to read immediately. It should be:
- Intriguing and mysterious (what's the hook?)
- Quick to read (10-20 words max)
- Focus on what makes this book unmissable
- Be vivid and compelling

Book: {title} by {author}

First 1000 characters:
{content_sample}

Provide only the summary, no other text."""

        result = subprocess.run(
            ["claude", "-p", prompt],
            capture_output=True,
            text=True,
            timeout=30
        )

        if result.returncode == 0:
            summary = result.stdout.strip()
            _save_summary(cache_key, summary)
            return summary
        else:
            error_msg = result.stderr.lower() if result.stderr else ""
            if "auth" in error_msg or "unauthorized" in error_msg or "signed in" in error_msg:
                return "Sign in to Claude Code CLI to enable AI Prephrase"
            return "Unable to generate prephrase"

    except subprocess.TimeoutExpired:
        return "Prephrase timeout - try again later"
    except FileNotFoundError:
        return "Claude Code CLI not found"
    except Exception:
        return "Unable to generate prephrase"


def get_ai_conclusion(book_id: str, chapter_content: str, title: str, author: str) -> str:
    """
    Get or fetch a conclusion summarizing key points from a chapter.

    Args:
        book_id: Unique identifier for the book (for caching)
        chapter_content: Content of the current chapter
        title: Book title
        author: Book author

    Returns:
        2-3 sentence conclusion consolidating knowledge
    """
    # Use chapter hash as part of cache key to avoid conflicts
    import hashlib
    content_hash = hashlib.md5(chapter_content[:500].encode()).hexdigest()[:8]
    cache_key = f"{book_id}_conclusion_{content_hash}"
    cached = _load_cached_summary(cache_key)
    if cached:
        return cached

    # Fetch from Claude CLI
    try:
        # Take first 3000 chars of chapter, stripping HTML if present
        chapter_sample = chapter_content[:3000]
        # Remove common HTML tags if present
        import re
        chapter_sample = re.sub(r'<[^>]+>', '', chapter_sample).strip()

        prompt = f"""Write a 2-3 sentence conclusion that consolidates the key knowledge and material from this chapter. It should:
- Summarize the main points and developments
- Connect to the overall narrative or themes
- Help the reader retain and understand what they read
- Be clear and insightful

Book: {title} by {author}

Chapter excerpt (first 2000 chars):
{chapter_sample}

Provide only the conclusion, no other text."""

        result = subprocess.run(
            ["claude", "-p", prompt],
            capture_output=True,
            text=True,
            timeout=30
        )

        if result.returncode == 0:
            conclusion = result.stdout.strip()
            _save_summary(cache_key, conclusion)
            return conclusion
        else:
            error_msg = result.stderr.lower() if result.stderr else ""
            if "auth" in error_msg or "unauthorized" in error_msg or "signed in" in error_msg:
                return "Sign in to Claude Code CLI to enable AI Conclusion"
            return "Unable to generate conclusion"

    except subprocess.TimeoutExpired:
        return "Conclusion timeout - try again later"
    except FileNotFoundError:
        return "Claude Code CLI not found"
    except Exception:
        return "Unable to generate conclusion"


def get_book_summary(book_id: str, title: str, author: str, content_sample: str) -> str:
    """
    Get or fetch a one-paragraph summary of a book using Claude CLI.

    Args:
        book_id: Unique identifier for the book (for caching)
        title: Book title
        author: Book author
        content_sample: First 1000 characters of book content

    Returns:
        One-paragraph summary (3-4 sentences)
    """
    # Check cache first
    cached = _load_cached_summary(book_id)
    if cached:
        return cached

    # Fetch from Claude CLI
    try:
        prompt = f"""Write a SHORT, punchy one-sentence summary that makes someone want to read immediately. It should be:
- Intriguing and mysterious (what's the hook?)
- Quick to read (10-20 words max)
- Focus on what makes this book unmissable
- Be vivid and compelling

Book: {title} by {author}

First 1000 characters:
{content_sample}

Provide only the summary, no other text."""

        result = subprocess.run(
            ["claude", "-p", prompt],
            capture_output=True,
            text=True,
            timeout=30
        )

        if result.returncode == 0:
            summary = result.stdout.strip()
            _save_summary(book_id, summary)
            return summary
        else:
            # Claude CLI auth or error
            error_msg = result.stderr.lower() if result.stderr else ""
            if "auth" in error_msg or "unauthorized" in error_msg or "signed in" in error_msg:
                return "Sign in to Claude Code CLI to enable book summaries"
            return "Unable to generate summary"

    except subprocess.TimeoutExpired:
        return "Summarization timeout - try again later"
    except FileNotFoundError:
        return "Claude Code CLI not found"
    except Exception as e:
        return "Unable to generate summary"
