"""Stage 3: canonicalization.

This is where an LLM-generated term list becomes an index.

Two failure modes to fix, and they pull in opposite directions:

  MERGE   the same concept named several ways -- 'Contact' / 'Contacts' / 'contacts',
          'Power Requirements' / 'power requirements'. Collapse to one headword.

  SPLIT   several concepts sharing a word. 'sensors' is a skill (p67), a piece of
          personal kit (p116), a vehicle component (p143), and a ship component
          (p160, p181). Collapsing those would destroy information; they need
          disambiguating subentries.

Blindly deduping does the first and botches the second, which is why this needs a model
rather than a fuzzy-match loop. The signal that makes it tractable is the heading
breadcrumb Stage 1 attached to every chunk: 'Equipment > Sensors' vs 'Spacecraft
Construction > Sensors' is what tells the senses apart.

The resulting vocabulary is APPEND-MOSTLY. Once 'jump drive' is canonical, adding a new
book must not rename it -- otherwise every page ref in a printed index shifts and the
whole document churns. New books propose new terms; settled terms are frozen.
"""

from __future__ import annotations

import json
import re
import time
import tomllib
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from mgtindex.backends import Vertex

ROOT = Path(__file__).resolve().parent.parent
VOCAB = ROOT / "build" / "vocab.json"
_REG = tomllib.loads((ROOT / "books.toml").read_text())["book"]
SIGLA = {b["id"]: b["siglum"] for b in _REG}
ORDER = {b["id"]: b["order"] for b in _REG}

SYSTEM = """You are canonicalizing headwords for a printed back-of-book index.

You are given every occurrence of a group of similar-looking index terms: the exact term
the indexer wrote, the printed page, whether they thought that page DEFINED the concept,
and the heading path the passage sat under.

Decide whether these occurrences are ONE concept or SEVERAL.

MERGE them when they are the same concept spelled differently — 'Contact' / 'Contacts',
'Power Requirements' / 'power requirements'. Emit one sense, with no qualifier.

SPLIT them when they are different concepts that happen to share a word. This is common
and you must not miss it. 'sensors' under 'Skills' is a SKILL; under 'Equipment' it is a
piece of personal kit; under 'Spacecraft Construction' it is a starship component. Those
are three senses, and a reader looking for one does not want the others. Give each sense
a short disambiguating qualifier ('skill', 'personal equipment', 'spacecraft component')
which will be printed as a subentry.

THE HEADING PATH IS YOUR BEST EVIDENCE. Trust it over the term's spelling.

You are indexing SEVEN books at once, so the same term appears across several of them.
Occurrences are tagged with a book: CRB (Core Rulebook), HG (High Guard), CSC (Central
Supply Catalogue), TC (Traveller Companion), RH (Robot Handbook), AL1/AL2 (Aliens of
Charted Space). Mongoose spreads one topic over many books, and the whole point of this
index is to gather them: a reader looking up 'sensors' wants the CRB equipment entry AND
the High Guard ship-component entry.

So DO NOT split senses merely because two occurrences are in different books. Split only
when the concepts are genuinely different things. The same rule expanded in a supplement
is ONE sense with several page references.

NEVER WRITE A PAGE NUMBER. Each occurrence below has an id. You assign occurrence ids to
senses; the book and page are looked up from those ids afterwards. Every id you use must
be one that appears in the list, and every id must be assigned to exactly one sense.

For each sense, nominate exactly ONE occurrence as `primary`: the single page a reader must
turn to in order to actually learn that sense of the thing. The rest of that sense's
occurrences are secondary references.

DECIDE THAT BY READING THE PASSAGE. Every occurrence below quotes the text it came from.
A DEFINITION tells you what the thing IS and how it works — it introduces the term, states
its rules, gives its statistics or its table. A MENTION uses the term while assuming you
already know it, modifies a rule stated elsewhere, or merely lists the thing in passing.

Each occurrence also carries a flag an earlier indexer set: DEFINES or mentions. TREAT THAT
FLAG AS WEAK EVIDENCE ONLY. It was assigned by a reader who could see just a few paragraphs
at a time and had no idea what the rest of the book contained, so it claims DEFINES far too
often — the majority of the occurrences you are about to see claim it. You can see all the
passages side by side, which that reader could not. WHERE THE FLAG AND THE TEXT DISAGREE,
THE TEXT WINS. Where several occurrences all claim DEFINES, ignore the flag completely and
pick the one whose text actually does the defining.

Use the text to check your sense assignments as well. If a passage's content does not match
the sense you were about to file it under, file it under the sense it does match — or open
a new sense for it.

Choose the canonical form a reader would look under. Prefer the singular, and normal
book-index capitalisation: lowercase for common concepts ('jump drive', 'armour'),
capitalised only for proper nouns and formal game terms ('Effect', 'Dice Modifier',
'Third Imperium'). Everything else the group was called becomes an alias."""

