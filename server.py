import os
import pickle
from functools import lru_cache
from typing import Optional

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from reader3 import Book, BookMetadata, ChapterContent, TOCEntry
from claude_code_detect import get_claude_code_status
from book_info import (
    get_book_summary,
    get_ai_prephrase,
    get_ai_conclusion,
    get_paragraph_summaries,
)

app = FastAPI()
templates = Jinja2Templates(directory="templates")

# Where are the book folders located?
BOOKS_DIR = "."

# Get Claude Code status once at startup
CLAUDE_CODE_STATUS = get_claude_code_status()

@lru_cache(maxsize=1)
def load_book_cached(folder_name: str) -> Optional[Book]:
    """
    Loads the book from the pickle file.
    Cached so we don't re-read the disk on every click.
    """
    file_path = os.path.join(BOOKS_DIR, folder_name, "book.pkl")
    if not os.path.exists(file_path):
        return None

    try:
        with open(file_path, "rb") as f:
            book = pickle.load(f)
        return book
    except Exception as e:
        print(f"Error loading book {folder_name}: {e}")
        return None

def _get_book_folder() -> Optional[str]:
    """Get the single book folder. Returns None if not found or multiple exist."""
    if os.path.exists(BOOKS_DIR):
        books = [item for item in os.listdir(BOOKS_DIR)
                 if item.endswith("_data") and os.path.isdir(item)]
        if len(books) == 1:
            return books[0]
    return None

@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    """Root endpoint - redirect to the book."""
    book_folder = _get_book_folder()
    if not book_folder:
        raise HTTPException(status_code=404, detail="No book found. Run: uv run reader3.py <book.epub>")
    return await read_chapter(request=request, book_id=book_folder, chapter_index=0)

@app.get("/read/{book_id}", response_class=HTMLResponse)
async def redirect_to_first_chapter(book_id: str):
    """Helper to just go to chapter 0."""
    return await read_chapter(book_id=book_id, chapter_index=0)

@app.get("/read/{book_id}/{chapter_index}", response_class=HTMLResponse)
async def read_chapter(request: Request, book_id: str, chapter_index: int):
    """The main reader interface."""
    book = load_book_cached(book_id)
    if not book:
        raise HTTPException(status_code=404, detail="Book not found")

    if chapter_index < 0 or chapter_index >= len(book.spine):
        raise HTTPException(status_code=404, detail="Chapter not found")

    current_chapter = book.spine[chapter_index]

    # Calculate Prev/Next links
    prev_idx = chapter_index - 1 if chapter_index > 0 else None
    next_idx = chapter_index + 1 if chapter_index < len(book.spine) - 1 else None

    # Get book summary for context
    content_sample = book.spine[0].content[:1000] if book.spine else ""
    summary = get_book_summary(
        book_id,
        book.metadata.title,
        ", ".join(book.metadata.authors),
        content_sample
    )

    # Get AI Prephrase (before chapter)
    # Use current chapter content, not book sample
    chapter_sample = current_chapter.content[:1000] if current_chapter.content else ""
    ai_prephrase = get_ai_prephrase(
        book_id,
        book.metadata.title,
        ", ".join(book.metadata.authors),
        chapter_sample,
        summary
    )

    # Get AI Conclusion (after chapter)
    ai_conclusion = get_ai_conclusion(
        book_id,
        current_chapter.content,
        book.metadata.title,
        ", ".join(book.metadata.authors),
        summary
    )

    # Get paragraph summaries in parallel with book context
    paragraph_summaries = await get_paragraph_summaries(
        current_chapter.content,
        book.metadata.title,
        ", ".join(book.metadata.authors)
    )

    return templates.TemplateResponse("reader.html", {
        "request": request,
        "book": book,
        "current_chapter": current_chapter,
        "chapter_index": chapter_index,
        "book_id": book_id,
        "prev_idx": prev_idx,
        "next_idx": next_idx,
        "claude_code_enabled": CLAUDE_CODE_STATUS["enabled"],
        "book_summary": summary,
        "ai_prephrase": ai_prephrase,
        "ai_conclusion": ai_conclusion,
        "paragraph_summaries": paragraph_summaries
    })

@app.get("/read/{book_id}/images/{image_name}")
async def serve_image(book_id: str, image_name: str):
    """
    Serves images specifically for a book.
    Supports both structured paths and loose filenames.
    """
    # Security check: ensure book_id is clean
    safe_book_id = os.path.basename(book_id)
    safe_image_name = os.path.basename(image_name)

    img_path = os.path.join(BOOKS_DIR, safe_book_id, "images", safe_image_name)

    if not os.path.exists(img_path):
        raise HTTPException(status_code=404, detail="Image not found")

    return FileResponse(img_path)

@app.get("/{image_name:path}")
async def serve_any_image(image_name: str):
    """
    Catch-all for loose image filenames (e.g., from SVG xlink:href).
    Only matches image extensions (.jpg, .png, .gif, .webp, .svg).
    """
    # Only serve image files
    if not any(image_name.lower().endswith(ext) for ext in ('.jpg', '.jpeg', '.png', '.gif', '.webp', '.svg')):
        raise HTTPException(status_code=404, detail="Not an image")

    # Get current book
    book_folder = _get_book_folder()
    if not book_folder:
        raise HTTPException(status_code=404, detail="Book not found")

    # Security check
    safe_image_name = os.path.basename(image_name)

    img_path = os.path.join(BOOKS_DIR, book_folder, "images", safe_image_name)

    if not os.path.exists(img_path):
        raise HTTPException(status_code=404, detail="Image not found")

    return FileResponse(img_path)

if __name__ == "__main__":
    import uvicorn
    print("Starting server at http://127.0.0.1:8123")
    uvicorn.run(app, host="127.0.0.1", port=8123)
