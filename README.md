# MGT2 Master Index

A single fine-grained index across seven Mongoose Traveller 2nd Edition rulebooks, built
by running the books through a text-extraction pipeline and an LLM indexer.

Mongoose spreads the rules for one topic across many books, and each book ships an index
that is really just a dump of its section headings — `Opposed Checks 62`, `Multiple Tasks
62`. That is not what you need at the table. What happens when an opposed check *ties* is a
rule, it lives in an unheaded paragraph, and today it is unfindable. This index tries to
find it.

**Output:** `MGT2 Master Index.html` — 3,190 headwords, 4,855 records, ~9,800 deep links
that open the right PDF at the right page. Plus a printable PDF, and a sortable catalogue of
every ship in the seven books that has a full specification.

## The books it covers

Seven books, 1,772 printed pages, indexed into one alphabet.

Look up `armour` and you get it under four separate meanings — *personal* armour (Core
Rulebook, Central Supply Catalogue, Companion, Robot Handbook, Aliens Vol. 2), *spacecraft*
armour (High Guard), *robot* armour (Robot Handbook), and armour as a *combat mechanic*
(Core Rulebook) — with 42 subentries filed under whichever one they belong to. That is the
point of the exercise: one lookup, every book, and the senses kept apart.

| | Book | Edition indexed | Pages | Index refs | Ships |
|---|---|---|---:|---:|---:|
| **CRB** | Core Rulebook | 2022 Update (Dec 2024 printing) | 266 | 1,829 | 24 |
| **HG** | High Guard | Apr 2024 | 290 | 1,446 | 31 |
| **CSC** | Central Supply Catalogue | 2023 Update (Apr 2024 printing) | 186 | 1,394 | — |
| **TC** | Traveller Companion | 2024 Update | 186 | 938 | — |
| **RH** | Robot Handbook | Apr 2024 | 266 | 1,504 | — |
| **AL1** | Aliens of Charted Space, Vol. 1 | Apr 2024 | 305 | 1,345 | 30 |
| **AL2** | Aliens of Charted Space, Vol. 2 | undated printing | 273 | 1,364 | 9 |
| | | | **1,772** | **9,820** | **94** |

*Index refs* is how many page links point into that book; *Ships* is how many vessels in it
carry a full stat block and so appear in the Ships tab.

**Editions matter.** Mongoose reprints these books and the pages move, so an index built
against one printing is wrong for another. Every page link here is generated against the
exact file listed above, and `books.toml` records each one's SHA-256 — the index's **About**
panel shows them, so you can check whether your copy is the same printing before you trust a
page number.

The books themselves are **not** in this repo. They are commercial products and not mine to
redistribute; bring your own PDFs.

## Get it

