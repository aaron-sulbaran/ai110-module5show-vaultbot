# VaultBot

A question answering system for a personal markdown vault (an Obsidian style "second brain"). It retrieves the section of your notes that answers a question, cites it, and refuses when your notes do not cover the question.

AI110 Project 4: Applied AI System.

## Base project and original scope

VaultBot extends **DocuBot**, the Module 4 Tinker project (`codepath/ai110-module4tinker-docubot-starter`). The git history of this repository starts from that starter.

DocuBot was a small documentation assistant for a codebase. Its original scope:

- Load four markdown files from one flat `docs/` folder.
- Retrieve by treating each **whole file** as one document and counting how many question words appear in it.
- Answer in three modes: naive LLM over all docs, retrieval only, and RAG (retrieved files passed to Gemini).
- Refuse only when no file shares a single word with the question.
- Measure a retrieval hit rate over eight sample queries.

That design works for four short files about one codebase. It does not hold up for a personal vault, which is nested, mixes curated pages with raw meeting notes, puts many topics in one file, contains private notes, and holds personal facts where a confident wrong answer is worse than no answer.

## What is new

| Feature | Where | What it changes |
| --- | --- | --- |
| **RAG over a vault** (the substantial AI feature) | `vaultbot.py`, `vault_loader.py` | `VaultBot` subclasses `DocuBot` and replaces loading, indexing, and retrieval. The DocuBot menu, modes, and LLM client still run on top of it. |
| **Custom indexing** (stretch: RAG enhancement) | `vault_loader.py` | Notes are split into heading level chunks. Retrieval returns the section that answers the question, not the file. |
| **BM25 ranking with heading boost** | `vaultbot.py` | Rare words count more than common ones. Words in the note title and heading trail count extra. |
| **Multi source retrieval** (stretch: RAG enhancement) | `vaultbot.py` | Each top level folder is a source with its own weight (`wiki` 1.0, `sources` 0.85), and `--source` limits a search to one source. |
| **Confidence refusal** (guardrail) | `vaultbot.py` | If the best chunk covers less than half of the question's information, the bot answers "I do not know based on this vault." |
| **Input validation, redaction, citation check** (guardrails) | `guardrails.py` | Empty or oversized questions are rejected. Emails, phone numbers, and key shaped strings are masked before any snippet is sent to an LLM. An LLM answer that cites nothing, or cites a snippet it was not given, is discarded. |
| **Privacy exclusions** | `vault_loader.py` | Notes with `private: true` in frontmatter, excluded globs, and folders like `.obsidian` and `_archive` are never read into the index. |
| **Evaluation harness** (stretch: test harness) | `evaluation.py`, `dataset.py` | 14 predefined cases run against the DocuBot baseline and VaultBot, with a pass/fail summary and exit code. |

## Privacy model

This repository contains code and `sample_vault/`, a small **fictional** vault. It contains no real notes.

- Your own vault is referenced only by `VAULT_PATH` in `.env`, which is gitignored. Nothing from your vault is copied into the project folder, and no index is written to disk.
- The default mode is fully local. No network call is made unless you pass `--llm`.
- With `--llm`, only the top retrieved snippets are sent to Gemini, after redaction.

## Architecture

The Mermaid source is in [`architecture.mmd`](architecture.mmd).

```mermaid
flowchart TD
    Q[User question<br/>main.py ask] --> V{validate_query<br/>guardrails.py}
    V -- empty, too long, no words --> R[Refusal<br/>I do not know based on this vault]

    subgraph Indexing [Indexing, once per run]
        N[Vault folder<br/>VAULT_PATH or sample_vault] --> L[vault_loader.py<br/>walk folders, parse frontmatter]
        L -- private: true, excluded globs,<br/>.obsidian, _archive --> X[Skipped, never indexed]
        L --> C[Heading level chunks<br/>path, source, title, heading trail]
        C --> I[BM25 inverted index<br/>title and heading words boosted]
    end

    V -- valid --> S[VaultBot.search<br/>BM25 score x source weight<br/>max 2 chunks per note]
    I --> S
    S --> K{Confidence check<br/>IDF weighted coverage<br/>of the question >= 0.5}
    K -- below threshold --> R
    K -- pass, no --llm --> A1[Answer: cited snippets<br/>mode retrieval]
    K -- pass, --llm --> D[redact_sensitive<br/>emails, phones, keys masked]
    D --> G[Gemini<br/>answer_with_citations]
    G --> CC{check_citations<br/>every n in 1..k}
    CC -- grounded --> A2[Answer with citations<br/>mode rag]
    CC -- uncited or invalid --> A3[LLM answer discarded<br/>cited snippets shown<br/>mode retrieval-fallback]
    G -- model says it does not know --> R

    E[evaluation.py --vault<br/>14 cases: 10 answerable, 4 refusals] -. runs baseline and VaultBot .-> S
```

