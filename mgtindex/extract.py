"""Stage 1: deterministic structural extraction.

Turns a PDF into a stream of chunks. A chunk is one semantic unit -- a run of
body paragraphs under a heading, a table, or a sidebar -- carrying the heading
path that leads to it, its printed page, and its bounding box on that page.

No LLM involvement. Page numbers and structure come from here and are never
generated downstream, which is what makes the citations trustworthy.
"""

from __future__ import annotations

import hashlib
import re
import tomllib
from dataclasses import dataclass, field, asdict
from pathlib import Path

import fitz

ROOT = Path(__file__).resolve().parent.parent

# A style profile maps (font, size) onto structural roles. It is per-publisher:
# every current book is Mongoose/InDesign, but a book from another layout house
# will need its own profile rather than silently mis-detecting every heading.
#
# h1 (chapter) is NOT detected by font -- it comes from the PDF's own TOC, which
# is authoritative. Mongoose prints the chapter name as a rotated tab on the outer
# page edge in the same BebasNeue as real headings, so font alone cannot tell a
# section heading from a running head.
PROFILES = {
    # CRB / High Guard / CSC / Companion / Robot Handbook.
    # NOTE the body test must accept Helvetica as well as Arial: High Guard sets ~44% of
    # its body copy in Helvetica, and an Arial-only test silently drops all of it.
    "mongoose-indesign": {
        "h2": lambda f, s: "BebasNeue" in f and s >= 18,
        "h3": lambda f, s: "AgencyFB" in f and s >= 12,
        "body": lambda f, s: ("Arial" in f or "Helvetica" in f) and s < 12,
    },
    # Aliens of Charted Space -- a different layout house entirely: TradeGothic body,
    # WalkwayExpandBlack / TradeGothic-Bold display faces.
    "mongoose-tradegothic": {
        "h2": lambda f, s: "Walkway" in f or ("TradeGothic" in f and s >= 14),
        "h3": lambda f, s: "TradeGothic" in f and "Bold" in f and 11 <= s < 14,
        "body": lambda f, s: "TradeGothic" in f and s < 11,
    },
}

# The text frame, as a fraction of page size. Anything outside it is furniture: the
# rotated chapter tab on the outer edge, the vertical spine, the page folio.
FRAME = (0.065, 0.93, 0.935)  # x0, x1, y1

# The vertical "TRAVELLER" spine: single glyphs stacked in a tall, narrow block.
SPINE = re.compile(r"^[A-Z]$")


@dataclass
class Chunk:
    id: str
    book_id: str
    page: int  # printed page number, not pdf index
    kind: str  # body | table | heading
    path: list[str]  # heading breadcrumb, outermost first
    text: str
    bbox: tuple[float, float, float, float]  # for paragraph-precise hyperlinks


def load_registry() -> list[dict]:
    return tomllib.loads((ROOT / "books.toml").read_text())["book"]


def _role(profile: dict, font: str, size: float) -> str | None:
    for role, test in profile.items():
        if test(font, size):
            return role
    return None


def _is_spine(block: dict) -> bool:
    """Tall, narrow, all single-character lines -> the vertical TRAVELLER spine."""
    x0, y0, x1, y1 = block["bbox"]
    if (x1 - x0) > 30 or (y1 - y0) < 80:
        return False
    chars = [s["text"].strip() for l in block.get("lines", []) for s in l["spans"]]
    return bool(chars) and all(SPINE.match(c) for c in chars if c)


def _columns(blocks: list[dict], page_width: float) -> list[dict]:
    """Reading order for a 2-column layout.

    Full-width blocks (tables, spanning sidebars) act as horizontal BARRIERS: they split
    the page into bands, and within each band you read the whole left column before the
    whole right one. Getting this wrong doesn't corrupt the text -- it corrupts the
    heading-to-body association, so a section heading at the top of the right column
    lands *after* the paragraphs it introduces, and every following chunk inherits a
    stale breadcrumb.
    """
    mid = page_width / 2

    def _display(b) -> bool:
        # A big section title (Mongoose sets these ~50pt) is a barrier, not column
        # content. It is often centred, straddling the column midpoint -- bucket it as
        # "right column" and it sorts AFTER the body text it introduces, so every chunk
        # on the page inherits the previous section's heading.
        return any(
            s["size"] >= 30 for l in b.get("lines", []) for s in l["spans"] if s["text"].strip()
        )

    def _wide(b) -> bool:
        return (b["bbox"][2] - b["bbox"][0]) > page_width * 0.6 or _display(b)

    full = [b for b in blocks if _wide(b)]
    bars = sorted(b["bbox"][1] for b in full)

    def band(y: float) -> int:
        return sum(1 for bar in bars if y >= bar)

    def key(b):
        x0, y0, x1, _ = b["bbox"]
        is_full = _wide(b)
        col = 0 if is_full else (0 if x1 <= mid + 20 else 1)
        # within a band: full-width block first, then left column, then right column
        return (band(y0), 0 if is_full else 1, col, round(y0))

    return sorted(blocks, key=key)