**[⬇ Download the index](https://github.com/dcsturman/mgt-index/raw/main/MGT2%20Master%20Index.html)**
— one self-contained HTML file, no install, no build, nothing to run.

Save it **into the folder where you keep your Traveller PDFs**, then open it in a browser.
If your files still have the names Mongoose shipped them under, that is the whole setup —
the links are relative, so they find the books sitting beside them.

Otherwise open **Settings** (⚙): type the folder the books live in, and hit **Choose…** to
pick them. The picker exists because a browser will tell a page a file's *name* but never
its *path*, and these filenames are long and dated — `MgT2 Core Rulebook Update 2022
11-12-2024.pdf` — so one wrong character silently kills every link into that book. Let the
picker spell them. It matches renamed files by title words too. Settings live in
`localStorage`, so you do this once per browser.

> Clicking the file on GitHub shows you its source, because GitHub serves HTML as text
> rather than rendering it. Use the download link above, or `curl`:
>
> ```sh
> curl -L -o "MGT2 Master Index.html" \
>   "https://github.com/dcsturman/mgt-index/raw/main/MGT2%20Master%20Index.html"
> ```

It has to run from your own disk rather than from a web page, and that is not laziness:
browsers refuse to let an `https://` page open a `file:///` link — sensibly, or any site
could rummage through your filesystem. So a hosted copy would render perfectly and every
one of its ~9,800 page links would be dead. Local it is.

## Using it

Every page reference is a link that opens the right book at the right page, scrolled to the
right paragraph. Search matches terms and aliases as you type.

The **Ships** tab lists every vessel with a full specification in any of the seven books,
sortable by tonnage, name, or book. Looking up a ship is a common enough activity to deserve
its own view, and it beats hunting for a name you half-remember.

## How it works

Four stages. Stage 1 and Stage 4 are deterministic and free; only 2 and 3 call a model.

| | Stage | Cost | What it does |
|---|---|---|---|
| 1 | `extract.py` | free | PDF → text chunks, tagged with printed page and heading path |
| 2 | `generate.py` | $$ | Chunk windows → candidate index entries |
| 3 | `canon.py` | $$ | Cluster synonyms, split senses, elect one primary page per term |
| 3b | `subents.py` | free | Attach subentries to the right *sense* of their headword |
| 3c | `ships.py` | $ | Identify ship stat blocks; tonnage comes from a regex |
| 4 | `web.py`, `master.py` | free | Render the HTML and the printable PDF |

### The one invariant: the model never emits a number

This is the whole trick, and everything else is arranged around it.

An LLM asked to produce an index will happily cite page 143 for something that is on page
138, and a citation you cannot trust is worse than no index at all. So the model is never
allowed to write a number down. In Stage 2 it emits a *term* tagged with the id of the
chunk it came from, and the page number is joined back on afterwards from Stage 1's
metadata. In Stage 3c it names the ship, and the tonnage is read out of the stat block with
a regular expression. The worst failure mode is designed out rather than mitigated.

### Local questions get local answers

Stage 2 sees a six-chunk window and nothing else, so it is only ever asked things it can
actually answer from that window: *does this passage define the term, or use one it assumes
you know?* It is explicitly told **not** to guess whether this is the book's best page for a
term — it cannot know that, and guessing makes it hedge and mark everything `primary`.
Electing the single primary page is Stage 3's job, and Stage 3 sees every passage at once.

Getting this wrong was the source of two separate bugs, both of which came down to asking
for a global judgement from a local view.

### Caching

Stage 2 results are cached on disk under `build/entries/`, keyed by
`(chunk ids + model + the book's effective rubric)`. Adding a book re-indexes only that
book. Editing the bestiary section of the rubric invalidates only the two bestiary books.
This matters because a full re-index is real money, and a cache key that is too coarse
quietly bills you for it.

## The rubric

[`mgtindex/rubric.md`](mgtindex/rubric.md) is the Stage 2 prompt, and it is where nearly all
of the quality lives. It is shared rules plus one section per genre — what a reader hunts
for in an equipment catalogue is not what they hunt for in a bestiary, and a prompt written
around the Core Rulebook under-indexes both. Each book declares its `genre` in `books.toml`.

If you want to improve this project, improve the rubric.

## Running it

```sh
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
export VERTEX_PROJECT=your-gcp-project      # required; there is no default, on purpose
gcloud auth application-default login       # Stages 2 and 3 authenticate via ADC

.venv/bin/python -m mgtindex.extract  core-rulebook   # free
.venv/bin/python -m mgtindex.generate core-rulebook   # ~$2-3/book, cached
.venv/bin/python -m mgtindex.canon                    # ~$2, all books at once
.venv/bin/python -m mgtindex.web                      # free -> the HTML
.venv/bin/python -m mgtindex.master                   # free -> the PDF
```

Stages 2 and 3 take a hard dollar ceiling (`MGT_BUDGET`, default $3.00) and abort rather
than overspend. Every window already paid for is written to the cache before the abort, so
a re-run resumes instead of starting over.

The whole index, from nothing, costs roughly $20 in Gemini calls.

## Known limitations

- *Aliens of Charted Space* uses a different typographic profile and Stage 1 captures about
  87% of its text, against 94–97% elsewhere. Two Hiver ships end up with placeholder names
  because their heading extraction produces garbage. That is a Stage 1 defect and no amount
  of LLM spend fixes it.
- Entry density varies by book. The model converges on roughly one entry per 350–400
  characters of body text no matter what the rubric asks for, so text-dense pages get denser
  indexing. I spent a while trying to fight this and concluded it was not worth it.

---

*This work is a non-commercial fan project. Mongoose Traveller and Traveller are the
property of their respective owners; see the notice reproduced in the generated index.*
