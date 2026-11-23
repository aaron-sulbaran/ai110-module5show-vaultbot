"""Tests for the vault loader, VaultBot retrieval, guardrails, and the harness."""

import pytest

from dataset import VAULT_EVAL_CASES
from evaluation import evaluate_vault
from guardrails import (
    REFUSAL_MESSAGE,
    SENSITIVE_REFUSAL_MESSAGE,
    check_citations,
    detect_sensitive_request,
    mask_identifiers,
    redact_sensitive,
    validate_query,
)
from vault_loader import chunk_note, load_vault, parse_frontmatter, split_into_sections
from vaultbot import VaultBot, WholeFileVaultBot, tokenize

SAMPLE_VAULT = "sample_vault"


@pytest.fixture(scope="module")
def bot():
    return VaultBot(SAMPLE_VAULT)


class FakeLLM:
    """Stands in for GeminiClient so RAG paths run offline."""

    def __init__(self, reply):
        self.reply = reply
        self.received_snippets = None

    def answer_with_citations(self, query, numbered_snippets):
        self.received_snippets = numbered_snippets
        return self.reply


# ----- loader ---------------------------------------------------------------

def test_frontmatter_is_parsed_and_removed_from_body():
    frontmatter, body = parse_frontmatter("---\ntitle: Trailhead\nprivate: true\n---\n# Hello\n")
    assert frontmatter == {"title": "Trailhead", "private": "true"}
    assert body.startswith("# Hello")


def test_sections_follow_headings_and_ignore_code_fences():
    body = "# Top\nintro text\n## Setup\nrun it\n```\n# not a heading\n```\n## Usage\nask it\n"
    trails = [trail for trail, _ in split_into_sections(body)]
    assert trails == ["Top", "Top > Setup", "Top > Usage"]


def test_chunk_ids_point_at_the_section():
    chunks = chunk_note("wiki/a.md", "# A\n## Decisions\nWe chose SQLite for offline use.\n")
    assert chunks[0].chunk_id == "wiki/a.md#Decisions"
    assert chunks[0].source == "wiki"


def test_private_notes_are_never_indexed():
    chunks, stats = load_vault(SAMPLE_VAULT)
    assert stats.files_skipped_private == 1
    assert not any("journal" in chunk.path for chunk in chunks)
    assert not any("1450" in chunk.text for chunk in chunks)


def test_exclude_globs_remove_notes(tmp_path):
    (tmp_path / "keep.md").write_text("# Keep\nThis note stays in the index.\n")
    (tmp_path / "finances").mkdir()
    (tmp_path / "finances" / "taxes.md").write_text("# Taxes\nThis note is excluded.\n")
    chunks, stats = load_vault(str(tmp_path), exclude_globs=["finances/*"])
    assert [chunk.path for chunk in chunks] == ["keep.md"]
    assert stats.files_skipped_excluded == 1


# ----- retrieval ------------------------------------------------------------

def test_tokenize_folds_suffixes_and_drops_stopwords():
    assert tokenize("What are the reviews I reviewed?") == ["review", "review"]


def test_retrieval_returns_the_answering_section(bot):
    results = bot.search("When are Dr. Ortiz's office hours?")
    assert results[0].chunk.chunk_id == "wiki/people/dr-lena-ortiz.md#Working with her"
    assert "Tuesdays" in results[0].chunk.text


def test_source_filter_limits_results_to_one_source(bot):
    results = bot.search("Why was SQLite chosen over Postgres?", top_k=5, sources={"sources"})
    assert results and all(result.chunk.source == "sources" for result in results)


def test_source_weights_change_the_ranking():
    question = "storage decision Postgres signal"
    wiki_first = VaultBot(SAMPLE_VAULT, source_weights={"wiki": 1.0, "sources": 0.1})
    sources_first = VaultBot(SAMPLE_VAULT, source_weights={"wiki": 0.1, "sources": 1.0})
    assert wiki_first.search(question)[0].chunk.source == "wiki"
    assert sources_first.search(question)[0].chunk.source == "sources"


# ----- guardrails -----------------------------------------------------------

@pytest.mark.parametrize("question", [
    "What is the payment processing vendor for Trailhead?",
    "Who won the 2022 World Cup?",
    "How much is the rent on the new lease?",
])
def test_questions_outside_the_vault_are_refused(bot, question):
    result = bot.ask(question)
    assert result["refused"] and result["answer"] == REFUSAL_MESSAGE
    assert result["citations"] == []
    assert bot.retrieve(question) == []


@pytest.mark.parametrize("question", ["", "   ", "???", "x" * 501])
def test_invalid_questions_are_rejected_before_retrieval(bot, question):
    result = bot.ask(question)
    assert result["refused"] and result["confidence"] == 0.0


def test_validate_query_normalizes_whitespace():
    assert validate_query("  when   is  the demo ") == (True, "when is the demo")


def test_redaction_masks_emails_phones_and_keys():
    text = "Mail jordan@example.com or call 512-555-0147. Key sk-abcdefghijklmnopqrstuv."
    redacted = redact_sensitive(text)
    assert "example.com" not in redacted and "555" not in redacted and "sk-" not in redacted
    assert redacted.count("[REDACTED") == 3


def test_citation_check():
    assert check_citations("Tuesdays from 2 to 4 PM [1].", source_count=3)[0]
    assert not check_citations("Tuesdays from 2 to 4 PM.", source_count=3)[0]
    assert not check_citations("Tuesdays [7].", source_count=3)[0]


