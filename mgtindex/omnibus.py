"""Stage 4b: the OMNIBUS -- all seven books in one PDF, with the master index bound in.

Why this exists: macOS Preview is sandboxed and will not follow a cross-document (GoToR)
link into a file it was not explicitly opened with. It reports "you don't have permission
to view it" even when the file is right there, unquarantined, and world-readable. That is
not a bug we can fix in the PDF -- Acrobat honours GoToR, Preview simply refuses.

An omnibus sidesteps it entirely: concatenate the books, and every reference becomes an
INTERNAL link, which every viewer on earth handles. The originals are not modified.

Paragraph-precision is preserved: destinations carry a y-coordinate from the chunk bbox
Stage 1 recorded. Note the books are NOT all the same page height (Aliens vol 1 is 816pt,
CSC 780, Robot Handbook 776) and a PDF destination measures y from the BOTTOM of the
target page -- so `to` is pre-distorted to compensate, exactly as in master.py.
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

import fitz

from mgtindex.canon import blocking_key
from mgtindex.master import (
    BLACK, BOLD, BODY, BOOKS, GREY, H, ITAL, ORDER, W, _REG,
    Layout, front_matter, load,
)

ROOT = Path(__file__).resolve().parent.parent


def build():
    vocab, entries, chunks = load()

    # 1. concatenate the books, remembering where each one starts
    doc = fitz.open()
    start: dict[str, int] = {}
    height: dict[tuple[str, int], float] = {}
    toc = []
    for b in sorted(_REG, key=lambda b: b["order"]):
        src = fitz.open(ROOT / b["file"])
        start[b["id"]] = doc.page_count
        toc.append([1, f"{b['siglum']} — {b['title']}", doc.page_count + 1])
        for i in range(src.page_count):
            height[(b["id"], i)] = src[i].rect.height
        doc.insert_pdf(src)
        src.close()
        print(f"  {b['siglum']:<4} pp {start[b['id']]+1}–{doc.page_count}")
    body_pages = doc.page_count

    def omni(bid: str, printed: int) -> int | None:
        """printed page in a book -> 0-based page index in the omnibus."""
        idx = printed + BOOKS[bid]["offset"]
        if not (0 <= idx < BOOKS[bid]["pages"]):
            return None
        return start[bid] + idx

    # 2. same index data as the standalone master index
    target: dict[tuple, float] = {}
    for e in entries:
        c = chunks.get(e["chunk_id"])
        if c:
            target.setdefault(
                (blocking_key(e.get("parent") or e["term"]), e["book_id"], e["page"]), c["bbox"][1]
            )

    subs: dict[str, dict[str, dict[tuple, bool]]] = defaultdict(lambda: defaultdict(dict))
    for e in entries:
        if e.get("parent"):
            k = blocking_key(e["parent"])
            d = subs[k][e["term"].strip()]
            ref = (e["book_id"], e["page"])
            d[ref] = d.get(ref, False) or e["role"] == "primary"

    def refs(pairs, key):
        by_book = defaultdict(list)
        for (bid, pg), prim in pairs.items():
            by_book[bid].append((pg, prim))
        out, first = [], True
        for bid in sorted(by_book, key=lambda b: ORDER.get(b, 99)):
            out.append((", " if first else "; ", BODY, BLACK, None))
            out.append((BOOKS[bid]["siglum"] + " ", ITAL, GREY, None))
            first = False
            for i, (pg, prim) in enumerate(sorted(set(by_book[bid]))):
                if i:
                    out.append((", ", BODY, BLACK, None))
                y = target.get((key, bid, pg), 60.0)
                out.append((str(pg), BOLD if prim else BODY, BLACK, (bid, pg, y)))
        return out

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
                continue
            emitted.add(al)
            seq.append((a, {"kind": "see", "to": v["canonical"]}))
    seq.sort(key=lambda t: (t[0].lower(), t[0]))

    # 3. colophon + index, appended after the books
    front_matter(doc, {
        "entries": len(entries),
        "headwords": len(vocab),
        "senses": sum(len(v["senses"]) for v in vocab.values()),
    })
    toc.append([1, "MASTER INDEX", body_pages + 1])
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

        if len(senses) == 1 and not senses[0]["qualifier"]:
            anchors += lay.entry([(v["canonical"], BODY, BLACK, None)] + refs(pairs(senses[0]), key))
            lines += 1
        else:
            lay.entry([(v["canonical"], BODY, BLACK, None)])
            lines += 1
            for s in senses:
                anchors += lay.entry(
                    [(s["qualifier"] or "general", ITAL, BLACK, None)] + refs(pairs(s), key),
                    indent=10.0,
                )
                lines += 1
        for sub in sorted(subs.get(key, {}), key=str.lower):
            anchors += lay.entry([(sub, BODY, BLACK, None)] + refs(subs[key][sub], key), indent=10.0)
            lines += 1

    # 4. internal links -- these work in EVERY viewer, Preview included
    made = 0
    for pno, rect, (bid, pg, y) in anchors:
        dest = omni(bid, pg)
        if dest is None:
            continue
        th = height.get((bid, pg + BOOKS[bid]["offset"]), H)
        to_y = H - th + max(0.0, y - 14)  # compensate for differing page heights
        doc[pno].insert_link(
            {"kind": fitz.LINK_GOTO, "from": rect, "page": dest, "to": fitz.Point(36, to_y)}
        )
        made += 1

    doc.set_toc(toc)
    out = ROOT / "MGT2 Omnibus + Master Index.pdf"
    doc.save(out, garbage=3, deflate=True)
    print(f"\n{doc.page_count} pages ({body_pages} of books + {doc.page_count-body_pages} of index)")
    print(f"{lines} index lines, {made} INTERNAL links")
    print(f"-> {out}  ({out.stat().st_size/1e6:.0f} MB)")
    doc.close()


if __name__ == "__main__":
    build()
