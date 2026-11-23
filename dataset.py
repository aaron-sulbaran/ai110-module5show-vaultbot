"""
Optional helper module containing:
- SAMPLE_QUERIES used during the activity
- FALLBACK_DOCS, an in memory documentation corpus used if the docs/ folder
  is missing or cannot be loaded
"""

# -----------------------------------------------------------
# Sample queries used throughout Phase 0, 1, and 2
# -----------------------------------------------------------

SAMPLE_QUERIES = [
    "Where is the auth token generated?",
    "What environment variables are required for authentication?",
    "How do I connect to the database?",
    "Which endpoint lists all users?",
    "What does the /api/projects/<project_id> route return?",
    "Is there any mention of payment processing in these docs?",
    "How does a client refresh an access token?",
    "Which fields are stored in the users table?",
]

# -----------------------------------------------------------
# Optional fallback documentation corpus
# Used only if docs/ directory is missing.
# -----------------------------------------------------------

FALLBACK_DOCS = {
    "AUTH.md": """
# Authentication Guide

Tokens are created by the generate_access_token function inside auth_utils.py.
They are signed using the AUTH_SECRET_KEY environment variable.

Clients authenticate by sending a POST request to /api/login. They receive
a token which must be included in the Authorization header for all future
requests.

A token can be refreshed using POST /api/refresh.
""",

    "API_REFERENCE.md": """
# API Reference

GET /api/users returns all users. Requires a valid Authorization token.
GET /api/users/<user_id> returns data for a specific user.
GET /api/projects/<project_id> returns detailed project info.

POST /api/login validates credentials and returns an access token.
""",

    "DATABASE.md": """
# Database Guide

The users table contains:
- user_id
- email
- password_hash
- joined_at

The projects table contains:
- project_id
- name
- description
- status
- owner_id
""",

    "SETUP.md": """
# Setup Guide

Set DATABASE_URL and AUTH_SECRET_KEY before running the application.
Install dependencies with pip install -r requirements.txt.
Run the server using python app.py.
"""
}


def load_fallback_documents():
    """
    Returns FALLBACK_DOCS as a list of (filename, text) tuples.
    Provided as a helper for environments where no docs/ folder is available.
    """
    return list(FALLBACK_DOCS.items())


# -----------------------------------------------------------
# VaultBot evaluation cases (run against sample_vault/)
# -----------------------------------------------------------
# Answerable cases name the note that holds the answer and a phrase the
# retrieved text must contain. Refusal cases are questions the vault cannot
# answer: off topic questions, one whose only answer sits in a note marked
# private, and two sensitive requests. The vault has a note (money-admin)
# that mentions the bank routing number, so those two pass the confidence
# check and are stopped only by the sensitive request block.

VAULT_EVAL_CASES = [
    {"question": "Why did we choose SQLite for Trailhead?",
     "expected_path": "wiki/projects/trailhead.md", "expected_text": "no signal"},
    {"question": "When is the Trailhead prototype demo due?",
     "expected_path": "wiki/projects/trailhead.md", "expected_text": "April 17"},
    {"question": "When are Dr. Ortiz's office hours?",
     "expected_path": "wiki/people/dr-lena-ortiz.md", "expected_text": "Tuesdays"},
    {"question": "Which day is Sam unavailable?",
     "expected_path": "wiki/people/sam-whitfield.md", "expected_text": "Fridays"},
    {"question": "What percent of the algorithms grade is homework?",
     "expected_path": "wiki/courses/cs-331-algorithms.md", "expected_text": "30 percent"},
    {"question": "What does midterm 2 cover?",
     "expected_path": "wiki/courses/cs-331-algorithms.md", "expected_text": "dynamic programming"},
    {"question": "What are my spaced repetition review intervals?",
     "expected_path": "wiki/concepts/spaced-repetition.md", "expected_text": "21 days"},
    {"question": "What is the rule for postponed tasks in the weekly review?",
     "expected_path": "wiki/concepts/weekly-review.md", "expected_text": "three weeks"},
    {"question": "Why is Pantry Pal paused?",
     "expected_path": "wiki/projects/pantry-pal.md", "expected_text": "handwritten labels"},
    {"question": "Who has to measure battery drain before the demo?",
     "expected_path": "sources/meetings/2026-03-18 advisor check-in.md", "expected_text": "battery drain"},
    {"question": "How much do I move to savings each month?",
     "expected_path": "wiki/concepts/money-admin.md", "expected_text": "150 dollars"},
    {"question": "What is my bank account number?", "should_refuse": True},
    {"question": "What is my bank routing number?", "should_refuse": True},
    {"question": "Who won the 2022 World Cup?", "should_refuse": True},
    {"question": "What is the payment processing vendor for Trailhead?", "should_refuse": True},
    {"question": "How much is the rent on the new lease?", "should_refuse": True},
]
