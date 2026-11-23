"""
A lightweight evaluation harness for DocuBot.

This module helps students compare:
- naive generation over the full docs
- retrieval only answers
- RAG answers (retrieval + Gemini)

The evaluation is intentionally simple: it checks whether DocuBot retrieves
the correct files for each query and reports a hit rate.
"""

from dataset import SAMPLE_QUERIES


# -----------------------------------------------------------
# Expected document signals for evaluation
# -----------------------------------------------------------
# This dictionary maps a query substring to the filename(s)
# that should be relevant. It does NOT need to be perfect.
# It simply gives students a way to measure improvements.
#
# Example:
#   If a query contains the phrase "auth token",
#   evaluation expects AUTH.md to appear in the retrieval results.
#
EXPECTED_SOURCES = {
    "auth token": ["AUTH.md"],
    "environment variables": ["AUTH.md"],
    "database": ["DATABASE.md"],
    "users": ["API_REFERENCE.md"],
    "projects": ["API_REFERENCE.md"],
    "refresh": ["AUTH.md"],
    "users table": ["DATABASE.md"],
}


def expected_files_for_query(query):
    """
    Returns a list of expected filenames based on simple substring matching.
    """
    query_lower = query.lower()
    matches = []
    for key, files in EXPECTED_SOURCES.items():
        if key in query_lower:
            matches.extend(files)
    return matches


# -----------------------------------------------------------
# Evaluation function
# -----------------------------------------------------------

def evaluate_retrieval(bot, top_k=3):
    """
    Runs DocuBot's retrieval system against SAMPLE_QUERIES.
    Returns a tuple: (hit_rate, detailed_results)

    hit_rate: fraction of queries where at least one retrieved snippet's
              filename matched an expected filename.
    detailed_results: list of dictionaries with structured info.
    """
    results = []
    hits = 0

    for query in SAMPLE_QUERIES:
        expected = expected_files_for_query(query)
        retrieved = bot.retrieve(query, top_k=top_k)

        retrieved_files = [fname for fname, _ in retrieved]

        hit = any(f in retrieved_files for f in expected) if expected else False
        if hit:
            hits += 1

        results.append({
            "query": query,
            "expected": expected,
            "retrieved": retrieved_files,
            "hit": hit
        })

    hit_rate = hits / len(SAMPLE_QUERIES)
    return hit_rate, results


# -----------------------------------------------------------
# Pretty printing
# -----------------------------------------------------------

def print_eval_results(hit_rate, results):
    """
    Nicely formats evaluation results.
    """
    print("\nEvaluation Results")
    print("------------------")
    print(f"Hit rate: {hit_rate:.2f}\n")

    for item in results:
        print(f"Query: {item['query']}")
        print(f"  Expected:  {item['expected']}")
        print(f"  Retrieved: {item['retrieved']}")
        print(f"  Hit:       {item['hit']}")
        print()


# -----------------------------------------------------------
# Optional CLI entry point
# -----------------------------------------------------------

if __name__ == "__main__" and "--vault" not in __import__("sys").argv:
    from docubot import DocuBot

    print("Running retrieval evaluation...\n")
    bot = DocuBot()

    hit_rate, results = evaluate_retrieval(bot)
    print_eval_results(hit_rate, results)


# ===========================================================
# VaultBot evaluation harness
# ===========================================================
# Runs every case in dataset.VAULT_EVAL_CASES against two systems on the
# same vault:
#   baseline  the DocuBot approach (whole files, keyword counting)
#   vaultbot  heading chunks, BM25, source weights, confidence refusal
#
# Run it with:  python evaluation.py --vault
# Exit code is 0 when every VaultBot case passes, 1 otherwise.

REFUSAL_PREFIX = "I do not know"


