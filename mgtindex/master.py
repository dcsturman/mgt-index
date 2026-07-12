"""Stage 4: the MASTER INDEX -- one index across all seven books.

Mongoose spreads a single topic over many books, so the whole value is in gathering
them: looking up 'sensors' should show you the Core Rulebook equipment entry AND the
High Guard ship component in one place.

Output is a standalone PDF that links OUT to the seven originals with GoToR (remote)
links, landing on the paragraph (Stage 1 kept each chunk's bounding box).

TWO THINGS TO KNOW ABOUT GoToR:
  * The links are by RELATIVE PATH. Keep this PDF in the same directory as the books.
  * Viewer support varies (Acrobat honours them; Preview is patchy; browsers mostly
    ignore them). So the siglum and page are always PRINTED AS TEXT -- if a viewer
    drops the link, you still have a perfectly good ordinary index.
"""

from __future__ import annotations

import json
import tomllib
from collections import defaultdict
from pathlib import Path

import fitz

from mgtindex.canon import blocking_key
from mgtindex.subents import attach, promote_orphans
from mgtindex.ships import apply as apply_ships

ROOT = Path(__file__).resolve().parent.parent

def sort_key(t: str) -> str:
    """Index sort ignores leading punctuation.

    "'trator" is a real Traveller creature, not junk -- the apostrophe is part of the
    name. Sorting on the raw first character files it under '#', where nobody will look.
    A reader looks for it under T.
    """
    s = t.lstrip("'\u2019\u2018\"/([-\u2014\u2013 .").lower()
    return s or t.lower()


def bucket(t: str) -> str:
    """The A-Z heading a term belongs under."""
    s = sort_key(t)
    c = s[:1].upper()
    return c if c.isalpha() else "#"

_REG = tomllib.loads((ROOT / "books.toml").read_text())["book"]
BOOKS = {b["id"]: b for b in _REG}
ORDER = {b["id"]: b["order"] for b in _REG}

W, H = 612.0, 792.0
MARGIN, GUTTER = 44.0, 20.0
COLW = (W - 2 * MARGIN - GUTTER) / 2
SIZE, LEAD, INDENT = 8.0, 9.5, 10.0
BODY, BOLD, ITAL = "helv", "hebo", "heit"
GREY = (0.42, 0.42, 0.42)
BLACK = (0, 0, 0)


MONGOOSE_NOTICE = (
    "The Traveller, 2300AD, Twilight: 2000 and Dark Conspiracy games in all forms are owned by "
    "Mongoose Publishing. Copyright 1977 – 2025 Mongoose Publishing. Traveller is a registered "
    "trademark of Mongoose Publishing. Mongoose Publishing permits web sites and fanzines for this "
    "game, provided it contains this notice, that Mongoose Publishing is notified, and subject to a "
    "withdrawal of permission on 90 days notice. The contents of this site are for personal, "
    "non-commercial use only. Any use of Mongoose Publishing’s copyrighted material or trademarks "
    "anywhere on this web site and its files should not be viewed as a challenge to those copyrights "
    "or trademarks. In addition, any program/articles/file on this site cannot be republished or "
    "distributed without the consent of the author who contributed it."
)


FOLD = {"\u2014": "-", "\u2013": "-", "\u2019": "'", "\u2018": "'",
        "\u201c": '"', "\u201d": '"', "\u2026": "...", "\u00a0": " ", "\u00b7": "-"}


def ascii_fold(t: str) -> str:
    """PDF base-14 Helvetica cannot encode em-dash / curly quote / ellipsis -- they come
    out as a mid-dot. Fold to ASCII rather than ship mojibake."""
    for k, v in FOLD.items():
        t = t.replace(k, v)
    return t