def _chapter_map(doc: fitz.Document, offset: int) -> dict[int, str]:
    """printed page -> chapter title, from the PDF's own outline.

    Level 1 is not always the chapter level. High Guard is a merged PDF whose level-1
    outline reads 'Front Cover.pdf' / 'High Guard_ebook.pdf' / 'Back Cover.pdf' -- its
    real chapters are at level 2. Pick the shallowest level that looks like chapters.
    """
    toc = doc.get_toc()
    level = 1
    for lvl in (1, 2, 3):
        titles = [t for l, t, _ in toc if l == lvl]
        if len(titles) >= 5 and not any(t.lower().endswith(".pdf") for t in titles):
            level = lvl
            break
    tops = [(p - 1 - offset, t) for lvl, t, p in toc if lvl == level]
    by_page: dict[int, str] = {}
    for i, (start, title) in enumerate(tops):
        end = tops[i + 1][0] if i + 1 < len(tops) else doc.page_count
        for pg in range(start, end):
            by_page[pg] = _clean(title)
    return by_page


def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def extract_book(book: dict) -> list[Chunk]:
    profile = PROFILES[book["profile"]]
    doc = fitz.open(ROOT / book["file"])
    chapters = _chapter_map(doc, book["offset"])
    chunks: list[Chunk] = []
    path: list[str] = []  # running heading breadcrumb, carries across pages
    buf: list[str] = []
    buf_bbox: list[tuple] = []
    buf_page: int | None = None

    def flush():
        nonlocal buf, buf_bbox, buf_page
        text = " ".join(buf).strip()
        # rejoin words hyphenated across a line break
        text = re.sub(r"(\w)-\s+(\w)", r"\1\2", text)
        text = re.sub(r"\s+", " ", text)
        if len(text) >= 40 and buf_page is not None:
            x0 = min(b[0] for b in buf_bbox)
            y0 = min(b[1] for b in buf_bbox)
            x1 = max(b[2] for b in buf_bbox)
            y1 = max(b[3] for b in buf_bbox)
            cid = hashlib.sha256(
                f"{book['id']}|{buf_page}|{'>'.join(path)}|{text}".encode()
            ).hexdigest()[:16]
            chunks.append(
                Chunk(cid, book["id"], buf_page, "body", list(path), text, (x0, y0, x1, y1))
            )
        buf, buf_bbox, buf_page = [], [], None

    chapter: str | None = None
    for idx in range(doc.page_count):
        page = doc[idx]
        printed = idx - book["offset"]
        pw, ph = page.rect.width, page.rect.height
        fx0, fx1, fy1 = FRAME[0] * pw, FRAME[1] * pw, FRAME[2] * ph

        if chapters.get(printed) != chapter:
            flush()
            chapter = chapters.get(printed)
            path[:] = [chapter] if chapter else []

        blocks = [b for b in page.get_text("dict")["blocks"] if "lines" in b]
        blocks = [b for b in blocks if not _is_spine(b)]

        for block in _columns(blocks, page.rect.width):
            for line in block["lines"]:
                spans = [s for s in line["spans"] if s["text"].strip()]
                if not spans:
                    continue
                x0, y0, x1, _ = line["bbox"]
                if x0 < fx0 or x1 > fx1 or y0 > fy1:
                    continue  # furniture: chapter tab, spine, folio

                # A heading is a line that is ENTIRELY set in a heading font. Equipment
                # lists use the same bold face for run-in item names --
                # "Medicinal Drugs (TL5+): Includes vaccines..." -- and treating those
                # as headings poisons the breadcrumb for every chunk that follows.
                # Require the heading font to own essentially the whole line.
                text = _clean("".join(s["text"] for s in spans))
                nchars = sum(len(s["text"]) for s in spans) or 1
                roles = {}
                for s in spans:
                    r = _role(profile, s["font"], s["size"])
                    roles[r] = roles.get(r, 0) + len(s["text"])
                role = max(roles, key=roles.get)
                if role in ("h2", "h3") and roles[role] / nchars < 0.9:
                    role = "body"  # bold run-in lead-in, not a heading

                if role in ("h2", "h3"):
                    flush()
                    depth = int(role[1]) - 1  # h2 -> 1, h3 -> 2
                    path[:] = path[:depth] + [text]
                elif role == "body":
                    if buf_page is None:
                        buf_page = printed
                    buf.append(text)
                    buf_bbox.append(line["bbox"])
        # a chunk does not span pages: flush at the page boundary so every chunk
        # has exactly one page number and one bbox to hyperlink to
        flush()

    return chunks


if __name__ == "__main__":
    import json
    import sys

    want = sys.argv[1] if len(sys.argv) > 1 else "core-rulebook"
    book = next(b for b in load_registry() if b["id"] == want)
    chunks = extract_book(book)
    out = ROOT / "build" / f"{want}.chunks.jsonl"
    out.parent.mkdir(exist_ok=True)
    with out.open("w") as fh:
        for c in chunks:
            fh.write(json.dumps(asdict(c)) + "\n")
    print(f"{book['siglum']}: {len(chunks)} chunks -> {out}")
