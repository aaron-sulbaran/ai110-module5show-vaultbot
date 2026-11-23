"""
VaultBot: DocuBot extended into a retrieval system for a personal vault.

DocuBot (the base project) loaded a flat docs/ folder, scored whole files by
counting shared words, and passed whole files to the LLM.

VaultBot keeps the DocuBot interface (retrieve, answer_retrieval_only,
answer_rag) and replaces what sits behind it:

- Custom indexing: notes are split into heading level chunks (vault_loader).
- BM25 ranking with a boost for words in the note title and heading trail.
- Multi source retrieval: every top level folder is a source with its own
  weight, so curated wiki pages can outrank raw meeting notes.
- A confidence guardrail: when the vault does not cover the question, the
  bot refuses instead of returning the least bad match.
- Citations on every answer, checked before an LLM answer is shown.
- A sensitive request block: questions asking for credentials or financial
  and government identifiers are refused before retrieval runs.
"""

import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass

from docubot import DocuBot
from guardrails import (
    REFUSAL_MESSAGE,
    SENSITIVE_REFUSAL_MESSAGE,
    check_citations,
    detect_sensitive_request,
    mask_identifiers,
    redact_sensitive,
    validate_query,
)
from vault_loader import load_notes, load_vault

BM25_K1 = 1.5
BM25_B = 0.75
HEADING_BOOST = 2           # extra copies of title and heading words per chunk
MAX_CHUNKS_PER_NOTE = 2     # keeps one long note from filling every slot
DEFAULT_MIN_CONFIDENCE = 0.5
DEFAULT_SOURCE_WEIGHTS = {"wiki": 1.0, "sources": 0.85}

STOPWORDS = frozenset("""
a about am an and any are as at be been but by can could did do does for from
had has have he her him his how i if in is it its me my of on or our she so
than that the their them then there these they this to us was we were what
when where which who whom why will with would you your
""".split())


def tokenize(text):
    """
    Lowercase words, stopwords removed, with light suffix folding so that
    "reviews", "reviewed", and "reviewing" all index as "review".
    """
    tokens = []
    for word in re.findall(r"[a-z0-9]+", text.lower()):
        if word in STOPWORDS:
            continue
        if word.endswith("ies") and len(word) > 4:
            word = word[:-3] + "y"
        elif word.endswith("ing") and len(word) > 6:
            word = word[:-3]
        elif word.endswith("ed") and len(word) > 5:
            word = word[:-2]
        elif word.endswith("s") and not word.endswith("ss") and len(word) > 3:
            word = word[:-1]
        tokens.append(word)
    return tokens


@dataclass(frozen=True)
class RetrievedChunk:
    chunk: object       # vault_loader.Chunk
    score: float        # BM25 score after the source weight
    coverage: float     # share of the question's information found in the chunk