def _flow(page, text, x, y, width, size=8.0, font=BODY, colour=BLACK, lead=10.0):
    """Dumb word-wrapper. Returns the y after the last line."""
    text = ascii_fold(text)
    words, line = text.split(), ""
    for w in words:
        trial = f"{line} {w}".strip()
        if fitz.get_text_length(trial, fontname=font, fontsize=size) > width and line:
            page.insert_text((x, y), line, fontname=font, fontsize=size, color=colour)
            y += lead
            line = w
        else:
            line = trial
    if line:
        page.insert_text((x, y), line, fontname=font, fontsize=size, color=colour)
        y += lead
    return y


def front_matter(doc, stats: dict):
    """Title, licence notice, exactly-what-was-indexed table, and how the links work.

    The sources table is not decoration. These are content-hashed, dated files; a
    page reference is only meaningful against the exact PDF it was generated from, and
    a reader with a different printing needs to know that immediately.
    """
    p = doc.new_page(width=W, height=H)
    x, right = MARGIN, W - MARGIN
    tw = right - x

    p.insert_text((x, 92), "MONGOOSE TRAVELLER", fontname=BOLD, fontsize=26)
    p.insert_text((x, 120), "2nd Edition - Master Index", fontname=BOLD, fontsize=19)
    p.draw_line((x, 134), (right, 134), width=1.0)

    y = _flow(
        p,
        "A single index across seven Mongoose Traveller rulebooks. Mongoose spreads the rules for "
        "one topic across many books; this index gathers them, so looking up a term shows every "
        "book that covers it. Page numbers in bold mark the passage that defines the concept; "
        "plain numbers are secondary references. Book sigla are listed below.",
        x, 156, tw, size=9.5, lead=12,
    )

    y += 14
    p.insert_text((x, y), "HOW THE LINKS WORK", fontname=BOLD, fontsize=10)
    y += 14
    y = _flow(
        p,
        "Every page number in this index is a live cross-document link that opens the relevant "
        "book at the relevant page — in fact at the relevant paragraph. For that to work:",
        x, y, tw, size=9, lead=11,
    )
    y += 4
    for bullet in [
        "1.  Keep this PDF in the SAME FOLDER as the seven book PDFs. The links are relative paths.",
        "2.  Do not rename the book files. The exact filenames the links expect are listed in the "
        "table below — a renamed file will simply not open.",
        "3.  Use a reader that supports remote GoTo links. Adobe Acrobat honours them. macOS "
        "Preview is inconsistent. Most web browsers ignore them entirely.",
        "4.  If your reader ignores the links, nothing is lost — the siglum and page number are "
        "printed as ordinary text, so this still works as a normal printed index.",
    ]:
        y = _flow(p, bullet, x + 10, y, tw - 10, size=9, lead=11) + 2

    y += 12
    p.insert_text((x, y), "BOOKS INDEXED", fontname=BOLD, fontsize=10)
    y += 6
    p.draw_line((x, y), (right, y), width=0.5)
    y += 12
    for hx, ht in [(x, "SIGLUM"), (x + 46, "BOOK"), (x + 226, "PP.")]:
        p.insert_text((hx, y), ht, fontname=BOLD, fontsize=7.2, color=GREY)
    p.insert_text((x + 252, y), "FILENAME - MUST MATCH EXACTLY", fontname=BOLD, fontsize=7.2, color=GREY)
    y += 11
    for b in sorted(_REG, key=lambda b: b["order"]):
        p.insert_text((x, y), b["siglum"], fontname=BOLD, fontsize=8.6)
        # shrink the title to fit its column rather than let it collide with PP.
        title, ts = ascii_fold(b["title"]), 7.8
        while fitz.get_text_length(title, fontname=BODY, fontsize=ts) > 172 and ts > 5.4:
            ts -= 0.2
        p.insert_text((x + 46, y), title, fontname=BODY, fontsize=ts)
        p.insert_text((x + 226, y), str(b["pages"]), fontname=BODY, fontsize=7.8)
        # The filename IS the contract that makes a link resolve. Shrink to fit, never clip.
        fs = 7.2
        while fitz.get_text_length(b["file"], fontname=BODY, fontsize=fs) > (right - x - 252) and fs > 4.4:
            fs -= 0.2
        p.insert_text((x + 252, y), b["file"], fontname=BODY, fontsize=fs)
        y += 9.5
        p.insert_text((x + 252, y), f"sha-256 {b['sha256']}", fontname=BODY, fontsize=5.0, color=GREY)
        y += 11.5
    p.draw_line((x, y), (right, y), width=0.5)
    y += 12
    y = _flow(
        p,
        f"Index generated from these exact files. {stats['entries']:,} source references were "
        f"reduced to {stats['headwords']:,} headwords and {stats['senses']:,} distinct senses. "
        "A page reference is only valid against the printing listed above — check the SHA-256 "
        "if you are unsure you have the same file.",
        x, y, tw, size=8, lead=10, colour=GREY,
    )

    y += 18
    p.insert_text((x, y), "COPYRIGHT AND PERMISSION", fontname=BOLD, fontsize=10)
    y += 14
    y = _flow(p, MONGOOSE_NOTICE, x, y, tw, size=7.6, lead=9.4, colour=GREY)
    y += 10
    _flow(
        p,
        "This index is a fan-made, non-commercial reference work. It is not sold and carries no "
        "charge. It contains no rules text — only terms and page references pointing into books "
        "you must already own. All rules content remains the property of Mongoose Publishing.",
        x, y, tw, size=7.6, lead=9.4, colour=GREY,
    )