SCHEMA = {
    "type": "object",
    "properties": {
        "canonical": {"type": "string"},
        "aliases": {"type": "array", "items": {"type": "string"}},
        "senses": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "qualifier": {"type": "string"},
                    "primary": {"type": "integer", "description": "occurrence id, not a page"},
                    "others": {
                        "type": "array",
                        "items": {"type": "integer"},
                        "description": "occurrence ids, not pages",
                    },
                },
                "required": ["qualifier", "primary"],
            },
        },
    },
    "required": ["canonical", "senses"],
}


def blocking_key(term: str) -> str:
    """Cheap normalisation -- groups candidates for the model to adjudicate.

    Deliberately aggressive: over-grouping is safe (the model splits them back apart),
    under-grouping is not (variants never meet, and drift survives into the index).
    """
    t = term.lower().strip()
    t = re.sub(r"\(.*?\)", "", t)              # drop parenthetical qualifiers
    t = re.sub(r"[^a-z0-9 ]+", "", t)
    t = re.sub(r"\b(the|a|an|of|and)\b", "", t)
    t = re.sub(r"(ies)$", "y", t)
    t = re.sub(r"(es|s)$", "", t)
    return re.sub(r"\s+", " ", t).strip()


def cluster(entries: list[dict]) -> dict[str, list[dict]]:
    groups: dict[str, list[dict]] = defaultdict(list)
    for e in entries:
        if e.get("parent"):
            continue  # subentries canonicalize with their parent, in a later pass
        groups[blocking_key(e["term"])].append(e)
    return {k: v for k, v in groups.items() if k}


def needs_review(occs: list[dict]) -> bool:
    """Singletons with one page pass through untouched -- don't pay a model for them."""
    forms = {o["term"].strip() for o in occs}
    primaries = {(o["book_id"], o["page"]) for o in occs if o["role"] == "primary"}
    books = {o["book_id"] for o in occs}
    return len(forms) > 1 or len(primaries) > 1 or len(books) > 1


SNIPPET = 600


