"""Stage 3b: attach subentries to the SENSE they belong to, and rescue orphaned parents.

Stage 3 splits a headword into senses -- 'Agent' is a career (CRB 22) and a disease vector
(TC 71) -- but it deliberately skips anything with a parent (canon.cluster), leaving the
subentries in one undifferentiated pile per headword. The renderers then printed that pile
under the headword, so the career's assignments (Corporate, Intelligence) sat alphabetically
interleaved with the toxin's (biological agents, delivery method). The split was in the data
and the page threw it away.

No model is needed to fix it. The senses own disjoint sets of (book, page) refs, and every
subentry occurrence carries a book and a page, so the assignment is a join:

  1. the subentry's page is owned by exactly one sense           -> that sense       (~71%)
  2. the page is owned by several senses                         -> nearest primary  (~2%)
  3. the page is owned by no sense (the parent wasn't indexed
     there) -> the sense with the nearest ref in the same book,
     because Mongoose keeps a topic's discussion contiguous      (~28%)

Also here: a parent the model named but never emitted as a headword of its own. Stage 4 used
to look it up, miss, and silently drop its children -- 177 subentries vanished from the index.
Now the parent is promoted to a headword whose refs are the union of its subentries'.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict

from mgtindex.canon import blocking_key


def sub_key(term: str) -> str:
    """Normalise a subentry for dedupe. MUCH gentler than blocking_key.

    blocking_key strips parentheticals, which is what you want when clustering headwords
    ('Agent (career)' and 'Agent' are one headword) and disastrous for subentries: it would
    fuse 'Profession (bartender)' with 'Profession (chef)', and 'Battle Dress (standard)'
    with 'Battle Dress (noble/command)'. Those are distinct things and must stay distinct.

    So: fold case and whitespace, drop a trailing plural, keep everything else.
    """
    t = re.sub(r"\s+", " ", term.strip().lower())
    return re.sub(r"(?<=[a-z])s$", "", t)


def _display(forms: Counter) -> str:
    """Pick the surface form to print: the most frequent, then lowercase, then shortest."""
    best = max(forms.values())
    return sorted(
        (f for f, n in forms.items() if n == best),
        key=lambda f: (f[:1].isupper(), len(f), f),
    )[0]


def promote_orphans(vocab: dict, entries: list[dict]) -> int:
    """A parent that is never a headword loses all its children. Give it a headword.

    Its page refs are the union of its subentries' -- which is exactly right: the parent
    IS discussed on those pages, the model just didn't emit a top-level entry for it.
    """
    kids: dict[str, list[dict]] = defaultdict(list)
    for e in entries:
        p = (e.get("parent") or "").strip()
        if p and blocking_key(p) not in vocab:
            kids[blocking_key(p)].append(e)

    for key, occs in kids.items():
        forms = Counter((o.get("parent") or "").strip() for o in occs)
        refs, seen = [], set()
        for o in sorted(occs, key=lambda o: o["page"]):
            r = (o["book_id"], o["page"])
            if r not in seen:
                seen.add(r)
                refs.append({"book": r[0], "page": r[1]})
        vocab[key] = {
            "canonical": _display(forms),
            "aliases": [],
            "senses": [{"qualifier": "", "primary": dict(refs[0]), "refs": refs}],
            "promoted": True,
        }
    return len(kids)


_STOP = {"the", "a", "an", "of", "and", "to", "in", "for", "traveller"}


def _tokens(path: list[str]) -> set:
    return {w for seg in path or [] for w in re.findall(r"[a-z]+", seg.lower())} - _STOP


def attach(vocab: dict, entries: list[dict], chunks: dict) -> dict[str, list[dict]]:
    """-> {headword key: [ [ {t, refs} ... ] per sense ]}

    The returned list is parallel to vocab[key]["senses"]: index i holds the subentries
    belonging to sense i.
    """
    occs: dict[str, list[dict]] = defaultdict(list)
    tops: dict[str, list[dict]] = defaultdict(list)
    for e in entries:
        p = (e.get("parent") or "").strip()
        k = blocking_key(p or e["term"])
        if k not in vocab:
            continue
        if not p:
            tops[k].append(e)
        elif blocking_key(e["term"]) != k:  # a subentry identical to its parent is noise
            occs[k].append(e)

    def path_of(e) -> list[str]:
        return (chunks.get(e.get("chunk_id")) or {}).get("path") or []

    out: dict[str, list[dict]] = {}
    for key, es in occs.items():
        senses = vocab[key]["senses"]
        n = len(senses)

        # which sense owns each (book, page)?
        owner: dict[tuple, set] = defaultdict(set)
        for i, s in enumerate(senses):
            for r in s["refs"]:
                owner[(r["book"], r["page"])].add(i)

        # ...and what does each sense's neighbourhood look like? The heading breadcrumb is
        # the same evidence Stage 3 used to split the senses apart, so it is the right thing
        # to reach for when the page join comes up empty: 'Spacecraft Design > Hull' places
        # `bonded superdense` with hull armour, not with the flak jackets, even though the
        # page number sits nearer the personal-equipment sense.
        vibe: list[set] = [set() for _ in range(n)]
        for e in tops[key]:
            for i in owner.get((e["book_id"], e["page"])) or ():
                vibe[i] |= _tokens(path_of(e))

        def pick(e) -> int:
            if n == 1:
                return 0
            who = owner.get((e["book_id"], e["page"]))
            if who and len(who) == 1:
                return next(iter(who))
            cands = list(who) if who else list(range(n))
            mine = _tokens(path_of(e))

            def dist(i):
                same = [r["page"] for r in senses[i]["refs"] if r["book"] == e["book_id"]]
                return min((abs(p - e["page"]) for p in same), default=10**6)

            # most heading-path overlap wins; nearest page breaks the tie
            return max(cands, key=lambda i: (len(mine & vibe[i]), -dist(i), -i))

        # group by sense, then dedupe surface-form drift within the sense
        # ('dewclaws'/'Dewclaw', 'merchant caste'/'Merchant caste')
        buckets: list[dict[str, dict]] = [defaultdict(lambda: {"forms": Counter(), "refs": {}})
                                          for _ in range(n)]
        for e in es:
            b = buckets[pick(e)][sub_key(e["term"])]
            b["forms"][e["term"].strip()] += 1
            r = (e["book_id"], e["page"])
            b["refs"][r] = b["refs"].get(r, False) or e["role"] == "primary"

        out[key] = [
            sorted(
                ({"t": _display(d["forms"]), "refs": d["refs"]} for d in bucket.values()),
                key=lambda s: s["t"].lower(),
            )
            for bucket in buckets
        ]
    return out
