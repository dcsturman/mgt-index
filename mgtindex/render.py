"""Stage 4: typeset the index and bind it into a hyperlinked PDF.

Two things worth knowing about the output:

1. Every page number is a live internal link, and it does not just land on the page --
   it lands on the PARAGRAPH. Stage 1 kept each chunk's bounding box, so the GoTo
   destination carries a y-coordinate. This is strictly better than a printed index can
   be, and it is why the clickable PDF is the primary artifact and print the fallback.

2. Bold page numbers mark the primary definition; plain ones are secondary references.
   Aliases become "see" cross-references, filed alphabetically, as a human indexer sets
   them.
"""

from __future__ import annotations

import json
import tomllib
from collections import defaultdict
from pathlib import Path

import fitz

from mgtindex.canon import blocking_key

ROOT = Path(__file__).resolve().parent.parent
BOOKS = {b["id"]: b for b in tomllib.loads((ROOT / "books.toml").read_text())["book"]}

W, H = 612.0, 792.0
MARGIN, GUTTER = 46.0, 22.0
COLW = (W - 2 * MARGIN - GUTTER) / 2
SIZE, LEAD, INDENT = 8.0, 9.6, 10.0
BODY, BOLD, ITAL = "helv", "hebo", "heit"

Frag = tuple[str, str, object]  # (text, font, link_target | None)


class Layout:
    def __init__(self, doc: fitz.Document, title: str):
        self.doc, self.title = doc, title
        self.pages: list[fitz.Page] = []
        self._new_page()

    def _new_page(self):
        p = self.doc.new_page(width=W, height=H)
        p.insert_text((MARGIN, MARGIN + 10), self.title, fontname=BOLD, fontsize=13)
        p.draw_line((MARGIN, MARGIN + 16), (W - MARGIN, MARGIN + 16), width=0.5)
        self.pages.append(p)
        self.page, self.col, self.y = p, 0, MARGIN + 32
        self.pno = self.doc.page_count - 1

    def _advance(self):
        self.y += LEAD
        if self.y > H - MARGIN:
            if self.col == 0:
                self.col, self.y = 1, MARGIN + 32
            else:
                self._new_page()

    def x0(self) -> float:
        return MARGIN + self.col * (COLW + GUTTER)

    def entry(self, frags: list[Frag], indent: float = 0.0) -> list[tuple]:
        """Draw one index entry, wrapping inside its column. Returns [(rect, target)]."""
        x = self.x0() + indent
        anchors = []
        for text, font, target in frags:
            w = fitz.get_text_length(text, fontname=font, fontsize=SIZE)
            if x + w > self.x0() + COLW and x > self.x0() + indent:
                self._advance()
                x = self.x0() + indent + INDENT  # hanging indent for the runover
            self.page.insert_text((x, self.y), text, fontname=font, fontsize=SIZE)
            if target is not None:
                anchors.append((self.pno, fitz.Rect(x, self.y - SIZE + 1, x + w, self.y + 1.5), target))
            x += w
        self._advance()
        return anchors


def refs(pages: dict[int, bool], key: str, target: dict) -> list[Frag]:
    """', 12, 14' -- bold where the page defines the concept. Each number is a link."""
    out: list[Frag] = []
    for pg in sorted(pages):
        out.append((", ", BODY, None))
        out.append((str(pg), BOLD if pages[pg] else BODY, (pg, target.get((key, pg), 60.0))))
    return out


def build(book_id: str = "core-rulebook"):
    vocab = json.loads((ROOT / "build" / "vocab.json").read_text())
    entries = [json.loads(l) for l in (ROOT / "build" / f"{book_id}.entries.jsonl").open()]
    chunks = {c["id"]: c for c in (json.loads(l) for l in (ROOT / "build" / f"{book_id}.chunks.jsonl").open())}
    book = BOOKS[book_id]

    # (cluster, page) -> y of the passage, so a link lands on the paragraph not the page
    target: dict[tuple[str, int], float] = {}
    for e in entries:
        c = chunks.get(e["chunk_id"])
        if c:
            target.setdefault((blocking_key(e.get("parent") or e["term"]), e["page"]), c["bbox"][1])

    subs: dict[str, dict[str, dict[int, bool]]] = defaultdict(lambda: defaultdict(dict))
    for e in entries:
        if e.get("parent"):
            k = blocking_key(e["parent"])
            pg = subs[k][e["term"].strip()]
            pg[e["page"]] = pg.get(e["page"], False) or e["role"] == "primary"

    seq: list[tuple[str, dict]] = []
    for key, v in vocab.items():
        seq.append((v["canonical"], {"kind": "entry", "key": key, "v": v}))
        for a in v.get("aliases", []):
            if a.strip() and a.strip().lower() != v["canonical"].lower():
                seq.append((a.strip(), {"kind": "see", "to": v["canonical"]}))
    seq.sort(key=lambda t: (t[0].lower(), t[0]))

    doc = fitz.open(ROOT / book["file"])
    body_pages = doc.page_count
    lay = Layout(doc, f"{book['siglum']} — INDEX")
    anchors: list[tuple] = []
    lines = 0

    for label, item in seq:
        if item["kind"] == "see":
            lay.entry([(label, BODY, None), (", see ", ITAL, None), (item["to"], BODY, None)])
            lines += 1
            continue

        v, key = item["v"], item["key"]
        senses = v["senses"]

        def pagemap(s):
            m = {s["primary_page"]: True}
            for p in s.get("other_pages", []):
                m.setdefault(p, False)
            return m

        if len(senses) == 1 and not senses[0]["qualifier"]:
            anchors += lay.entry([(v["canonical"], BODY, None)] + refs(pagemap(senses[0]), key, target))
            lines += 1
        else:
            lay.entry([(v["canonical"], BODY, None)])
            lines += 1
            for s in senses:
                anchors += lay.entry(
                    [(s["qualifier"] or "general", ITAL, None)] + refs(pagemap(s), key, target),
                    indent=INDENT,
                )
                lines += 1

        for sub in sorted(subs.get(key, {}), key=str.lower):
            anchors += lay.entry(
                [(sub, BODY, None)] + refs(subs[key][sub], key, target), indent=INDENT
            )
            lines += 1

    made = 0
    for pno, rect, (pg, y) in anchors:
        dest = pg + book["offset"]
        if 0 <= dest < body_pages:
            doc[pno].insert_link(
                {"kind": fitz.LINK_GOTO, "from": rect, "page": dest,
                 "to": fitz.Point(36, max(0.0, y - 14))}
            )
            made += 1

    out = ROOT / "build" / f"{book['siglum']}-indexed.pdf"
    doc.save(out, garbage=3, deflate=True)
    print(f"{lines} index lines over {len(lay.pages)} pages, {made} hyperlinks")
    print(f"-> {out}  ({out.stat().st_size/1e6:.1f} MB)")
    doc.close()


if __name__ == "__main__":
    build()