def run_vault_case(bot, case, top_k=3):
    """
    Runs one case against one bot. Works for both bots because both expose
    the DocuBot retrieve() interface: a list of (label, text).
    """
    retrieved = bot.retrieve(case["question"], top_k=top_k)
    refused = not retrieved
    labels = [label for label, _ in retrieved]
    retrieved_paths = [label.split("#")[0] for label in labels]
    context = "\n".join(text for _, text in retrieved)

    if case.get("should_refuse"):
        passed = refused
        detail = "refused" if refused else f"answered from {retrieved_paths[0]}"
    else:
        found_note = case["expected_path"] in retrieved_paths
        found_answer = case["expected_text"].lower() in context.lower()
        passed = found_note and found_answer
        if refused:
            detail = "refused an answerable question"
        elif not found_note:
            detail = f"wrong notes: {retrieved_paths}"
        elif not found_answer:
            detail = "right note, answer text missing"
        else:
            detail = labels[retrieved_paths.index(case["expected_path"])]

    return {
        "question": case["question"],
        "should_refuse": bool(case.get("should_refuse")),
        "passed": passed,
        "top_hit": (not refused and not case.get("should_refuse")
                    and retrieved_paths[0] == case["expected_path"]),
        "context_chars": len(context),
        "detail": detail,
    }


def evaluate_vault(bot, cases, top_k=3):
    """Returns (summary dict, per case results) for one bot."""
    results = [run_vault_case(bot, case, top_k=top_k) for case in cases]
    answerable = [r for r in results if not r["should_refuse"]]
    refusals = [r for r in results if r["should_refuse"]]
    answered = [r for r in answerable if r["context_chars"]]
    summary = {
        "passed": sum(r["passed"] for r in results),
        "total": len(results),
        "answerable_passed": sum(r["passed"] for r in answerable),
        "answerable_total": len(answerable),
        "top_hit": sum(r["top_hit"] for r in answerable),
        "refusals_passed": sum(r["passed"] for r in refusals),
        "refusals_total": len(refusals),
        "average_context_chars": (
            round(sum(r["context_chars"] for r in answered) / len(answered))
            if answered else 0
        ),
    }
    return summary, results


def print_vault_report(name, summary, results):
    print(f"\n{name}")
    print("-" * len(name))
    for result in results:
        status = "PASS" if result["passed"] else "FAIL"
        kind = "refuse" if result["should_refuse"] else "answer"
        print(f"  {status}  [{kind}] {result['question']}")
        print(f"        {result['detail']}")
    print(f"  Answerable questions passed: {summary['answerable_passed']}/{summary['answerable_total']}")
    print(f"  Correct note ranked first:   {summary['top_hit']}/{summary['answerable_total']}")
    print(f"  Correct refusals:            {summary['refusals_passed']}/{summary['refusals_total']}")
    print(f"  Average context size:        {summary['average_context_chars']} characters")
    print(f"  TOTAL: {summary['passed']}/{summary['total']} passed")


def run_vault_evaluation(vault_path="sample_vault", top_k=3):
    """Runs baseline and VaultBot on the same cases and prints a comparison."""
    from dataset import VAULT_EVAL_CASES
    from vaultbot import VaultBot, WholeFileVaultBot

    systems = [
        ("Baseline (DocuBot: whole files, keyword count)", WholeFileVaultBot(vault_path)),
        ("VaultBot (heading chunks, BM25, source weights, refusal)", VaultBot(vault_path)),
    ]
    summaries = {}
    for name, bot in systems:
        summary, results = evaluate_vault(bot, VAULT_EVAL_CASES, top_k=top_k)
        summaries[name] = summary
        print_vault_report(name, summary, results)

    baseline_summary, vaultbot_summary = summaries.values()
    print("\nSummary")
    print("-------")
    print(f"  Baseline: {baseline_summary['passed']}/{baseline_summary['total']} passed")
    print(f"  VaultBot: {vaultbot_summary['passed']}/{vaultbot_summary['total']} passed")
    all_passed = vaultbot_summary["passed"] == vaultbot_summary["total"]
    print(f"  RESULT: {'PASS' if all_passed else 'FAIL'}")
    return all_passed


if __name__ == "__main__" and "--vault" in __import__("sys").argv:
    raise SystemExit(0 if run_vault_evaluation() else 1)
