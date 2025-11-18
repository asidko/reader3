"""
Book info summarization module.

Fetches compact summaries and AI context for books using Claude CLI.
Caches results for future requests.
"""

import asyncio
import hashlib
import json
import re
import subprocess
from pathlib import Path
from typing import Optional

# Precompile regex patterns for performance
_PARAGRAPH_PATTERN = re.compile(r'<p[^>]*>(.*?)</p>', re.DOTALL)
_HTML_TAG_PATTERN = re.compile(r'<[^>]+>')

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


def _fetch_from_claude(prompt: str) -> Optional[str]:
    """Fetch a response from Claude Code CLI.

    Args:
        prompt: The prompt to send to Claude

    Returns:
        The response text, or None if the request failed
    """
    try:
        result = subprocess.run(
            ["claude", "-p", prompt],
            capture_output=True,
            text=True,
            timeout=30
        )

        if result.returncode == 0:
            return result.stdout.strip()

        error_msg = result.stderr.lower() if result.stderr else ""
        if "auth" in error_msg or "unauthorized" in error_msg or "signed in" in error_msg:
            return "Sign in to Claude Code CLI to enable this feature"
        return None

    except subprocess.TimeoutExpired:
        return "Request timeout - try again later"
    except FileNotFoundError:
        return "Claude Code CLI not found"
    except Exception:
        return None


def get_ai_prephrase(book_id: str, title: str, author: str, content_sample: str, book_summary: str = "") -> str:
    """
    Get or fetch an intriguing one-sentence hook before reading.

    Args:
        book_id: Unique identifier for the book (for caching)
        title: Book title
        author: Book author
        content_sample: First 1000 characters of book content
        book_summary: Optional summary of the book for context

    Returns:
        One-sentence intriguing hook (10-20 words)
    """
    # Check cache first
    cache_key = f"{book_id}_prephrase"
    cached = _load_cached_summary(cache_key)
    if cached:
        return cached

    # Build prompt with book context
    context = f"Book overview: {book_summary}\n\n" if book_summary else ""
    prompt = f"""Write a SHORT, punchy one-sentence hook that makes someone DESPERATE to read this chapter. It should:
- Extract a specific intriguing detail, event, or character moment from the content
- Raise a compelling question or hint at conflict/tension/mystery
- Use vivid, sensory language (not generic)
- Be 10-20 words max
- Focus on what actually happens in this text, not the book premise

Book: {title} by {author}

{context}Chapter content sample:
{content_sample}

Provide ONLY the one-sentence hook, no other text."""

    # Fetch from Claude CLI
    result = _fetch_from_claude(prompt)
    if result:
        _save_summary(cache_key, result)
        return result
    return "Unable to generate prephrase"


def get_ai_conclusion(book_id: str, chapter_content: str, title: str, author: str, book_summary: str = "") -> str:
    """
    Get or fetch a conclusion summarizing key points from a chapter.

    Args:
        book_id: Unique identifier for the book (for caching)
        chapter_content: Content of the current chapter
        title: Book title
        author: Book author
        book_summary: Optional summary of the book for context

    Returns:
        2-3 sentence conclusion consolidating knowledge
    """
    # Use chapter hash as part of cache key to avoid conflicts
    content_hash = hashlib.md5(chapter_content[:500].encode()).hexdigest()[:8]
    cache_key = f"{book_id}_conclusion_{content_hash}"
    cached = _load_cached_summary(cache_key)
    if cached:
        return cached

    # Take first 3000 chars of chapter, stripping HTML if present
    chapter_sample = chapter_content[:3000]
    chapter_sample = re.sub(r'<[^>]+>', '', chapter_sample).strip()

    # Build prompt with book context
    context = f"Book overview: {book_summary}\n\n" if book_summary else ""
    prompt = f"""Write a 2-3 sentence conclusion that consolidates the key knowledge and material from this chapter. It should:
- Summarize the main points and developments
- Connect to the overall narrative or themes
- Help the reader retain and understand what they read
- Be clear and insightful

Book: {title} by {author}

{context}Chapter excerpt (first 2000 chars):
{chapter_sample}

Provide only the conclusion, no other text."""

    # Fetch from Claude CLI
    result = _fetch_from_claude(prompt)
    if result:
        _save_summary(cache_key, result)
        return result
    return "Unable to generate conclusion"


