# Architecture

How the index is built, and why it is built this way. If you are changing the pipeline,
read this first; if you are just using the index, the [README](README.md) is enough.

## The one invariant everything is built around

**The model never emits a number.** Page numbers come from deterministic text extraction;
ship tonnages come from a regex on the stat block. The LLM only ever emits *words* — index
terms — tagged with an **id** that points back to a specific chunk of text. We look the page
up from that id afterwards.

This is the whole reason the citations can be trusted. An LLM asked to "index page 143" will
cheerfully cite page 138; the failure is designed out by never letting the model near a
number. Every stage that involves the model preserves this invariant, and any change that
would let a page number into a prompt — or let the model hand one back — is a bug.

## The pipeline

Six stages. Stages 1 and 4 are deterministic and free; only 2, 3 and 3c call the model.

| Stage | File | Cost | In → Out |
|---|---|---|---|
| 1. Extract | `extract.py` | free | PDF → `build/<book>.chunks.jsonl` |
| 2. Generate | `generate.py` | $$ | chunks → `build/<book>.entries.jsonl` |
| 3. Canon | `canon.py` | $$ | all entries → `build/vocab.json` |
| 3b. Subentries | `subents.py` | free | (applied during render) |
| 3c. Ships | `ships.py` | $ | chunks → `build/ships.json` |
| 4. Render | `web.py`, `master.py` | free | vocab + ships → `index.html`, the PDF |

A run is just these in order. `books.toml` is the registry — one `[[book]]` block per book,
carrying its file, sha256, page count, page `offset`, extraction `profile`, and `genre`.

### Stage 1 — Extract (`extract.py`)

Turns a PDF into **chunks**. A chunk is one within-page, within-heading run of text, carrying:

- `text` — the chunk's own text (a *fraction* of a page, not the whole page)
- `page` — the printed page number it sits on (an integer; every chunk on a physical page
  shares it)
- `path` — the heading breadcrumb it lives under
- `bbox` — its rectangle on the page (used later to anchor links)
- `id` — a content hash

No model. **This is where every page number is born**, and it is never regenerated
downstream. Extraction flushes a chunk at each heading and at every page boundary, so a chunk
never spans a heading or a page.

Headings are told from body text by a per-book **style profile** — a map from `(font, size)`
to a role (`h2`/`h3`/`body`). This is the fiddliest part of the whole pipeline: when a book
sets its headings in a font the profile does not list, those headings are silently dropped,
which mislabels everything under them. `fontaudit.py` (`python -m mgtindex.fontaudit`) exists
to catch exactly that — it flags any font a book uses at title size that its profile captures
at no size. Run it whenever you add a book.

### Stage 2 — Generate (`generate.py`)

Slides a 6-chunk window through the book and asks the model, per window: *what would a reader
look up here?* It emits candidate entries, each tagged with the `chunk_id` it came from and
flagged **primary** (this passage defines the term) or **mention** (uses it, assumes you know
it). The prompt is `rubric.md`, which has shared rules plus a per-`genre` section.

Then — the trust boundary — `generate.py` joins the page on from the chunk metadata:

```python
out.append({**e, "page": chunk["page"], "book_id": chunk["book_id"]})   # generate.py
```

The model returned a `chunk_id`; we stamp the page. The result, `build/<book>.entries.jsonl`,
is the intermediary artifact that carries the page from here on — nothing downstream re-opens
the PDF to get it.

Stage 2's judgements are **local**: the model sees 6 chunks and nothing else, so it is only
ever asked things answerable from that window (define-vs-mention *of this passage*). It is
explicitly told *not* to guess whether this is the book's best page for a term — it cannot
know, and guessing makes it hedge. Electing the one true page is Stage 3's job.

### Stage 3 — Canon (`canon.py`)

Canonicalisation. After Stage 2 you have thousands of raw candidate entries across all books:
duplicates (`jump drive` / `J-drive` / `jump drives`), the same term claimed primary by a
dozen local readers, and collisions (`Agent` the career vs. the toxin vector). Canon is the
editor that sees the whole pile at once and cleans it up.

It works **one term-cluster at a time**, not per book and never on the whole PDF:

1. **Cluster** all candidates by a cheap normalised key (`blocking_key` — lowercases, strips
   parentheticals and plurals). Over-grouping is safe; the model splits them back apart.
2. A cluster that is trivial (one form, one book, at most one primary claim) **passes through
   free** — no model call.
3. Every other cluster gets **one model call** (`adjudicate`). The prompt contains, per
   occurrence: an integer `id`, the book siglum, the term as written, the DEFINES/mentions
   flag, the heading path, and a **snippet** — and *no page number*.