# ----- sensitive requests ---------------------------------------------------

@pytest.mark.parametrize("question", [
    "What is my bank routing number?",
    "What is my bank account number?",
    "what's my SSN",
    "What is my credit card number?",
    "What is my wifi password?",
    "Find the API key for the weather service",
    "What is my passport number?",
])
def test_sensitive_requests_are_refused_before_retrieval(bot, question):
    result = bot.ask(question)
    assert result["refused"] and result["answer"] == SENSITIVE_REFUSAL_MESSAGE
    assert result["citations"] == [] and "Sensitive request" in result["reason"]
    assert bot.retrieve(question) == []


def test_sensitive_block_holds_even_when_the_vault_matches_the_words(bot):
    question = "What is my bank routing number?"
    # The money-admin note makes plain retrieval confident about this question,
    # which is the failure seen on a real vault. Only the block stops it.
    assert bot.confidence_of(bot.search(question)) >= bot.min_confidence
    assert bot.ask(question)["refused"]


def test_sensitive_requests_never_reach_the_llm():
    llm = FakeLLM("It is 021000021 [1].")
    result = VaultBot(SAMPLE_VAULT, llm_client=llm).ask("What is my bank routing number?", use_llm=True)
    assert result["refused"] and llm.received_snippets is None


@pytest.mark.parametrize("question", [
    "How much do I move to savings each month?",
    "When is the Trailhead prototype demo due?",
    "Which bank is the checking account at?",
    "What are the account settings for the tile cache?",
])
def test_ordinary_questions_are_not_treated_as_sensitive(question):
    assert detect_sensitive_request(question) is None


def test_identifiers_are_masked_in_displayed_snippets(tmp_path):
    (tmp_path / "paperwork.md").write_text(
        "# Paperwork\n## Tax forms\nThe tax form lists SSN 123-45-6789 and "
        "routing number: 021000021 and card 4111 1111 1111 1111 for the refund.\n"
    )
    answer = VaultBot(str(tmp_path)).ask("What does the tax form list for the refund?")["answer"]
    assert "123-45-6789" not in answer and "021000021" not in answer and "4111" not in answer
    assert "[REDACTED ID]" in answer and "[REDACTED ACCOUNT]" in answer and "[REDACTED CARD]" in answer


def test_mask_identifiers_leaves_ordinary_numbers_alone():
    text = "Midterm 2 is April 9 in room 3.214. The demo is due 2026-04-17 at 10 AM."
    assert mask_identifiers(text) == text


# ----- RAG path with a fake LLM ---------------------------------------------

def test_grounded_llm_answer_is_returned():
    llm = FakeLLM("Office hours are Tuesdays from 2 to 4 PM [1].")
    result = VaultBot(SAMPLE_VAULT, llm_client=llm).ask("When are Dr. Ortiz's office hours?", use_llm=True)
    assert result["mode"] == "rag" and "[1]" in result["answer"]


def test_uncited_llm_answer_is_discarded():
    llm = FakeLLM("Office hours are Wednesdays at noon.")
    result = VaultBot(SAMPLE_VAULT, llm_client=llm).ask("When are Dr. Ortiz's office hours?", use_llm=True)
    assert result["mode"] == "retrieval-fallback"
    assert "Wednesdays" not in result["answer"] and "Tuesdays" in result["answer"]


def test_llm_is_not_called_when_retrieval_refuses():
    llm = FakeLLM("Paris [1].")
    result = VaultBot(SAMPLE_VAULT, llm_client=llm).ask("Who won the 2022 World Cup?", use_llm=True)
    assert result["refused"] and llm.received_snippets is None


def test_snippets_are_redacted_before_reaching_the_llm(tmp_path):
    (tmp_path / "contacts.md").write_text(
        "# Contacts\n## Landlord\nThe landlord email is pat@example.com and the phone is 512-555-0147.\n"
    )
    llm = FakeLLM("See the landlord contact [1].")
    VaultBot(str(tmp_path), llm_client=llm).ask("What is the landlord email and phone?", use_llm=True)
    sent_text = " ".join(text for _, _, text in llm.received_snippets)
    assert "pat@example.com" not in sent_text and "555-0147" not in sent_text


# ----- evaluation harness ---------------------------------------------------

def test_vaultbot_passes_every_evaluation_case():
    summary, _ = evaluate_vault(VaultBot(SAMPLE_VAULT), VAULT_EVAL_CASES)
    assert summary["passed"] == summary["total"] == len(VAULT_EVAL_CASES)


def test_baseline_never_refuses_and_sends_more_context():
    baseline, _ = evaluate_vault(WholeFileVaultBot(SAMPLE_VAULT), VAULT_EVAL_CASES)
    vaultbot, _ = evaluate_vault(VaultBot(SAMPLE_VAULT), VAULT_EVAL_CASES)
    assert baseline["refusals_passed"] == 0
    assert vaultbot["average_context_chars"] < baseline["average_context_chars"]


# ----- repository hygiene ---------------------------------------------------

def test_readme_outputs_come_from_the_sample_vault():
    """Sample output in the README must never be generated from a real vault."""
    with open("README.md", encoding="utf8") as readme_file:
        readme = readme_file.read()
    assert "vault: sample_vault" in readme
    assert "/Users/" not in readme and "/home/" not in readme
