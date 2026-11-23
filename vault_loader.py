"""
Loads a markdown vault (an Obsidian style "second brain") for retrieval.

DocuBot read a flat docs/ folder and treated each file as one document.
A personal vault is different: it is nested, it mixes curated pages with raw
sources, single files cover many topics, and some notes must never be indexed.

This module handles those differences:
- walks the vault recursively
- skips excluded folders, excluded glob patterns, and notes marked private
- parses simple YAML frontmatter
- splits each note into heading level chunks so retrieval can return the
  section that answers a question instead of a whole file
"""

import fnmatch
import os
import re
from dataclasses import dataclass

DEFAULT_EXCLUDED_DIRS = frozenset({
    ".git", ".obsidian", ".trash", ".claude", ".venv",
    "_archive", "attachments", "node_modules",
})

# A note opts out of indexing with either of these frontmatter flags.
PRIVATE_FRONTMATTER_KEYS = ("private", "no_rag")

MAX_CHUNK_CHARS = 1200
MIN_CHUNK_CHARS = 20

HEADING_PATTERN = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
CODE_FENCE_PATTERN = re.compile(r"^\s*(```|~~~)")


@dataclass(frozen=True)
class Chunk:
    """One retrievable section of one note."""
    chunk_id: str   # "wiki/projects/trailhead.md#Decisions"
    path: str       # vault relative path, always with forward slashes
    source: str     # top level folder ("wiki", "sources"), "" for root notes
    title: str      # note title (frontmatter title, else file name)
    heading: str    # heading trail inside the note, "Trailhead > Decisions"
    text: str       # section body


@dataclass
class LoadStats:
    files_indexed: int = 0
    files_skipped_private: int = 0
    files_skipped_excluded: int = 0
    chunks: int = 0


def parse_frontmatter(raw_text):
    """
    Splits a note into (frontmatter dict, body).

    Only flat "key: value" pairs are understood, which is all the loader
    needs (title, private, no_rag). Anything else in the block is ignored.
    """
    if not raw_text.startswith("---"):
        return {}, raw_text

    lines = raw_text.split("\n")
    if lines[0].strip() != "---":
        return {}, raw_text

    for line_number in range(1, len(lines)):
        if lines[line_number].strip() == "---":
            frontmatter = {}
            for line in lines[1:line_number]:
                if ":" not in line or line.startswith((" ", "\t", "-")):
                    continue
                key, _, value = line.partition(":")
                frontmatter[key.strip().lower()] = value.strip().strip("\"'")
            body = "\n".join(lines[line_number + 1:])
            return frontmatter, body

    return {}, raw_text


def is_private(frontmatter):
    """True when the note's frontmatter opts it out of indexing."""
    return any(
        frontmatter.get(key, "").lower() in ("true", "yes", "1")
        for key in PRIVATE_FRONTMATTER_KEYS
    )


def split_into_sections(body):
    """
    Splits a note body at markdown headings.

    Returns a list of (heading_trail, section_text). Headings inside fenced
    code blocks are ignored so a "# comment" in a code sample does not start
    a new section.
    """
    sections = []
    heading_stack = []          # list of (level, heading text)
    current_lines = []
    inside_code_fence = False

    def flush():
        text = "\n".join(current_lines).strip()
        if text:
            trail = " > ".join(heading for _, heading in heading_stack)
            sections.append((trail, text))

    for line in body.split("\n"):
        if CODE_FENCE_PATTERN.match(line):
            inside_code_fence = not inside_code_fence
            current_lines.append(line)
            continue

        heading_match = None if inside_code_fence else HEADING_PATTERN.match(line)
        if heading_match:
            flush()
            current_lines = []
            level = len(heading_match.group(1))
            while heading_stack and heading_stack[-1][0] >= level:
                heading_stack.pop()
            heading_stack.append((level, heading_match.group(2).strip()))
        else:
            current_lines.append(line)

    flush()
    return sections


def split_long_text(text, max_chars=MAX_CHUNK_CHARS):
    """Splits an oversized section on paragraph boundaries."""
    if len(text) <= max_chars:
        return [text]

    pieces = []
    current = ""
    for paragraph in re.split(r"\n\s*\n", text):
        candidate = f"{current}\n\n{paragraph}" if current else paragraph
        if len(candidate) <= max_chars:
            current = candidate
            continue
        if current:
            pieces.append(current)
        # A single paragraph longer than the limit is cut hard.
        while len(paragraph) > max_chars:
            pieces.append(paragraph[:max_chars])
            paragraph = paragraph[max_chars:]
        current = paragraph
    if current:
        pieces.append(current)
    return pieces


def chunk_note(relative_path, raw_text):
    """Turns one note into a list of Chunk objects."""
    frontmatter, body = parse_frontmatter(raw_text)
    file_stem = os.path.splitext(os.path.basename(relative_path))[0]
    title = frontmatter.get("title") or file_stem
    source = relative_path.split("/")[0] if "/" in relative_path else ""

    chunks = []
    for heading_trail, section_text in split_into_sections(body):
        for piece_number, piece in enumerate(split_long_text(section_text)):
            if len(piece) < MIN_CHUNK_CHARS:
                continue
            anchor = heading_trail.split(" > ")[-1] if heading_trail else "top"
            chunk_id = f"{relative_path}#{anchor}"
            if piece_number:
                chunk_id += f" (part {piece_number + 1})"
            chunks.append(Chunk(
                chunk_id=chunk_id,
                path=relative_path,
                source=source,
                title=title,
                heading=heading_trail,
                text=piece,
            ))
    return chunks


def iter_note_paths(vault_path, exclude_globs=(), stats=None):
    """
    Yields (absolute_path, relative_path) for every indexable .md or .txt note.
    Excluded folders are never descended into.
    """
    for folder, subfolders, files in os.walk(vault_path):
        subfolders[:] = sorted(
            name for name in subfolders if name not in DEFAULT_EXCLUDED_DIRS
        )
        for file_name in sorted(files):
            if not file_name.endswith((".md", ".txt")):
                continue
            absolute_path = os.path.join(folder, file_name)
            relative_path = os.path.relpath(absolute_path, vault_path).replace(os.sep, "/")
            if any(fnmatch.fnmatch(relative_path, pattern) for pattern in exclude_globs):
                if stats is not None:
                    stats.files_skipped_excluded += 1
                continue
            yield absolute_path, relative_path


def load_notes(vault_path, exclude_globs=()):
    """
    Returns (notes, stats) where notes is a list of (relative_path, raw_text)
    for every note that is allowed to be indexed.
    """
    if not os.path.isdir(vault_path):
        raise FileNotFoundError(f"Vault folder not found: {vault_path}")

    stats = LoadStats()
    notes = []
    for absolute_path, relative_path in iter_note_paths(vault_path, exclude_globs, stats):
        try:
            with open(absolute_path, "r", encoding="utf8", errors="replace") as note_file:
                raw_text = note_file.read()
        except OSError:
            stats.files_skipped_excluded += 1
            continue

        frontmatter, _ = parse_frontmatter(raw_text)
        if is_private(frontmatter):
            stats.files_skipped_private += 1
            continue

        notes.append((relative_path, raw_text))
        stats.files_indexed += 1
    return notes, stats


def load_vault(vault_path, exclude_globs=()):
    """Returns (chunks, stats) for the whole vault."""
    notes, stats = load_notes(vault_path, exclude_globs)
    chunks = []
    for relative_path, raw_text in notes:
        chunks.extend(chunk_note(relative_path, raw_text))
    stats.chunks = len(chunks)
    return chunks, stats