The model reads the occurrences side by side and returns: a `canonical` name, `aliases`, and
`senses`, where each sense nominates occurrence **ids** (`{primary: 4, others: [3]}`). It
merges spellings, splits genuine collisions into senses with a disambiguating qualifier, and
picks the defining occurrence by *reading the snippets*. Then `adjudicate` resolves those ids
back to `{book, page}` from the stored entry metadata — the model's integers become
references. Result: `build/vocab.json`.

**The snippet** (`snippet()`): the text of the *one* chunk that occurrence points at — the
whole chunk if it is ≤600 chars, otherwise a ~600-char window centred on the term. It is a
slice of a single chunk; it never spans chunks. This is the usage text the model judges on;
the page number rides separately as metadata and is never shown to the model.

### Stage 3b — Subentries (`subents.py`)

Deterministic, applied during render. Files each sub-entry under the correct **sense** of its
parent, so `Agent`-the-career's subentries (Corporate, Intelligence) do not interleave with
`Agent`-the-toxin's (biological agents, exposure). Also rescues subentries whose parent was
named but never emitted as a headword.

### Stage 3c — Ships (`ships.py`)

Ships get special handling because a stat block is far more valuable than a name-drop and
readers hunt for them. Unlike canon, this stage **stitches a page's chunks back together** —
it wants the whole spec page to name the vessel. It finds every page with a `Hull N tons`
block (the tonnage is the regex — no model number), and makes one model call per page to name
the ship. `apply()` folds the vessels into the vocabulary at render time and builds the Ships
tab. Output: `build/ships.json`.

### Stage 4 — Render (`web.py`, `master.py`)

Deterministic. `web.py` builds `index.html` (the primary deliverable); `master.py` builds the
printable PDF. Both call `load()`, apply subentries and ships, apply the manual overrides
(below), and lay out the alphabet, the cross-references, and the Ships tab. Page links use
`#page=N` — bare, because `view=FitH,<top>` misbehaves in Chrome (over-magnifies and reads
the coordinate upside down).

## Data flow of a single reference

The lineage of one page number in the finished index:

```
Stage 1   chunk.page = 172              (minted here, from the PDF)
Stage 2   entry.page = 172              (copied onto the entry via chunk_id; PDF not re-read)
Stage 3   occ.page  = 172              (canon reads the entry; used to sort + resolve, never sent)
Stage 4   ref = {book, page: 172}      (rendered into the link)
```

One value, minted once, copied forward, never regenerated and never shown to the model.

## Caching

Every model call is cached on disk by a **content hash of its inputs**, so re-running after a
change only re-bills what actually changed.

- `build/entries/` — Stage 2, keyed by `(chunk ids + model + the book's effective rubric)`.
  Adding a book re-indexes only that book; editing one genre's rubric section invalidates only
  that genre's books.
- `build/clusters/` — Stage 3, keyed by the cluster's occurrences (book, page, role, term,
  path, snippet) + model + system prompt. Editing one book re-bills only its changed clusters;
  the rest load free. Bump `_CACHE_V` in `canon.py` to invalidate everything.
- `build/ships/` — Stage 3c, keyed per spec page.

These caches live under `build/`, which is **gitignored** — it also holds verbatim extracted
book text, so it must never be committed. That means the caches are local: a fresh clone
re-bills from scratch.

## Overrides (`overrides.toml`, `overrides.py`)

A hand-edited correction layer applied at the very end (Stage 4), for the handful of cases the
pipeline gets wrong that are not worth re-running the model for. Keyed to stable,
human-checkable anchors:

- **`[[ship]]`** — by book siglum + printed page. Rename a vessel, replace its aliases,
  override the tonnage, or drop it. (Applied inside `ships.apply`, before name hygiene.)
- **`[[headword]]`** — by exact canonical. `rename`, `drop`, `aliases_add`/`aliases_drop`, or
  `primary = {book, page}` to re-aim a headword whose elected primary landed on a mention.

Every override is surgical: any field you omit is left as the pipeline produced it.

## Cost model

Stages 2 and 3 are the only real spend. Their cost tracks the number and size of *model calls*,
not book length:

- **Stage 2** ≈ windows × density. Dense genres (catalogue, bestiary emit ~11 entries/page)
  cost more; a big book is ~$5–6.
- **Stage 3** ≈ clusters needing adjudication × their occurrence count. A full pass over the
  current corpus is ~$10 uncached; the cluster cache makes an incremental re-run a fraction of
  that.

Output tokens are 6× the price of input, and thinking counts as output — which is why dense,
sense-splitting clusters dominate. Re-extracting a book with a corrected profile roughly
doubles its chunk count (it recovers dropped headings), which roughly doubles its Stage 2
cost — budget accordingly, and set the `MGT_BUDGET` cap *above* the real per-book cost or a run
stops half-done.
