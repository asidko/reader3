#!/usr/bin/env python3
"""
Unified EPUB reader: processes books and runs the server.
Usage: python book.py <file.epub>
"""

import os
import pickle
import shutil
import sys
import socket
from dataclasses import dataclass, field
from typing import List, Dict, Optional
from datetime import datetime
from urllib.parse import unquote
from functools import lru_cache

import ebooklib
from ebooklib import epub
from bs4 import BeautifulSoup, Comment
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.templating import Jinja2Templates
import uvicorn

from claude_code_detect import get_claude_code_status
from book_info import (
    get_book_summary,
    get_ai_prephrase,
    get_ai_conclusion,
    get_paragraph_summaries,
)

# --- Data structures ---

@dataclass
class ChapterContent:
    id: str
    href: str
    title: str
    content: str
    text: str
    order: int


@dataclass
class TOCEntry:
    title: str
    href: str
    file_href: str
    anchor: str
    children: List['TOCEntry'] = field(default_factory=list)


@dataclass
class BookMetadata:
    title: str
    language: str
    authors: List[str] = field(default_factory=list)
    description: Optional[str] = None
    publisher: Optional[str] = None
    date: Optional[str] = None
    identifiers: List[str] = field(default_factory=list)
    subjects: List[str] = field(default_factory=list)


@dataclass
class Book:
    metadata: BookMetadata
    spine: List[ChapterContent]
    toc: List[TOCEntry]
    images: Dict[str, str]
    source_file: str
    processed_at: str
    version: str = "3.0"


# --- Utilities ---

def clean_html_content(soup: BeautifulSoup) -> BeautifulSoup:
    for tag in soup(['script', 'style', 'iframe', 'video', 'nav', 'form', 'button']):
        tag.decompose()

    for comment in soup.find_all(string=lambda text: isinstance(text, Comment)):
        comment.extract()

    for tag in soup.find_all('input'):
        tag.decompose()

    return soup


def extract_plain_text(soup: BeautifulSoup) -> str:
    text = soup.get_text(separator=' ')
    return ' '.join(text.split())


def parse_toc_recursive(toc_list, depth=0) -> List[TOCEntry]:
    result = []

    for item in toc_list:
        if isinstance(item, tuple):
            section, children = item
            entry = TOCEntry(
                title=section.title,
                href=section.href,
                file_href=section.href.split('#')[0],
                anchor=section.href.split('#')[1] if '#' in section.href else "",
                children=parse_toc_recursive(children, depth + 1)
            )
            result.append(entry)
        elif isinstance(item, epub.Link):
            entry = TOCEntry(
                title=item.title,
                href=item.href,
                file_href=item.href.split('#')[0],
                anchor=item.href.split('#')[1] if '#' in item.href else ""
            )
            result.append(entry)
        elif isinstance(item, epub.Section):
            entry = TOCEntry(
                title=item.title,
                href=item.href,
                file_href=item.href.split('#')[0],
                anchor=item.href.split('#')[1] if '#' in item.href else ""
            )
            result.append(entry)

    return result


def get_fallback_toc(book_obj) -> List[TOCEntry]:
    toc = []
    for item in book_obj.get_items():
        if item.get_type() == ebooklib.ITEM_DOCUMENT:
            name = item.get_name()
            title = item.get_name().replace('.html', '').replace('.xhtml', '').replace('_', ' ').title()
            toc.append(TOCEntry(title=title, href=name, file_href=name, anchor=""))
    return toc


def extract_metadata_robust(book_obj) -> BookMetadata:
    def get_list(key):
        data = book_obj.get_metadata('DC', key)
        return [x[0] for x in data] if data else []

    def get_one(key):
        data = book_obj.get_metadata('DC', key)
        return data[0][0] if data else None

    return BookMetadata(
        title=get_one('title') or "Untitled",
        language=get_one('language') or "en",
        authors=get_list('creator'),
        description=get_one('description'),
        publisher=get_one('publisher'),
        date=get_one('date'),
        identifiers=get_list('identifier'),
        subjects=get_list('subject')
    )


# --- Main EPUB Processing ---

def process_epub(epub_path: str, output_dir: str) -> Book:
    print(f"Loading {epub_path}...")
    book = epub.read_epub(epub_path)

    metadata = extract_metadata_robust(book)

    if os.path.exists(output_dir):
        shutil.rmtree(output_dir)
    images_dir = os.path.join(output_dir, 'images')
    os.makedirs(images_dir, exist_ok=True)

    print("Extracting images...")
    image_map = {}

    for item in book.get_items():
        if item.get_type() in (ebooklib.ITEM_IMAGE, ebooklib.ITEM_COVER):
            original_fname = os.path.basename(item.get_name())
            safe_fname = "".join([c for c in original_fname if c.isalpha() or c.isdigit() or c in '._-']).strip()

            local_path = os.path.join(images_dir, safe_fname)
            with open(local_path, 'wb') as f:
                f.write(item.get_content())

            rel_path = f"images/{safe_fname}"
            image_map[item.get_name()] = rel_path
            image_map[original_fname] = rel_path

    print("Parsing Table of Contents...")
    toc_structure = parse_toc_recursive(book.toc)
    if not toc_structure:
        print("Warning: Empty TOC, building fallback from Spine...")
        toc_structure = get_fallback_toc(book)

    print("Processing chapters...")
    spine_chapters = []

    for i, spine_item in enumerate(book.spine):
        item_id, _ = spine_item
        item = book.get_item_with_id(item_id)

        if not item:
            continue

        if item.get_type() == ebooklib.ITEM_DOCUMENT:
            raw_content = item.get_content().decode('utf-8', errors='ignore')
            soup = BeautifulSoup(raw_content, 'html.parser')

            for img in soup.find_all('img'):
                src = img.get('src', '')
                if not src: continue

                src_decoded = unquote(src)
                filename = os.path.basename(src_decoded)

                if src_decoded in image_map:
                    img['src'] = image_map[src_decoded]
                elif filename in image_map:
                    img['src'] = image_map[filename]

            soup = clean_html_content(soup)

            body = soup.find('body')
            if body:
                final_html = "".join([str(x) for x in body.contents])
            else:
                final_html = str(soup)

            chapter = ChapterContent(
                id=item_id,
                href=item.get_name(),
                title=f"Section {i+1}",
                content=final_html,
                text=extract_plain_text(soup),
                order=i
            )
            spine_chapters.append(chapter)

    final_book = Book(
        metadata=metadata,
        spine=spine_chapters,
        toc=toc_structure,
        images=image_map,
        source_file=os.path.basename(epub_path),
        processed_at=datetime.now().isoformat()
    )

    return final_book


