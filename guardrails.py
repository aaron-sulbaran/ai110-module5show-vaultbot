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
5. detect_sensitive_request
                        refuses questions that ask for credentials or for
                        financial or government identifiers, no matter what
                        the vault contains. A confidence score cannot do this
                        job: in a large vault the words "bank", "routing",
                        and "number" really do appear together somewhere.
6. mask_identifiers     masks SSN, card, and account shaped numbers in every
                        snippet that is displayed, including local mode
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

SENSITIVE_REFUSAL_MESSAGE = (
    "I will not look up credentials or financial and government identifiers."
)

# (category, pattern). Matched against the lowercased question.
SENSITIVE_REQUEST_PATTERNS = [
    ("bank details", re.compile(
        r"\b(bank|checking|savings)\s+(account\s+)?(login|details|credentials)\b"
        r"|\brouting\s+(number|no)\b|\baccount\s+(number|no)\b|\biban\b|\bswift\s+code\b")),
    ("payment card", re.compile(
        r"\b(credit|debit)\s+card\s+(number|no|details|info)\b|\bcard\s+number\b"
        r"|\bcvv\b|\bcvc\b|\bpin\s+(code|number)\b")),
    ("government ID", re.compile(
        r"\bsocial\s+security\b|\bssn\b|\bpassport\s+(number|no)\b"
        r"|\bdriver'?s?\s+licen[sc]e\s+(number|no)\b|\btax(payer)?\s+id\b")),
    ("credentials", re.compile(
        r"\bpasswords?\b|\bpasscodes?\b|\bpassphrases?\b|\bapi\s+keys?\b|\bsecret\s+keys?\b"
        r"|\bprivate\s+keys?\b|\baccess\s+tokens?\b|\b(recovery|seed|backup)\s+(code|phrase)s?\b"
        r"|\b(2fa|two\s+factor)\s+codes?\b")),
]

SSN_PATTERN = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
CARD_PATTERN = re.compile(r"\b\d{4}[ -]\d{4}[ -]\d{4}[ -]\d{4}\b|\b\d{16}\b")
ACCOUNT_PATTERN = re.compile(
    r"(?i)(\b(?:routing|account|acct|iban)\b[^\n\d]{0,24})(\d[\d -]{5,20}\d)"
)


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


def detect_sensitive_request(query):
    """
    Returns the category name when a question asks for a credential or a
    financial or government identifier, else None.
    """
    lowered = " ".join(str(query).lower().split())
    for category, pattern in SENSITIVE_REQUEST_PATTERNS:
        if pattern.search(lowered):
            return category
    return None


def mask_identifiers(text):
    """Masks SSN, card, account, and key shaped strings. Used on all output."""
    text = SSN_PATTERN.sub("[REDACTED ID]", text)
    text = CARD_PATTERN.sub("[REDACTED CARD]", text)
    text = ACCOUNT_PATTERN.sub(lambda match: match.group(1) + "[REDACTED ACCOUNT]", text)
    text = SECRET_PATTERN.sub("[REDACTED SECRET]", text)
    return text


def redact_sensitive(text):
    """Everything mask_identifiers does, plus emails and phone numbers."""
    text = mask_identifiers(text)
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