class VaultBot(DocuBot):
    def __init__(
        self,
        vault_path="sample_vault",
        llm_client=None,
        source_weights=None,
        exclude_globs=(),
        min_confidence=DEFAULT_MIN_CONFIDENCE,
    ):
        self.vault_path = vault_path
        self.exclude_globs = tuple(exclude_globs)
        self.min_confidence = min_confidence
        self.source_weights = dict(DEFAULT_SOURCE_WEIGHTS)
        if source_weights:
            self.source_weights.update(source_weights)

        self.chunks = []
        self.load_stats = None
        # DocuBot.__init__ calls load_documents() and build_index().
        super().__init__(docs_folder=vault_path, llm_client=llm_client)

    # -----------------------------------------------------------
    # Loading and indexing (overrides DocuBot)
    # -----------------------------------------------------------

    def load_documents(self):
        """Loads the vault as heading level chunks instead of whole files."""
        self.chunks, self.load_stats = load_vault(self.vault_path, self.exclude_globs)
        return [(chunk.chunk_id, chunk.text) for chunk in self.chunks]

    def build_index(self, documents):
        """
        Builds a BM25 inverted index over the chunks.

        index maps token -> list of (chunk position, term frequency).
        Title and heading words are counted extra so a question that names a
        note or a section ranks that section first.
        """
        index = defaultdict(list)
        self.chunk_lengths = []
        self.chunk_vocabularies = []

        for position, chunk in enumerate(self.chunks):
            heading_tokens = tokenize(f"{chunk.title} {chunk.heading}")
            tokens = tokenize(chunk.text) + heading_tokens * HEADING_BOOST
            term_frequencies = Counter(tokens)
            self.chunk_lengths.append(len(tokens))
            self.chunk_vocabularies.append(frozenset(term_frequencies))
            for token, frequency in term_frequencies.items():
                index[token].append((position, frequency))

        chunk_count = len(self.chunks)
        self.average_chunk_length = (
            sum(self.chunk_lengths) / chunk_count if chunk_count else 0.0
        )
        self.inverse_document_frequency = {
            token: math.log(1 + (chunk_count - len(postings) + 0.5) / (len(postings) + 0.5))
            for token, postings in index.items()
        }
        # A question word the vault has never seen counts as an average word.
        # Counting it as the rarest possible word made one paraphrased verb
        # ("choose" when the note says "decisions") enough to force a refusal.
        weights = self.inverse_document_frequency.values()
        self.unseen_token_weight = sum(weights) / len(weights) if weights else 1.0
        return dict(index)

    # -----------------------------------------------------------
    # Retrieval
    # -----------------------------------------------------------

    def _source_weight(self, source):
        return self.source_weights.get(source, 1.0)

    def _coverage(self, query_tokens, chunk_position):
        """
        Share of the question's information (IDF weighted) that the chunk
        contains. Rare words count more than common ones, and words the vault
        has never seen still count against the chunk, so an off topic question
        scores low even when a few common words happen to match.
        """
        unique_tokens = set(query_tokens)
        if not unique_tokens:
            return 0.0
        vocabulary = self.chunk_vocabularies[chunk_position]
        total_weight = 0.0
        matched_weight = 0.0
        for token in unique_tokens:
            weight = self.inverse_document_frequency.get(token, self.unseen_token_weight)
            total_weight += weight
            if token in vocabulary:
                matched_weight += weight
        return matched_weight / total_weight if total_weight else 0.0

    def search(self, query, top_k=3, sources=None):
        """
        Ranks chunks for a question. Returns a list of RetrievedChunk, best
        first, with no confidence filtering applied.

        sources: optional collection of top level folders to search in.
        """
        query_tokens = tokenize(query)
        scores = defaultdict(float)

        for token in set(query_tokens):
            token_weight = self.inverse_document_frequency.get(token)
            if token_weight is None:
                continue
            for position, frequency in self.index[token]:
                length_ratio = (
                    self.chunk_lengths[position] / self.average_chunk_length
                    if self.average_chunk_length else 1.0
                )
                normalizer = frequency + BM25_K1 * (1 - BM25_B + BM25_B * length_ratio)
                scores[position] += token_weight * frequency * (BM25_K1 + 1) / normalizer

        ranked = []
        for position, score in scores.items():
            chunk = self.chunks[position]
            if sources is not None and chunk.source not in sources:
                continue
            ranked.append((score * self._source_weight(chunk.source), position))
        ranked.sort(key=lambda item: (-item[0], self.chunks[item[1]].chunk_id))

        results = []
        chunks_per_note = Counter()
        for score, position in ranked:
            chunk = self.chunks[position]
            if chunks_per_note[chunk.path] >= MAX_CHUNKS_PER_NOTE:
                continue
            chunks_per_note[chunk.path] += 1
            results.append(RetrievedChunk(
                chunk=chunk,
                score=round(score, 3),
                coverage=round(self._coverage(query_tokens, position), 3),
            ))
            if len(results) == top_k:
                break
        return results

    @staticmethod
    def confidence_of(results):
        """Confidence of a result list: the best coverage among its chunks."""
        return max((result.coverage for result in results), default=0.0)

    def retrieve(self, query, top_k=3):
        """
        DocuBot compatible retrieval: a list of (label, text).

        Returns an empty list when confidence is below the threshold, which
        makes the inherited answer modes refuse.
        """
        if detect_sensitive_request(query):
            return []
        results = self.search(query, top_k=top_k)
        if self.confidence_of(results) < self.min_confidence:
            return []
        return [
            (result.chunk.chunk_id, mask_identifiers(result.chunk.text))
            for result in results
        ]

    # -----------------------------------------------------------
    # Answering
    # -----------------------------------------------------------

    def ask(self, query, top_k=3, use_llm=False, sources=None):
        """
        Full pipeline for one question. Returns a dict:

        question, refused, reason, confidence, answer, citations, mode
        """
        is_valid, cleaned_or_reason = validate_query(query)
        if not is_valid:
            return self._refusal(query, cleaned_or_reason, confidence=0.0)
        query = cleaned_or_reason

        sensitive_category = detect_sensitive_request(query)
        if sensitive_category:
            reason = (
                f"Sensitive request ({sensitive_category}). "
                "Refused before retrieval, whatever the vault contains."
            )
            return self._refusal(query, reason, confidence=0.0, answer=SENSITIVE_REFUSAL_MESSAGE)

        results = self.search(query, top_k=top_k, sources=sources)
        confidence = self.confidence_of(results)
        if confidence < self.min_confidence:
            reason = (
                f"Confidence {confidence:.2f} is below the threshold "
                f"{self.min_confidence:.2f}. The vault does not cover this question."
            )
            return self._refusal(query, reason, confidence)

        citations = [result.chunk.chunk_id for result in results]
        snippets_answer = self._format_snippets(results)

        if not use_llm or self.llm_client is None:
            return {
                "question": query, "refused": False, "reason": "",
                "confidence": confidence, "answer": snippets_answer,
                "citations": citations, "mode": "retrieval",
            }

        numbered_snippets = [
            (number, result.chunk.chunk_id, redact_sensitive(result.chunk.text))
            for number, result in enumerate(results, start=1)
        ]
        llm_answer = self.llm_client.answer_with_citations(query, numbered_snippets)

        if REFUSAL_MESSAGE.lower() in llm_answer.lower():
            return self._refusal(query, "The LLM judged the snippets insufficient.", confidence)

        is_grounded, citation_reason = check_citations(llm_answer, len(numbered_snippets))
        if not is_grounded:
            return {
                "question": query, "refused": False,
                "reason": f"LLM answer discarded. {citation_reason} Showing the retrieved snippets instead.",
                "confidence": confidence, "answer": snippets_answer,
                "citations": citations, "mode": "retrieval-fallback",
            }

        return {
            "question": query, "refused": False, "reason": "",
            "confidence": confidence, "answer": llm_answer,
            "citations": citations, "mode": "rag",
        }

    @staticmethod
    def _refusal(query, reason, confidence, answer=REFUSAL_MESSAGE):
        return {
            "question": query, "refused": True, "reason": reason,
            "confidence": confidence, "answer": answer,
            "citations": [], "mode": "refusal",
        }

    @staticmethod
    def _format_snippets(results):
        blocks = []
        for number, result in enumerate(results, start=1):
            blocks.append(
                f"[{number}] {result.chunk.chunk_id}\n{mask_identifiers(result.chunk.text)}"
            )
        return "\n\n".join(blocks)

    def answer_retrieval_only(self, query, top_k=3):
        """Retrieval only mode with the vault refusal wording."""
        return self.ask(query, top_k=top_k, use_llm=False)["answer"]

    def answer_rag(self, query, top_k=3):
        """RAG mode: retrieval, redaction, generation, citation check."""
        if self.llm_client is None:
            raise RuntimeError(
                "RAG mode requires an LLM client. Provide a GeminiClient instance."
            )
        return self.ask(query, top_k=top_k, use_llm=True)["answer"]

    def stats(self):
        """Summary of what was indexed, by source."""
        chunks_by_source = Counter(chunk.source or "(root)" for chunk in self.chunks)
        return {
            "vault": self.vault_path,
            "files_indexed": self.load_stats.files_indexed,
            "files_skipped_private": self.load_stats.files_skipped_private,
            "files_skipped_excluded": self.load_stats.files_skipped_excluded,
            "chunks": len(self.chunks),
            "chunks_by_source": dict(chunks_by_source),
            "unique_terms": len(self.index),
        }


class WholeFileVaultBot(DocuBot):
    """
    The DocuBot baseline pointed at a vault: whole files, keyword counting,
    no confidence check. Exists so the evaluation can measure what the
    VaultBot changes are worth on the same notes with the same privacy rules.
    """

    def __init__(self, vault_path="sample_vault", exclude_globs=()):
        self.vault_path = vault_path
        self.exclude_globs = tuple(exclude_globs)
        super().__init__(docs_folder=vault_path, llm_client=None)

    def load_documents(self):
        notes, _ = load_notes(self.vault_path, self.exclude_globs)
        return notes