class Layout:
    def __init__(self, doc):
        self.doc = doc
        self._new_page()

    def _new_page(self):
        p = self.doc.new_page(width=W, height=H)
        p.insert_text((MARGIN, MARGIN + 10), "MONGOOSE TRAVELLER 2e - MASTER INDEX",
                      fontname=BOLD, fontsize=11)
        p.draw_line((MARGIN, MARGIN + 16), (W - MARGIN, MARGIN + 16), width=0.5)
        self.pno = self.doc.page_count - 1
        self.page, self.col, self.y = p, 0, MARGIN + 32

    def _advance(self):
        self.y += LEAD
        if self.y > H - MARGIN:
            if self.col == 0:
                self.col, self.y = 1, MARGIN + 32
            else:
                self._new_page()

    def x0(self):
        return MARGIN + self.col * (COLW + GUTTER)

    def rule(self, title: str):
        """Start a fresh page with a section banner -- used for the ship appendix."""
        self._new_page()
        self.page.insert_text((MARGIN, self.y + 6), ascii_fold(title), fontname=BOLD, fontsize=13)
        self.page.draw_line((MARGIN, self.y + 12), (W - MARGIN, self.y + 12), width=0.8)
        self.y += 30

    def entry(self, frags, indent=0.0):
        """frags = [(text, font, colour, link|None)]. Wraps within the column."""
        x = self.x0() + indent
        anchors = []
        for text, font, colour, link in frags:
            text = ascii_fold(text)
            w = fitz.get_text_length(text, fontname=font, fontsize=SIZE)
            if x + w > self.x0() + COLW and x > self.x0() + indent:
                self._advance()
                x = self.x0() + indent + INDENT
            self.page.insert_text((x, self.y), text, fontname=font, fontsize=SIZE, color=colour)
            if link:
                anchors.append((self.pno, fitz.Rect(x, self.y - SIZE + 1, x + w, self.y + 1.5), link))
            x += w
        self._advance()
        return anchors


def load():
    vocab = json.loads((ROOT / "build" / "vocab.json").read_text())
    entries, chunks = [], {}
    for b in _REG:
        ef = ROOT / "build" / f"{b['id']}.entries.jsonl"
        cf = ROOT / "build" / f"{b['id']}.chunks.jsonl"
        if not ef.exists():
            continue
        entries += [json.loads(l) for l in ef.open()]
        for line in cf.open():
            c = json.loads(line)
            chunks[c["id"]] = c
    return vocab, entries, chunks