Data flow: a question is validated, scored against a BM25 index of heading level chunks, and gated by a confidence check. Below the threshold the system refuses. Above it, the system returns cited snippets, or (with `--llm`) redacts the snippets, asks Gemini for a cited answer, and verifies the citations before showing it.

## Setup

Requires Python 3.9 or newer.

```bash
git clone <this repository>
cd ai110-module5show-vaultbot
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Optional configuration:

```bash
cp .env.example .env
```

Then edit `.env`:

- `GEMINI_API_KEY`: only needed for `--llm`.
- `VAULT_PATH`: absolute path to your own vault. Defaults to `sample_vault`.
- `VAULT_EXCLUDE`: comma separated globs to never index, for example `journal/*,finances/*`.

## Run

```bash
python main.py ask "your question"          # retrieval with citations, fully local
python main.py ask "your question" --llm    # retrieval plus Gemini, citations verified
python main.py ask "your question" --source wiki --top-k 5
python main.py stats                        # what was indexed
python main.py eval                         # evaluation harness
python main.py                              # original DocuBot interactive menu
python -m pytest                            # tests
```

## End to end examples

All output below is from the fictional sample vault.

**1. An answerable question.** The answer comes from one section of one note, with its citation.

```text
$ python main.py ask "When are Dr. Ortiz's office hours?" --top-k 1
Question:   When are Dr. Ortiz's office hours?
Mode:       retrieval
Confidence: 0.74 (threshold 0.50)

[1] wiki/people/dr-lena-ortiz.md#Working with her
- Office hours are Tuesdays from 2 to 4 PM in room 3.214.
- She prefers a one page written update before each meeting.
- She pushes hard on scope. Her usual question is "what would you cut?"
```

**2. A decision question.** The curated wiki page outranks the raw meeting note where the decision was argued.

```text
$ python main.py ask "Why did we choose SQLite for Trailhead?" --top-k 2
Question:   Why did we choose SQLite for Trailhead?
Mode:       retrieval
Confidence: 0.60 (threshold 0.50)

[1] wiki/projects/trailhead.md#Decisions
- Storage: SQLite on the device instead of Postgres on a server. The app has
  to work with no signal, and a server database cannot do that.
- Maps: vector tiles from OpenTrailMap, cached per region. Raster tiles were
  three times larger for the same area.
- Scope cut: no social feed in version one. Dr. Ortiz said it would not be
  graded and would cost two weeks.

[2] wiki/projects/trailhead.md#Overview
Trailhead is an offline first hiking app. It downloads trail maps before a
hike and keeps working with no signal. It is the capstone project for the
spring semester, built with Sam Whitfield.
```

**3. Multi source retrieval.** The same topic, limited to the `sources` folder, returns the meeting note instead.

```text
$ python main.py ask "What action items came out of the Trailhead kickoff?" --source sources --top-k 1
Question:   What action items came out of the Trailhead kickoff?
Mode:       retrieval
Confidence: 0.61 (threshold 0.50)

[1] sources/meetings/2026-03-04 trailhead kickoff.md#Action items
- Jordan: write the storage schema by March 11.
- Sam: benchmark vector tiles against raster tiles.
- Dr. Ortiz: send the grading rubric for the prototype demo.
```

**4. What was indexed.** One note is skipped because its frontmatter says `private: true`.

```text
$ python main.py stats
vault: sample_vault
files_indexed: 11
files_skipped_private: 1
files_skipped_excluded: 0
chunks: 33
chunks_by_source: {'(root)': 1, 'sources': 6, 'wiki': 26}
unique_terms: 345
```

## Guardrail examples

| Input | Behavior | Result |
| --- | --- | --- |
| `"How much is the rent on the new lease?"` (the only answer is in a note marked `private: true`) | The private note was never indexed. Best remaining chunk covers 27% of the question, below the 50% threshold. | Refusal: "I do not know based on this vault." |
| `"Who won the 2022 World Cup?"` (off topic) | No chunk shares an informative word with the question. Confidence 0.00. | Refusal. The LLM is never called. |
| `"What is the payment processing vendor for Trailhead?"` (real project, fact not in the notes) | "Trailhead" matches many chunks, but "payment", "processing", and "vendor" match none. Confidence 0.11. | Refusal, instead of returning Trailhead notes that do not answer it. |
| `"   "` (empty) | Rejected by `validate_query` before retrieval. | Refusal with the note "The question is empty." |
| LLM replies "Office hours are Wednesdays at noon." with no citation | `check_citations` fails: the answer cites no sources. | LLM answer discarded. The retrieved snippet (which says Tuesdays) is shown instead, mode `retrieval-fallback`. |
| A retrieved snippet contains `pat@example.com` and `512-555-0147`, and `--llm` is on | `redact_sensitive` runs before the prompt is built. | Gemini receives `[REDACTED EMAIL]` and `[REDACTED PHONE]`. |

The refusal as it appears in the terminal:

```text
$ python main.py ask "How much is the rent on the new lease?"
Question:   How much is the rent on the new lease?
Mode:       refusal
Confidence: 0.27 (threshold 0.50)
Note:       Confidence 0.27 is below the threshold 0.50. The vault does not cover this question.

I do not know based on this vault.
```

The last two rows are covered by tests that use a fake LLM (`test_uncited_llm_answer_is_discarded`, `test_snippets_are_redacted_before_reaching_the_llm`), so they run without an API key.

## Evaluation harness

`python main.py eval` (or `python evaluation.py --vault`) runs 14 predefined cases against two systems on the same vault: the DocuBot baseline (whole files, keyword counting) and VaultBot. Ten cases are answerable and pass only if the right note is retrieved **and** the retrieved text contains the answer. Four cases must be refused. The exit code is 0 only when VaultBot passes every case.

```text
$ python main.py eval

Baseline (DocuBot: whole files, keyword count)
----------------------------------------------
  PASS  [answer] Why did we choose SQLite for Trailhead?
        wiki/projects/trailhead.md
  PASS  [answer] When is the Trailhead prototype demo due?
        wiki/projects/trailhead.md
  PASS  [answer] When are Dr. Ortiz's office hours?
        wiki/people/dr-lena-ortiz.md
  PASS  [answer] Which day is Sam unavailable?
        wiki/people/sam-whitfield.md
  PASS  [answer] What percent of the algorithms grade is homework?
        wiki/courses/cs-331-algorithms.md
  PASS  [answer] What does midterm 2 cover?
        wiki/courses/cs-331-algorithms.md
  PASS  [answer] What are my spaced repetition review intervals?
        wiki/concepts/spaced-repetition.md
  PASS  [answer] What is the rule for postponed tasks in the weekly review?
        wiki/concepts/weekly-review.md
  PASS  [answer] Why is Pantry Pal paused?
        wiki/projects/pantry-pal.md
  PASS  [answer] Who has to measure battery drain before the demo?
        sources/meetings/2026-03-18 advisor check-in.md
  FAIL  [refuse] What is my bank account number?
        answered from sources/meetings/2026-03-18 advisor check-in.md
  FAIL  [refuse] Who won the 2022 World Cup?
        answered from README.md
  FAIL  [refuse] What is the payment processing vendor for Trailhead?
        answered from wiki/people/dr-lena-ortiz.md
  FAIL  [refuse] How much is the rent on the new lease?
        answered from wiki/projects/trailhead.md
  Answerable questions passed: 10/10
  Correct note ranked first:   9/10
  Correct refusals:            0/4
  Average context size:        1667 characters
  TOTAL: 10/14 passed

VaultBot (heading chunks, BM25, source weights, refusal)
--------------------------------------------------------
  PASS  [answer] Why did we choose SQLite for Trailhead?
        wiki/projects/trailhead.md#Decisions
  PASS  [answer] When is the Trailhead prototype demo due?
        wiki/projects/trailhead.md#Timeline
  PASS  [answer] When are Dr. Ortiz's office hours?
        wiki/people/dr-lena-ortiz.md#Working with her
  PASS  [answer] Which day is Sam unavailable?
        wiki/people/sam-whitfield.md#Notes
  PASS  [answer] What percent of the algorithms grade is homework?
        wiki/courses/cs-331-algorithms.md#Grading
  PASS  [answer] What does midterm 2 cover?
        wiki/courses/cs-331-algorithms.md#Exams
  PASS  [answer] What are my spaced repetition review intervals?
        wiki/concepts/spaced-repetition.md#The idea
  PASS  [answer] What is the rule for postponed tasks in the weekly review?
        wiki/concepts/weekly-review.md#Rule
  PASS  [answer] Why is Pantry Pal paused?
        wiki/projects/pantry-pal.md#Status
  PASS  [answer] Who has to measure battery drain before the demo?
        sources/meetings/2026-03-18 advisor check-in.md#Action items
  PASS  [refuse] What is my bank account number?
        refused
  PASS  [refuse] Who won the 2022 World Cup?
        refused
  PASS  [refuse] What is the payment processing vendor for Trailhead?
        refused
  PASS  [refuse] How much is the rent on the new lease?
        refused
  Answerable questions passed: 10/10
  Correct note ranked first:   9/10
  Correct refusals:            4/4
  Average context size:        395 characters
  TOTAL: 14/14 passed

Summary
-------
  Baseline: 10/14 passed
  VaultBot: 14/14 passed
  RESULT: PASS
```

## RAG enhancement: before and after

Same vault, same 14 questions, same privacy exclusions.

| Measure | Baseline (DocuBot) | VaultBot |
| --- | --- | --- |
| Answerable questions passed | 10/10 | 10/10 |
| Questions correctly refused | 0/4 | 4/4 |
| Total | 10/14 | 14/14 |
| Average context returned per answer | 1667 characters | 395 characters |

What changed in output quality:

- **Refusals.** The baseline answered all four unanswerable questions, because a whole file almost always shares a common word like "the" or "is" with any question. For "Who won the 2022 World Cup?" it returned the vault README. VaultBot refuses all four.
- **Context size.** Both systems find the right note for the answerable questions on a vault this small. The baseline returns three whole files. VaultBot returns three sections, about a quarter of the text, so an LLM gets less unrelated material to drift into, and a person reading the snippets sees the answer first.
- **Before and after for one question**, "When are Dr. Ortiz's office hours?": the baseline returns the full Dr. Lena Ortiz page plus two other full files. VaultBot's first result is the section `wiki/people/dr-lena-ortiz.md#Working with her`, three lines long, whose first line is the answer.

On a real vault (about 2,700 notes and 17,600 chunks, not included here) the index builds in under two seconds and a question takes a few milliseconds. Whole file retrieval is not usable at that size: single files run to tens of thousands of characters.

## Tests

```text
$ python -m pytest -q
.........................                                                [100%]
25 passed in 0.04s
```

The suite covers the loader (frontmatter, headings, code fences, private notes, exclude globs), retrieval (ranking, source filter, source weights), every guardrail, the RAG path with a fake LLM, and the evaluation harness itself.

## Known limitations

- **Retrieval is lexical.** A question has to share words with the note. "Why was Postgres rejected?" limited to `--source sources` is refused at confidence 0.46, because the meeting note says Sam "argued for Postgres" and never uses the word "rejected". Embeddings would fix this and would add a model download and a slower index.
- **The confidence check weakens as the vault grows.** In a 2,700 note vault, almost every word appears somewhere, so a question like "What is my bank routing number?" found a chunk containing "bank" and "number" and passed the threshold at 0.76 with irrelevant snippets. On the sample vault the same style of question is refused. The citation check and the LLM's own refusal rule are the remaining layers in that case.
- **Redaction is pattern based.** It catches emails, phone numbers, and common key formats. It does not catch names, addresses, or account numbers written in other formats.
- **No LLM output was captured in this README.** RAG generation is implemented and tested with a fake LLM. Live Gemini output depends on your own API key.
- **The index is rebuilt on every run.** That is fine at 2 seconds and would need caching for a much larger vault.

## Reflection

_To be written by Aaron before submission._

## Files

| File | Purpose |
| --- | --- |
| `docubot.py` | Base project class, with its Phase 1 baseline completed. Used as the evaluation baseline. |
| `vaultbot.py` | `VaultBot` (chunk index, BM25, source weights, confidence, answer pipeline) and `WholeFileVaultBot` (baseline on a vault). |
| `vault_loader.py` | Vault walking, frontmatter, privacy exclusions, heading chunking. |
| `guardrails.py` | Input validation, redaction, citation check. |
| `llm_client.py` | Gemini client from the base project, plus `answer_with_citations`. |
| `evaluation.py`, `dataset.py` | Evaluation harness and its cases. |
| `main.py` | CLI. |
| `sample_vault/` | Fictional notes for demos, tests, and evaluation. |
| `tests/` | Pytest suite. |
| `architecture.mmd` | Mermaid source of the architecture diagram. |
| `model_card.md` | System behavior, failure cases, limitations, responsible use. |
