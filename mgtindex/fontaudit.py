"""Font audit -- catch dropped headings before they cost you a mislabelled ship.

A book's extraction profile is a map from (font, size) to a structural role: heading, body,
or nothing. When a book sets its headings in a font the profile does not list, those headings
are silently DROPPED -- which is exactly how AL2's Tenentsyo lost its name (an AlegreSans
title under a profile that only knows TradeGothic/Walkway), and why AL1 leaks headings too.

This tool makes that class of bug visible. For each book it counts every (font, size) pair,
asks the book's own profile what role it would assign, and flags display-sized text the
profile ignores yet the book uses across many pages -- because large + frequent = structural,
i.e. almost certainly a heading you are throwing away. Free, no model calls.

    python -m mgtindex.fontaudit              # every book
    python -m mgtindex.fontaudit high-guard   # one book, with its full (font,size) table
"""

from __future__ import annotations

import collections
import sys
import tomllib
from pathlib import Path

import fitz

from mgtindex.extract import PROFILES, _role

ROOT = Path(__file__).resolve().parent.parent

# What counts as a real "missed heading" is narrower than "any dropped display text", and the
# difference is what makes this tool usable instead of noisy. A profile intentionally drops
# small text in a heading font -- the stat-block LABELS (TONS, CREW) that share the ship-name
# font but must not become headings. That is by design, not a bug.
#
# The genuine bug -- AL2's Tenentsyo -- is a font the profile captures at NO size, used at
# TITLE size. So flag a (font, size) only when: the profile gives it no role, the font is one
# the profile never treats as a heading at any size, it is title-sized, and it recurs. That
# fires on AL2/AL1 (AlegreSans, never captured, 35pt) and stays silent on JG/SotR (AlegreSans
# IS captured >=22; the small drops are labels) and on CRB/HG (headings are BebasNeue/AgencyFB).
TITLE_SIZE = 24
MIN_PAGES = 3
# A drop cap -- the giant decorative initial letter at a section start -- is title-sized and
# recurs, but it is ONE glyph per page, so chars/pages ~= 1. A real heading carries many
# characters per page it appears on. This ratio is what separates "missed heading" from
# "decorative flourish the profile is right to ignore".
MIN_CHARS_PER_PAGE = 4


def audit(book: dict) -> list[dict]:
    prof = PROFILES[book["profile"]]
    doc = fitz.open(ROOT / book["file"])
    stats: dict = collections.defaultdict(lambda: {"chars": 0, "pages": set()})
    for i, pg in enumerate(doc):
        for bl in pg.get_text("dict")["blocks"]:
            for line in bl.get("lines", []):
                for s in line["spans"]:
                    t = s["text"].strip()
                    if not t:
                        continue
                    st = stats[(s["font"], round(s["size"]))]
                    st["chars"] += len(t)
                    st["pages"].add(i)
    doc.close()
    return [
        {"font": f, "size": sz, "chars": st["chars"], "pages": len(st["pages"]),
         "role": _role(prof, f, sz)}
        for (f, sz), st in stats.items()
    ]


def main() -> None:
    reg = tomllib.loads((ROOT / "books.toml").read_text())["book"]
    want = sys.argv[1] if len(sys.argv) > 1 else None
    books = [b for b in reg if not want or b["id"] == want]
    for b in sorted(books, key=lambda b: b["order"]):
        rows = audit(b)
        captured = {r["font"] for r in rows if r["role"] in ("h2", "h3")}
        # the real bug: a font the profile treats as a heading NOWHERE, used at title size
        missed = sorted(
            (r for r in rows if r["role"] is None and r["font"] not in captured
             and r["size"] >= TITLE_SIZE and r["pages"] >= MIN_PAGES
             and r["chars"] / r["pages"] >= MIN_CHARS_PER_PAGE),  # exclude drop caps
            key=lambda r: (-r["size"], -r["chars"]),
        )
        print(f"\n{'=' * 74}\n{b['siglum']}  [{b['profile']}]  {b['file']}")
        print(f"  heading fonts the profile CAPTURES: {', '.join(sorted(captured)) or '(none)'}")
        if missed:
            print(f"  ⚠ MISSED HEADING FONT(S) -- captured at no size, yet used at >={TITLE_SIZE}pt:")
            print(f"    {'font':30} {'size':>4} {'chars':>8} {'pages':>6}")
            for r in missed:
                print(f"    {r['font']:30} {r['size']:>4} {r['chars']:>8} {r['pages']:>6}")
        else:
            print("  ✓ every title-sized font is one the profile captures")

        if want:  # single-book mode: dump the whole (font, size) table for inspection
            print(f"\n  full (font, size) table for {b['siglum']}  (role=None means dropped):")
            print(f"    {'font':30} {'size':>4} {'chars':>8} {'pages':>6}  role")
            for r in sorted(rows, key=lambda r: (-r["size"], -r["chars"])):
                print(f"    {r['font']:30} {r['size']:>4} {r['chars']:>8} {r['pages']:>6}  {r['role']}")


if __name__ == "__main__":
    main()