def save_to_pickle(book: Book, output_dir: str):
    p_path = os.path.join(output_dir, 'book.pkl')
    with open(p_path, 'wb') as f:
        pickle.dump(book, f)
    print(f"Saved to {p_path}")


# --- FastAPI Server ---

app = FastAPI()
templates = Jinja2Templates(directory="templates")

BOOKS_DIR = "."
CLAUDE_CODE_STATUS = get_claude_code_status()


@lru_cache(maxsize=1)
def load_book_cached(folder_name: str) -> Optional[Book]:
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
    """Get the single book folder."""
    if os.path.exists(BOOKS_DIR):
        books = [item for item in os.listdir(BOOKS_DIR)
                 if item.endswith("_data") and os.path.isdir(item)]
        if len(books) == 1:
            return books[0]
    return None


@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    book_folder = _get_book_folder()
    if not book_folder:
        raise HTTPException(status_code=404, detail="No book found")
    return await read_chapter(request=request, book_id=book_folder, chapter_index=0)


@app.get("/read/{book_id}", response_class=HTMLResponse)
async def redirect_to_first_chapter(book_id: str):
    return await read_chapter(book_id=book_id, chapter_index=0)


@app.get("/read/{book_id}/{chapter_index}", response_class=HTMLResponse)
async def read_chapter(request: Request, book_id: str, chapter_index: int):
    book = load_book_cached(book_id)
    if not book:
        raise HTTPException(status_code=404, detail="Book not found")

    if chapter_index < 0 or chapter_index >= len(book.spine):
        raise HTTPException(status_code=404, detail="Chapter not found")

    current_chapter = book.spine[chapter_index]

    prev_idx = chapter_index - 1 if chapter_index > 0 else None
    next_idx = chapter_index + 1 if chapter_index < len(book.spine) - 1 else None

    content_sample = book.spine[0].content[:1000] if book.spine else ""
    summary = get_book_summary(
        book_id,
        book.metadata.title,
        ", ".join(book.metadata.authors),
        content_sample
    )

    chapter_sample = current_chapter.content[:1000] if current_chapter.content else ""
    ai_prephrase = get_ai_prephrase(
        book_id,
        book.metadata.title,
        ", ".join(book.metadata.authors),
        chapter_sample,
        summary
    )

    ai_conclusion = get_ai_conclusion(
        book_id,
        current_chapter.content,
        book.metadata.title,
        ", ".join(book.metadata.authors),
        summary
    )

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
    safe_book_id = os.path.basename(book_id)
    safe_image_name = os.path.basename(image_name)

    img_path = os.path.join(BOOKS_DIR, safe_book_id, "images", safe_image_name)

    if not os.path.exists(img_path):
        raise HTTPException(status_code=404, detail="Image not found")

    return FileResponse(img_path)


@app.get("/{image_name:path}")
async def serve_any_image(image_name: str):
    if not any(image_name.lower().endswith(ext) for ext in ('.jpg', '.jpeg', '.png', '.gif', '.webp', '.svg')):
        raise HTTPException(status_code=404, detail="Not an image")

    book_folder = _get_book_folder()
    if not book_folder:
        raise HTTPException(status_code=404, detail="Book not found")

    safe_image_name = os.path.basename(image_name)

    img_path = os.path.join(BOOKS_DIR, book_folder, "images", safe_image_name)

    if not os.path.exists(img_path):
        raise HTTPException(status_code=404, detail="Image not found")

    return FileResponse(img_path)


# --- Utilities ---

def find_available_port(start_port: int = 8123, max_attempts: int = 10) -> int:
    """Find an available port starting from start_port."""
    for port in range(start_port, start_port + max_attempts):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(('127.0.0.1', port))
                return port
        except OSError:
            continue
    raise RuntimeError(f"No available ports found in range {start_port}-{start_port + max_attempts}")


# --- CLI ---

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python book.py <file.epub>")
        sys.exit(1)

    epub_file = sys.argv[1]
    assert os.path.exists(epub_file), "File not found."
    out_dir = os.path.splitext(epub_file)[0] + "_data"

    # Process the book
    book_obj = process_epub(epub_file, out_dir)
    save_to_pickle(book_obj, out_dir)

    print("\n--- Summary ---")
    print(f"Title: {book_obj.metadata.title}")
    print(f"Authors: {', '.join(book_obj.metadata.authors)}")
    print(f"Chapters: {len(book_obj.spine)}")
    print(f"Images: {len(book_obj.images)}")

    # Find available port (prefer 8123)
    port = find_available_port()
    print(f"\nStarting server at http://127.0.0.1:{port}")
    print("Press Ctrl+C to stop\n")
    uvicorn.run(app, host="127.0.0.1", port=port)