def get_book_summary(book_id: str, title: str, author: str, content_sample: str) -> str:
    """
    Get or fetch an engaging summary of a book using Claude CLI.

    Args:
        book_id: Unique identifier for the book (for caching)
        title: Book title
        author: Book author
        content_sample: First 1000 characters of book content

    Returns:
        Engaging, conversational summary (2-3 sentences)
    """
    # Check cache first
    cached = _load_cached_summary(book_id)
    if cached:
        return cached

    # Build prompt
    prompt = f"""Write a 2-3 sentence summary that makes this book sound absolutely irresistible. It should:
- Be vivid, conversational, and exciting (not formal or dull)
- Capture the core tension or fascination that hooks readers
- Use strong verbs and concrete imagery, not generic descriptions
- Sound like you're recommending it to a friend, not writing a textbook
- Focus on the experience and feeling, not plot mechanics

Book: {title} by {author}

Sample text:
{content_sample}

Provide ONLY the summary, no other text."""

    # Fetch from Claude CLI
    result = _fetch_from_claude(prompt)
    if result:
        _save_summary(book_id, result)
        return result
    return "Unable to generate summary"


def _split_into_paragraph_groups(content: str, min_length: int = 500, max_groups: int = 10) -> list[str]:
    """Split HTML into groups with guaranteed minimum length, capped at max_groups.

    Args:
        content: HTML chapter content
        min_length: Minimum characters per group (hard floor)
        max_groups: Maximum number of groups to create (cap LLM requests)

    Returns:
        List of group HTML strings
    """
    p_matches = list(_PARAGRAPH_PATTERN.finditer(content))
    if not p_matches:
        return [content.strip()] if content.strip() else []

    # Extract and filter non-empty paragraphs
    paragraphs = []
    for match in p_matches:
        clean = _HTML_TAG_PATTERN.sub('', match.group(1)).strip()
        if clean:
            paragraphs.append(match.group(0))

    if not paragraphs:
        return []

    # Calculate total content and target group length
    total_length = sum(len(_HTML_TAG_PATTERN.sub('', p)) for p in paragraphs)
    target_length = max(min_length, total_length // max_groups)

    # Group paragraphs to meet target length
    groups = []
    current_group = []
    current_length = 0

    for para_html in paragraphs:
        para_length = len(_HTML_TAG_PATTERN.sub('', para_html))
        current_group.append(para_html)
        current_length += para_length

        # Flush when we hit target length and haven't hit max groups yet
        if (current_length >= target_length and len(groups) < max_groups - 1) or current_length >= target_length * 1.5:
            groups.append('\n'.join(current_group))
            current_group = []
            current_length = 0

    # Add remaining paragraphs to last group
    if current_group:
        groups.append('\n'.join(current_group))

    return groups[:max_groups]


def _get_paragraph_group_summary(group_text: str, book_title: str = "", book_author: str = "") -> Optional[str]:
    """Get an intriguing 2-4 word teaser for a paragraph group (shown before reading).

    Args:
        group_text: HTML text to summarize
        book_title: Book title for context
        book_author: Book author for context
    """
    # Clean HTML and strip
    clean_text = _HTML_TAG_PATTERN.sub('', group_text).strip()

    if not clean_text or len(clean_text) < 20:
        return None

    # Generate cache key from content hash
    content_hash = hashlib.md5(clean_text[:200].encode()).hexdigest()[:8]
    cache_key = f"para_summary_{content_hash}"

    # Check cache
    cached = _load_cached_summary(cache_key)
    if cached:
        return cached

    # Build context string
    context = ""
    if book_title and book_author:
        context = f"Book: {book_title} by {book_author}\n\n"
    elif book_title:
        context = f"Book: {book_title}\n\n"
    elif book_author:
        context = f"Book by {book_author}\n\n"

    # Build prompt for intriguing teaser
    prompt = f"""Create an intriguing 2-4 word teaser that makes someone curious to read this section.
Use vivid verbs, tension, or mystery. Make it a hook, not just a summary.
No punctuation. Just key words.

{context}Passage:
{clean_text[:400]}

ONLY the 2-4 words, nothing else."""

    result = _fetch_from_claude(prompt)
    if result:
        _save_summary(cache_key, result)
        return result

    return None


async def get_paragraph_summaries(
    content: str,
    book_title: str = "",
    book_author: str = ""
) -> dict[int, str]:
    """Get summaries for all paragraph groups in parallel.

    Args:
        content: HTML chapter content
        book_title: Book title for context
        book_author: Book author for context

    Returns:
        Dict mapping group index to summary text (max 10 groups)
    """
    groups = _split_into_paragraph_groups(content)

    if not groups:
        return {}

    # Create tasks with book context
    async def get_summary(i: int, group_text: str) -> tuple[int, Optional[str]]:
        result = await asyncio.to_thread(
            _get_paragraph_group_summary,
            group_text,
            book_title,
            book_author
        )
        return (i, result)

    # Run all summaries in true parallel using gather
    results = await asyncio.gather(*[
        get_summary(i, group_text)
        for i, group_text in enumerate(groups)
    ])

    # Build result dict, filtering None values
    return {i: summary for i, summary in results if summary}
