"""
CLI runner for VaultBot (DocuBot extended to a personal vault).

One shot commands:
    python main.py ask "When are Dr. Ortiz's office hours?"
    python main.py ask "..." --llm          retrieval plus Gemini, with citations
    python main.py ask "..." --source wiki  search one source only
    python main.py stats                    what was indexed
    python main.py eval                     run the evaluation harness

With no command it opens the original DocuBot menu:
1. Naive LLM generation over all notes
2. Retrieval only
3. RAG: retrieval plus LLM generation

The vault is sample_vault/ unless VAULT_PATH is set (in the shell or .env)
or --vault is passed.
"""

import argparse
import json
import os
import sys

from dotenv import load_dotenv
load_dotenv()

from vaultbot import VaultBot
from llm_client import GeminiClient
from dataset import VAULT_EVAL_CASES

SAMPLE_QUERIES = [case["question"] for case in VAULT_EVAL_CASES]


def try_create_llm_client():
    """
    Tries to create a GeminiClient.
    Returns (llm_client, has_llm: bool).
    """
    try:
        client = GeminiClient()
        return client, True
    except RuntimeError as exc:
        print("Warning: LLM features are disabled.")
        print(f"Reason: {exc}")
        print("You can still run retrieval only mode.\n")
        return None, False


def choose_mode(has_llm):
    """
    Asks the user which mode to run.
    Returns "1", "2", "3", or "q".
    """
    print("Choose a mode:")
    if has_llm:
        print("  1) Naive LLM over full docs (no retrieval)")
    else:
        print("  1) Naive LLM over full docs (unavailable, no GEMINI_API_KEY)")
    print("  2) Retrieval only (no LLM)")
    if has_llm:
        print("  3) RAG (retrieval + LLM)")
    else:
        print("  3) RAG (unavailable, no GEMINI_API_KEY)")
    print("  q) Quit")

    choice = input("Enter choice: ").strip().lower()
    return choice


def get_query_or_use_samples():
    """
    Ask the user if they want to run all sample queries or a single custom query.

    Returns:
        queries: list of strings
        label: short description of the source of queries
    """
    print("\nPress Enter to run built in sample queries.")
    custom = input("Or type a single custom query: ").strip()

    if custom:
        return [custom], "custom query"
    else:
        return SAMPLE_QUERIES, "sample queries"


def run_naive_llm_mode(bot, has_llm):
    """
    Mode 1:
    Naive LLM generation over the full docs corpus.
    """
    if not has_llm or bot.llm_client is None:
        print("\nNaive LLM mode is not available (no GEMINI_API_KEY).\n")
        return

    queries, label = get_query_or_use_samples()
    print(f"\nRunning naive LLM mode on {label}...\n")

    all_text = bot.full_corpus_text()

    for query in queries:
        print("=" * 60)
        print(f"Question: {query}\n")
        answer = bot.llm_client.naive_answer_over_full_docs(query, all_text)
        print("Answer:")
        print(answer)
        print()


def run_retrieval_only_mode(bot):
    """
    Mode 2:
    Retrieval only answers. No LLM involved.
    """
    queries, label = get_query_or_use_samples()
    print(f"\nRunning retrieval only mode on {label}...\n")

    for query in queries:
        print("=" * 60)
        print(f"Question: {query}\n")
        answer = bot.answer_retrieval_only(query)
        print("Retrieved snippets:")
        print(answer)
        print()


def run_rag_mode(bot, has_llm):
    """
    Mode 3:
    Retrieval plus LLM generation.
    """
    if not has_llm or bot.llm_client is None:
        print("\nRAG mode is not available (no GEMINI_API_KEY).\n")
        return

    queries, label = get_query_or_use_samples()
    print(f"\nRunning RAG mode on {label}...\n")

    for query in queries:
        print("=" * 60)
        print(f"Question: {query}\n")
        answer = bot.answer_rag(query)
        print("Answer:")
        print(answer)
        print()


def build_bot(vault_path, llm_client=None):
    """Builds a VaultBot, honoring VAULT_EXCLUDE (comma separated globs)."""
    exclude_globs = [
        pattern.strip()
        for pattern in os.getenv("VAULT_EXCLUDE", "").split(",")
        if pattern.strip()
    ]
    return VaultBot(vault_path, llm_client=llm_client, exclude_globs=exclude_globs)


def run_ask(args):
    llm_client = None
    if args.llm:
        llm_client, has_llm = try_create_llm_client()
        if not has_llm:
            print("Falling back to retrieval only.\n")

    bot = build_bot(args.vault, llm_client=llm_client)
    sources = set(args.source) if args.source else None
    result = bot.ask(args.question, top_k=args.top_k, use_llm=args.llm, sources=sources)

    if args.json:
        print(json.dumps(result, indent=2))
        return 0

    print(f"Question:   {result['question']}")
    print(f"Mode:       {result['mode']}")
    print(f"Confidence: {result['confidence']:.2f} (threshold {bot.min_confidence:.2f})")
    if result["reason"]:
        print(f"Note:       {result['reason']}")
    print()
    print(result["answer"])
    if result["mode"] == "rag":
        print("\nSources:")
        for number, citation in enumerate(result["citations"], start=1):
            print(f"  [{number}] {citation}")
    return 0


def run_stats(args):
    bot = build_bot(args.vault)
    for key, value in bot.stats().items():
        print(f"{key}: {value}")
    return 0


def run_eval(args):
    from evaluation import run_vault_evaluation
    return 0 if run_vault_evaluation() else 1


def run_interactive(vault_path):
    print("VaultBot (interactive)")
    print("======================\n")

    llm_client, has_llm = try_create_llm_client()
    bot = build_bot(vault_path, llm_client=llm_client)

    while True:
        choice = choose_mode(has_llm)

        if choice == "q":
            print("\nGoodbye.")
            break
        elif choice == "1":
            run_naive_llm_mode(bot, has_llm)
        elif choice == "2":
            run_retrieval_only_mode(bot)
        elif choice == "3":
            run_rag_mode(bot, has_llm)
        else:
            print("\nUnknown choice. Please pick 1, 2, 3, or q.\n")


def main(argv=None):
    default_vault = os.getenv("VAULT_PATH", "sample_vault")

    parser = argparse.ArgumentParser(description="Ask questions of a markdown vault.")
    parser.add_argument("--vault", default=default_vault, help="path to the vault folder")
    commands = parser.add_subparsers(dest="command")

    ask = commands.add_parser("ask", help="answer one question")
    ask.add_argument("question")
    ask.add_argument("--top-k", type=int, default=3)
    ask.add_argument("--llm", action="store_true", help="generate an answer with Gemini")
    ask.add_argument("--source", action="append", help="limit to a top level folder, repeatable")
    ask.add_argument("--json", action="store_true", help="print the raw result")
    ask.set_defaults(handler=run_ask)

    stats = commands.add_parser("stats", help="show what was indexed")
    stats.set_defaults(handler=run_stats)

    evaluate = commands.add_parser("eval", help="run the evaluation harness on sample_vault")
    evaluate.set_defaults(handler=run_eval)

    args = parser.parse_args(argv)
    if args.command is None:
        run_interactive(args.vault)
        return 0
    return args.handler(args)


if __name__ == "__main__":
    sys.exit(main())
