"""
Guardrails for VaultBot.

A second brain holds personal facts, so a confident wrong answer is worse
than no answer. Each guardrail here covers one way the system could mislead
or leak:

1. validate_query       rejects empty or oversized input before any work
2. confidence refusal   (in vaultbot.py) refuses when the vault does not
                        cover the question, instead of returning weak matches
3. redact_sensitive     masks emails, phone numbers, and key shaped strings
                        before any snippet leaves the machine for an LLM
4. check_citations      rejects an LLM answer that cites nothing, or cites a
                        source number it was never given
"""

import re

REFUSAL_MESSAGE = "I do not know based on this vault."
MAX_QUERY_CHARS = 500

EMAIL_PATTERN = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")
PHONE_PATTERN = re.compile(r"(?<!\w)(?:\+?1[\s.-]?)?\(?\d{3}\)?[\s.-]\d{3}[\s.-]\d{4}(?!\w)")
SECRET_PATTERN = re.compile(
    r"\b(?:sk|pk|ghp|gho|xox[abp])[-_][A-Za-z0-9_-]{16,}\b"   # common API key prefixes
    r"|\b[A-Fa-f0-9]{32,}\b"                                   # long hex strings
)
CITATION_PATTERN = re.compile(r"\[(\d+)\]")


def validate_query(query):
    """
    Returns (is_valid, cleaned_query_or_reason).
    """
    if query is None:
        return False, "The question is empty."
    cleaned = " ".join(str(query).split())
    if not cleaned:
        return False, "The question is empty."
    if len(cleaned) > MAX_QUERY_CHARS:
        return False, f"The question is longer than {MAX_QUERY_CHARS} characters."
    if not re.search(r"[A-Za-z0-9]", cleaned):
        return False, "The question has no searchable words."
    return True, cleaned


def redact_sensitive(text):
    """Masks emails, phone numbers, and secret shaped strings."""
    text = EMAIL_PATTERN.sub("[REDACTED EMAIL]", text)
    text = PHONE_PATTERN.sub("[REDACTED PHONE]", text)
    text = SECRET_PATTERN.sub("[REDACTED SECRET]", text)
    return text


def check_citations(answer, source_count):
    """
    Returns (is_grounded, reason).

    An answer is grounded when it cites at least one source as [n] and every
    cited number refers to a snippet that was actually provided.
    """
    cited_numbers = {int(number) for number in CITATION_PATTERN.findall(answer)}
    if not cited_numbers:
        return False, "The answer cites no sources."
    invalid = sorted(number for number in cited_numbers if not 1 <= number <= source_count)
    if invalid:
        return False, f"The answer cites sources that were not provided: {invalid}"
    return True, "All citations refer to provided sources."