def snippet(text: str, term: str) -> str:
    """A window of the passage, centred on the term rather than sliced off the front.

    The chunk runs to ~760 chars at the median and 7k at the worst, so it cannot go in
    whole -- and the head of it is often the tail of the previous topic. Centre on where
    the term is actually used, because that is the sentence that either defines it or
    doesn't, and that judgement is the whole reason we are sending text at all.
    """
    text = re.sub(r"\s+", " ", text or "").strip()
    if len(text) <= SNIPPET:
        return text
    m = re.search(re.escape(term.strip()), text, re.I)
    if not m:
        return text[:SNIPPET] + " ..."
    lo = max(0, m.start() - SNIPPET // 3)
    hi = min(len(text), lo + SNIPPET)
    return ("... " if lo else "") + text[lo:hi] + (" ..." if hi < len(text) else "")


def adjudicate(backend, key: str, occs: list[dict]) -> dict:
    occs = sorted(occs, key=lambda o: o["page"])
    lines = [
        f"- id={i} book={SIGLA.get(o['book_id'], '?')} term={o['term']!r} "
        f"{'DEFINES' if o['role'] == 'primary' else 'mentions'} "
        f"heading={' > '.join(o.get('path') or []) or '?'}\n"
        f"  text: {snippet(o.get('text', ''), o['term'])}"
        for i, o in enumerate(occs)
    ]
    user = f"Group: {key!r}\n\nOccurrences:\n" + "\n".join(lines)
    body = {
        "systemInstruction": {"parts": [{"text": SYSTEM}]},
        "contents": [{"role": "user", "parts": [{"text": user}]}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseSchema": SCHEMA,
            "maxOutputTokens": 8000,
        },
    }
    import requests

    r = requests.post(
        backend.url,
        headers={"Authorization": f"Bearer {backend.token()}", "Content-Type": "application/json"},
        json=body,
        timeout=180,
    )
    r.raise_for_status()
    d = r.json()
    text = "".join(p.get("text", "") for p in d["candidates"][0]["content"]["parts"])
    out = json.loads(text)

    # Resolve occurrence ids -> pages OURSELVES. The model never sees a page number and
    # cannot invent one; an out-of-range id is dropped rather than mis-cited. This is the
    # same invariant Stage 2 relies on, and it has to hold here too.
    senses, seen = [], set()
    for s in out.get("senses", []):
        ids = [s.get("primary"), *(s.get("others") or [])]
        ids = [i for i in ids if isinstance(i, int) and 0 <= i < len(occs) and i not in seen]
        if not ids:
            continue
        seen.update(ids)
        prim = occs[ids[0]]
        refs = [{"book": occs[i]["book_id"], "page": occs[i]["page"]} for i in ids]
        senses.append(
            {
                "qualifier": (s.get("qualifier") or "").strip(),
                "primary": {"book": prim["book_id"], "page": prim["page"]},
                "refs": _dedupe(refs),
            }
        )
    # any occurrence the model forgot to assign still belongs in the index
    for i, o in enumerate(occs):
        if i not in seen:
            ref = {"book": o["book_id"], "page": o["page"]}
            if senses:
                senses[0]["refs"] = _dedupe(senses[0]["refs"] + [ref])
            else:
                senses.append({"qualifier": "", "primary": ref, "refs": [ref]})

    out["senses"] = senses
    u = d.get("usageMetadata", {})
    out["_usage"] = {
        "input_tokens": u.get("promptTokenCount", 0),
        "output_tokens": u.get("candidatesTokenCount", 0) + u.get("thoughtsTokenCount", 0),
    }
    return out


def _dedupe(refs: list[dict]) -> list[dict]:
    seen, out = set(), []
    for r in refs:
        k = (r["book"], r["page"])
        if k not in seen:
            seen.add(k)
            out.append(r)
    return sorted(out, key=lambda r: (ORDER.get(r["book"], 99), r["page"]))


def passthrough(occs: list[dict]) -> dict:
    """No adjudication needed: one surface form, at most one primary."""
    prim = next((o for o in occs if o["role"] == "primary"), occs[0])
    return {
        "canonical": occs[0]["term"].strip(),
        "aliases": sorted({a for o in occs for a in o.get("aliases", []) if a}),
        "senses": [
            {
                "qualifier": "",
                "primary": {"book": prim["book_id"], "page": prim["page"]},
                "refs": _dedupe([{"book": o["book_id"], "page": o["page"]} for o in occs]),
            }
        ],
    }


def run(entries: list[dict], workers: int = 8):
    # attach the heading path from Stage 1 -- the model's key evidence for splitting senses
    chunks = {}
    for book in {e["book_id"] for e in entries}:
        for line in (ROOT / "build" / f"{book}.chunks.jsonl").open():
            c = json.loads(line)
            chunks[c["id"]] = c
    for e in entries:
        c = chunks.get(e["chunk_id"], {})
        e["path"] = c.get("path", [])
        e["text"] = c.get("text", "")  # the evidence for primary-vs-mention

    groups = cluster(entries)
    review = {k: v for k, v in groups.items() if needs_review(v)}
    simple = {k: v for k, v in groups.items() if not needs_review(v)}
    print(f"{len(groups)} clusters: {len(simple)} pass through, {len(review)} need adjudication")

    backend = Vertex("gemini-3.5-flash", 1.5, 9.0)
    spend = 0.0

    def one(item):
        # A cluster that falls back to passthrough is NOT merely slower -- it is unmerged
        # and unsplit, which is the exact defect this stage exists to remove. Retry before
        # giving up on one.
        k, occs = item
        for attempt in range(3):
            try:
                return k, adjudicate(backend, k, occs)
            except Exception as exc:
                last = exc
                time.sleep(2 * 2**attempt)
        print(f"  cluster {k!r} failed 3x ({last}); passing through UNADJUDICATED")
        return k, passthrough(occs)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        adjudicated = dict(pool.map(one, review.items()))

    for r in adjudicated.values():
        u = r.pop("_usage", None)
        if u:
            spend += backend.cost(u)

    vocab = {k: passthrough(v) for k, v in simple.items()}
    vocab.update(adjudicated)
    return vocab, spend


if __name__ == "__main__":
    entries = []
    for b in _REG:
        f = ROOT / "build" / f"{b['id']}.entries.jsonl"
        if f.exists():
            entries += [json.loads(l) for l in f.open()]
    print(f"{len(entries)} entries from {len({e['book_id'] for e in entries})} books")
    vocab, spend = run(entries)
    VOCAB.write_text(json.dumps(vocab, indent=2))

    heads = len(vocab)
    senses = sum(len(v["senses"]) for v in vocab.values())
    split = [v for v in vocab.values() if len(v["senses"]) > 1]
    print(f"\n{heads} headwords, {senses} senses, ${spend:.3f}")
    print(f"{len(split)} headwords split into multiple senses:")
    for v in sorted(split, key=lambda v: -len(v["senses"]))[:8]:
        qs = "; ".join(
            f"{s['qualifier']} {SIGLA[s['primary']['book']]} {s['primary']['page']}"
            for s in v["senses"]
        )
        print(f"    {v['canonical']:<22} {qs}")
    print(f"-> {VOCAB}")
