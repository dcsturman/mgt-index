"""Quick human-readable dump of Stage 2 output, so the rubric can be argued with.

This is NOT the real renderer -- there's no canonicalization yet, so expect the same
concept to appear under several names. That's Stage 3's job. What you're reviewing here
is: did it find the right THINGS, at the right density?
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SIGLA = {b["id"]: b["siglum"] for b in __import__("tomllib").loads((ROOT / "books.toml").read_text())["book"]}


def main(path: Path):
    entries = [json.loads(l) for l in path.open()]

    # group by top-level term; subentries nest under their parent
    tops: dict[str, dict] = defaultdict(lambda: {"pages": set(), "subs": defaultdict(set), "aliases": set()})
    for e in entries:
        key = (e.get("parent") or e["term"]).strip()
        node = tops[key]
        cite = (SIGLA.get(e["book_id"], "?"), e["page"], e["role"] == "primary")
        if e.get("parent"):
            node["subs"][e["term"].strip()].add(cite)
        else:
            node["pages"].add(cite)
            node["aliases"].update(a for a in e.get("aliases", []) if a)

    def fmt(cites) -> str:
        # one ref per (book, page); bold it if ANY entry called this page primary
        best: dict[tuple[str, int], bool] = {}
        for sig, page, primary in cites:
            best[(sig, page)] = best.get((sig, page), False) or primary
        return ", ".join(
            f"**{page}**" if best[(sig, page)] else str(page)
            for sig, page in sorted(best)
        )

    for term in sorted(tops, key=str.lower):
        node = tops[term]
        al = f"  *(also: {', '.join(sorted(node['aliases']))})*" if node["aliases"] else ""
        pages = fmt(node["pages"]) if node["pages"] else ""
        print(f"{term}, {pages}{al}" if pages else f"{term}{al}")
        for sub in sorted(node["subs"], key=str.lower):
            print(f"    {sub}, {fmt(node['subs'][sub])}")

    n_sub = sum(len(n["subs"]) for n in tops.values())
    print(f"\n--- {len(tops)} headwords, {n_sub} subentries, {len(entries)} raw entries ---",
          file=sys.stderr)


if __name__ == "__main__":
    main(ROOT / "build" / (sys.argv[1] if len(sys.argv) > 1 else "core-rulebook.entries.jsonl"))
