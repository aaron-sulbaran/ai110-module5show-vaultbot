# VaultBot Model Card

VaultBot extends the DocuBot Tinker project. This card follows the DocuBot model card sections.

---

## 1. System Overview

**What is VaultBot trying to do?**
Answer questions about one person's markdown notes by retrieving the section that holds the answer, citing it, and refusing when the notes do not cover the question. Personal facts are the content, so the system is built to prefer "I do not know" over a plausible guess.

**Inputs:** a question, a vault folder (`VAULT_PATH`, default `sample_vault`), optional exclude globs, and an optional `GEMINI_API_KEY` for generated answers.

**Outputs:** cited snippets (default), or a generated answer with verified `[n]` citations (`--llm`), or the refusal "I do not know based on this vault." Every result carries a mode and a confidence value.

---

## 2. Retrieval Design

**Indexing.** Each note is split at markdown headings into chunks of at most 1,200 characters. Each chunk keeps its path, top level folder (its source), note title, and heading trail. A BM25 inverted index maps each word to the chunks that contain it. Words from the title and heading trail are counted two extra times.

**Scoring.** BM25 (k1 1.5, b 0.75) times a per source weight (`wiki` 1.0, `sources` 0.85). At most two chunks per note are returned so one long note cannot fill every slot.

**Confidence.** For each returned chunk, coverage is the IDF weighted share of the question's words found in that chunk. Confidence is the best coverage in the result list. Below 0.5 the system refuses.

**Tradeoffs.**
- BM25 instead of embeddings: no model download, no data leaves the machine, a 2,700 note vault indexes in under two seconds. The cost is that a question must share words with the note.
- Heading chunks instead of fixed size windows: citations point at a named section a person can open. The cost is uneven chunk sizes and weak chunks under very short headings.
- Rebuilding the index on every run instead of caching: nothing derived from private notes is ever written to disk. The cost is about two seconds per run on a large vault.

---

## 3. Use of the LLM (Gemini)

- **Naive LLM mode** (inherited from DocuBot): sends only the question. Kept as the "no grounding" comparison.
- **Retrieval only mode** (default): no LLM call. Returns cited snippets or refuses.
- **RAG mode** (`--llm`): runs only after the confidence check passes. Snippets are redacted, numbered, and sent with the question.

**Grounding instructions:** use only the snippets, cite the snippet after every claim as `[n]`, reply exactly "I do not know based on this vault." when the snippets do not answer, stay under 120 words. After generation, `check_citations` discards any answer with no citation or with a citation number that was not provided.

---

## 4. Experiments and Comparisons

Measured with `python main.py eval` on the sample vault (14 cases).

| Measure | Baseline (DocuBot: whole files, keyword count) | VaultBot |
| --- | --- | --- |
| Answerable questions passed | 10/10 | 10/10 |
| Correct note ranked first | 9/10 | 9/10 |
| Questions correctly refused | 0/4 | 4/4 |
| Average context per answer | 1,667 characters | 395 characters |
| Total | 10/14 | 14/14 |

**Patterns.**
- The baseline never refuses. Any file shares some common word with any question, so "Who won the 2022 World Cup?" returns the vault README.
- On a vault this small, both systems find the right note. The measurable gains are refusals and context size (VaultBot returns about a quarter of the text).
- The first VaultBot version scored 12/14, below its own target. It refused "Why did we choose SQLite for Trailhead?" because the unseen word "choose" was weighted as the rarest possible word, and it missed the review schedule because "reviewed" did not match "review". Both were fixed (average weight for unseen words, light suffix folding) and the harness went to 14/14.

RAG mode was exercised with a fake LLM in tests. No live Gemini comparison was run for this card.

---

## 5. Failure Cases and Guardrails

**Failure case 1: paraphrase.** Question: "Why was Postgres rejected?" with `--source sources`. The meeting note says Sam "argued for Postgres" and that storage "has to live on the phone". The word "rejected" appears nowhere in that source, so confidence is 0.46 and the system refuses an answerable question. It should have returned the kickoff discussion.

**Failure case 2: large vault false confidence.** Question: "What is my bank routing number?" on a real 2,700 note vault. Some chunk contained both "bank" and "number" in unrelated contexts, coverage reached 0.76, and the system returned irrelevant snippets instead of refusing. It should have refused. The same style of question is refused on the sample vault, so the evaluation harness does not catch this.

**When VaultBot should refuse:** when no chunk covers at least half of the question's information, when the only answer is in a note marked private, when the question is empty or has no searchable words, and when the LLM's answer cannot be tied to the provided snippets.

**Guardrails implemented:** input validation, confidence refusal, privacy exclusions at load time, redaction before any LLM call, citation verification after generation, and a fallback to plain snippets when verification fails.

---

## 6. Limitations and Future Improvements

**Limitations**
1. Lexical retrieval misses paraphrases and synonyms.
2. The coverage based confidence check loses precision as the vault grows.
3. Redaction is pattern based and misses names, addresses, and unusual formats.
4. Wikilinks between notes are ignored, so a person page does not pull in the project pages it links to.
5. The evaluation set is 14 hand written cases on a fictional vault.

**Future improvements**
1. Require question words to appear near each other in a chunk before counting them as covered.
2. Add local embeddings as a second retriever and merge the two rankings.
3. Follow wikilinks one hop from the top result.
4. Build an evaluation set from real questions asked of a real vault, kept outside the repository.

---

## 7. Responsible Use

**Where this could cause harm.** A second brain holds deadlines, commitments, and facts about other people. A wrong answer that looks cited can be acted on. Notes about other people may be sent to an LLM provider when `--llm` is on. A shared or committed index would expose private notes.

**Guidance for use.**
- Open the cited section before acting on an answer that matters.
- Keep the default local mode for sensitive vaults. Use `--llm` only if you accept sending the top snippets to the provider.
- Mark sensitive notes `private: true` or list their folders in `VAULT_EXCLUDE`.
- Treat a refusal as "not found by keyword", not as proof the vault has nothing.
- Never commit `.env` or anything generated from your own vault.