def build():
    vocab, entries, chunks = load()

    # (cluster, book, page) -> y of the passage, for paragraph-precise remote links
    target: dict[tuple, float] = {}
    for e in entries:
        c = chunks.get(e["chunk_id"])
        if c:
            k = (blocking_key(e.get("parent") or e["term"]), e["book_id"], e["page"])
            target.setdefault(k, c["bbox"][1])

    # Subentries hang off a SENSE, not off the headword: 'Corporate' belongs to
    # Agent-the-career, not to Agent-the-disease-vector, and printing both in one
    # alphabetical pile discards the split Stage 3 found. See mgtindex/subents.py.
    promote_orphans(vocab, entries)
    # ships BEFORE subentries: folding 'Merchant Cruiser' into 'Leviathan-class Merchant
    # Cruiser' carries that headword's subentries with it, and attach() must see the result.
    apply_ships(vocab, entries)
    subs = attach(vocab, entries, chunks)

    # a ship family's members ARE its subentries; they are synthesised, so attach() never saw them
    for key, v in vocab.items():
        if not v.get("ship_members"):
            continue
        lst = subs.setdefault(key, [[] for _ in v["senses"]])
        lst += [[] for _ in range(len(v["senses"]) - len(lst))]
        for si, members in v["ship_members"]:
            lst[si] = members + lst[si]

    page_top: dict[tuple, float] = {}
    for c in chunks.values():
        k = (c["book_id"], c["page"])
        page_top[k] = min(page_top.get(k, 1e9), c["bbox"][1])

    def refs(pairs: dict[tuple, bool], key: str):
        """', CRB 116, 181; HG 34' -- book-grouped, sigla greyed, each page a link."""
        by_book: dict[str, list[tuple[int, bool]]] = defaultdict(list)
        for (bid, pg), prim in pairs.items():
            by_book[bid].append((pg, prim))
        out = []
        first = True
        for bid in sorted(by_book, key=lambda b: ORDER.get(b, 99)):
            out.append((", " if first else "; ", BODY, BLACK, None))
            out.append((BOOKS[bid]["siglum"] + " ", ITAL, GREY, None))
            first = False
            for i, (pg, prim) in enumerate(sorted(set(by_book[bid]))):
                if i:
                    out.append((", ", BODY, BLACK, None))
                y = target.get((key, bid, pg)) or page_top.get((bid, pg), 60.0)
                out.append((str(pg), BOLD if prim else BODY, BLACK, (bid, pg, y)))
        return out

    # Every headword, by cluster key AND by surface form. An alias that collides with a
    # real headword must NOT become a cross-reference -- otherwise the index says
    # "Arakoine class Strike Cruiser, see Strike Cruiser" three lines above the genuine
    # "Arakoine-class Strike Cruiser, HG 251" entry, and the reader is sent in a circle.
    headwords = {v["canonical"].strip().lower() for v in vocab.values()}
    headkeys = set(vocab.keys())

    seq, emitted = [], set()
    for key, v in vocab.items():
        seq.append((v["canonical"], {"kind": "entry", "key": key, "v": v}))
    for key, v in vocab.items():
        for a in v.get("aliases", []):
            a = a.strip()
            al = a.lower()
            if not a or al in headwords or al in emitted:
                continue
            if blocking_key(a) in headkeys and blocking_key(a) != key:
                continue           # normalises onto a different real headword
            if blocking_key(a) == blocking_key(v["canonical"]):
                continue           # differs only by punctuation/case/plural
            if blocking_key(a) == blocking_key(v["canonical"]) and al == v["canonical"].lower():
                continue
            emitted.add(al)
            seq.append((a, {"kind": "see", "to": v["canonical"]}))
    seq.sort(key=lambda t: (sort_key(t[0]), t[0]))

    doc = fitz.open()
    front_matter(doc, {
        "entries": len(entries),
        "headwords": len(vocab),
        "senses": sum(len(v["senses"]) for v in vocab.values()),
    })
    lay = Layout(doc)
    anchors, lines = [], 0

    for label, item in seq:
        if item["kind"] == "see":
            lay.entry([(label, BODY, BLACK, None), (", see ", ITAL, GREY, None),
                       (item["to"], BODY, BLACK, None)])
            lines += 1
            continue

        v, key = item["v"], item["key"]
        senses = v["senses"]

        def pairs(s):
            m = {(s["primary"]["book"], s["primary"]["page"]): True}
            for r in s.get("refs", []):
                m.setdefault((r["book"], r["page"]), False)
            return m

        mine = subs.get(key) or [[] for _ in senses]

        def subentries(i: int, indent: float):
            nonlocal anchors, lines
            for x in mine[i]:
                anchors += lay.entry(
                    [(x["t"], BODY, BLACK, None)] + refs(x["refs"], key), indent=indent
                )
                lines += 1

        if len(senses) == 1 and not senses[0]["qualifier"]:
            anchors += lay.entry([(v["canonical"], BODY, BLACK, None)] + refs(pairs(senses[0]), key))
            lines += 1
            subentries(0, INDENT)
        else:
            lay.entry([(v["canonical"], BODY, BLACK, None)])
            lines += 1
            for i, s in enumerate(senses):
                anchors += lay.entry(
                    [(s["qualifier"] or "general", ITAL, BLACK, None)] + refs(pairs(s), key),
                    indent=INDENT,
                )
                lines += 1
                subentries(i, INDENT * 2)  # nested under its own sense, not adrift

    # APPENDIX: every design in one place, by displacement. The A-Z answers "where is the
    # Tigress?"; this answers the question actually asked at the table -- "what have I got at
    # around 400 tons?" -- which an alphabetical index structurally cannot.
    fleet = sorted(
        ((k, v) for k, v in vocab.items() if v.get("ship") and v.get("spec")),
        key=lambda kv: (kv[1]["tons"], kv[1]["canonical"].lower()),
    )
    if fleet:
        lay.rule("SPACECRAFT DESIGNS - BY DISPLACEMENT")
        lines += 1
        for key, v in fleet:
            name = v["canonical"].rsplit(" (", 1)[0]
            pairs = {(s["book"], s["page"]): True for s in v["spec"]}
            anchors += lay.entry(
                [(f"{v['tons']:,} t", BODY, GREY, None), ("  " + name, BODY, BLACK, None)]
                + refs(pairs, key)
            )
            lines += 1

    # A GoToR destination is written as /XYZ with y measured from the BOTTOM of the
    # TARGET page -- but PyMuPDF converts `to` using the height of the page the link
    # sits on (this index, 792pt). The books are not all 792pt tall: Aliens vol 1 has
    # 816pt pages, CSC 780, the Robot Handbook 776. Without compensating, every link
    # into those books lands tens of points off. Pre-distort `to` so the value that
    # actually reaches the file is right.
    heights: dict[tuple[str, int], float] = {}
    for b in _REG:
        d = fitz.open(ROOT / b["file"])
        for i in range(d.page_count):
            heights[(b["id"], i)] = d[i].rect.height
        d.close()

    made = 0
    for pno, rect, (bid, pg, y) in anchors:
        b = BOOKS[bid]
        dest = pg + b["offset"]          # printed page -> 0-based pdf index
        if not (0 <= dest < b["pages"]):
            continue
        th = heights.get((bid, dest), H)
        to_y = H - th + max(0.0, y - 14)  # so that (H - to_y) == (th - y_target)
        doc[pno].insert_link(
            {
                "kind": fitz.LINK_GOTOR,
                "from": rect,
                "file": b["file"],       # RELATIVE path: keep the index beside the books
                "page": dest,
                "to": fitz.Point(36, to_y),
            }
        )
        made += 1

    out = ROOT / "MGT2 Master Index (generated).pdf"
    doc.save(out, garbage=3, deflate=True)
    print(f"{lines} index lines over {doc.page_count} pages, {made} cross-file links")
    print(f"-> {out}  ({out.stat().st_size/1e6:.1f} MB)")
    doc.close()


if __name__ == "__main__":
    build()
