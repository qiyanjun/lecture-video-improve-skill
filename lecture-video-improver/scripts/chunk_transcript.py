#!/usr/bin/env python3
"""Split a cleaned English transcript into sentence-safe JSON chunks."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path


ABBREVIATIONS = {
    "mr.", "mrs.", "ms.", "dr.", "prof.", "sr.", "jr.", "st.",
    "vs.", "etc.", "e.g.", "i.e.", "fig.", "eq.", "no.", "inc.",
    "u.s.", "u.k.", "ph.d.",
}
CLOSERS = "\"'”’)]}"
BOUNDARIES = ".!?"


@dataclass(frozen=True)
class Unit:
    text: str
    paragraph: int


def is_boundary(text: str, index: int) -> bool:
    char = text[index]
    if char not in BOUNDARIES:
        return False
    if char == ".":
        if index > 0 and index + 1 < len(text):
            if text[index - 1].isdigit() and text[index + 1].isdigit():
                return False
        prefix = text[: index + 1]
        match = re.search(r"([A-Za-z](?:[A-Za-z.]*)\.)$", prefix)
        if match and match.group(1).lower() in ABBREVIATIONS:
            return False
        if index + 1 < len(text) and text[index + 1] == ".":
            return False
    cursor = index + 1
    while cursor < len(text) and text[cursor] in CLOSERS:
        cursor += 1
    return cursor == len(text) or text[cursor].isspace()


def split_sentences(paragraph: str) -> list[str]:
    paragraph = re.sub(r"[ \t]+", " ", paragraph.strip())
    if not paragraph:
        return []
    sentences: list[str] = []
    start = 0
    index = 0
    while index < len(paragraph):
        if is_boundary(paragraph, index):
            end = index + 1
            while end < len(paragraph) and paragraph[end] in CLOSERS:
                end += 1
            sentence = paragraph[start:end].strip()
            if sentence:
                sentences.append(sentence)
            start = end
            while start < len(paragraph) and paragraph[start].isspace():
                start += 1
            index = start
            continue
        index += 1
    tail = paragraph[start:].strip()
    if tail:
        sentences.append(tail)
    return sentences


def build_units(text: str, max_chars: int) -> list[Unit]:
    paragraphs = [p for p in re.split(r"\n\s*\n", text) if p.strip()]
    units: list[Unit] = []
    for paragraph_index, paragraph in enumerate(paragraphs):
        for sentence in split_sentences(paragraph):
            if len(sentence) > max_chars:
                raise ValueError(
                    f"Sentence in paragraph {paragraph_index + 1} has "
                    f"{len(sentence)} characters, above max {max_chars}. "
                    "Rewrite it or explicitly raise --max-chars."
                )
            units.append(Unit(sentence, paragraph_index))
    if not units:
        raise ValueError("The transcript contains no text.")
    return units


def render_units(units: list[Unit], start: int, end: int) -> str:
    pieces = [units[start].text]
    for index in range(start + 1, end):
        separator = "\n\n" if units[index].paragraph != units[index - 1].paragraph else " "
        pieces.append(separator)
        pieces.append(units[index].text)
    return "".join(pieces)


def partition(
    units: list[Unit], min_chars: int, target_chars: int, max_chars: int
) -> list[str]:
    count = len(units)
    best_cost = [float("inf")] * (count + 1)
    next_break = [-1] * (count + 1)
    best_cost[count] = 0.0

    for start in range(count - 1, -1, -1):
        for end in range(start + 1, count + 1):
            text = render_units(units, start, end)
            length = len(text)
            if length > max_chars:
                break
            distance = (length - target_chars) / max(target_chars, 1)
            penalty = distance * distance
            if length < min_chars:
                penalty += (
                    1.0
                    if end == count
                    else 100.0 + (min_chars - length) / min_chars
                )
            penalty += 0.001
            cost = penalty + best_cost[end]
            if cost < best_cost[start]:
                best_cost[start] = cost
                next_break[start] = end

    if next_break[0] == -1:
        raise ValueError(
            "No sentence-safe partition fits the requested character limits."
        )

    chunks: list[str] = []
    cursor = 0
    while cursor < count:
        end = next_break[cursor]
        if end <= cursor:
            raise RuntimeError("Internal partition error.")
        chunks.append(render_units(units, cursor, end))
        cursor = end
    return chunks


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="Cleaned UTF-8 English transcript")
    parser.add_argument("output", type=Path, help="Destination chunks JSON")
    parser.add_argument("--min-chars", type=int, default=1500)
    parser.add_argument("--target-chars", type=int, default=2000)
    parser.add_argument("--max-chars", type=int, default=2500)
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not (0 < args.min_chars <= args.target_chars <= args.max_chars):
        print("Require 0 < min <= target <= max.", file=sys.stderr)
        return 2
    if not args.input.is_file():
        print(f"Input not found: {args.input}", file=sys.stderr)
        return 2
    if args.output.exists() and not args.overwrite:
        print(f"Output exists; use --overwrite: {args.output}", file=sys.stderr)
        return 2

    raw_bytes = args.input.read_bytes()
    text = (
        raw_bytes.decode("utf-8")
        .replace("\r\n", "\n")
        .replace("\r", "\n")
        .strip()
    )
    try:
        units = build_units(text, args.max_chars)
        chunks = partition(
            units, args.min_chars, args.target_chars, args.max_chars
        )
    except ValueError as error:
        print(str(error), file=sys.stderr)
        return 2

    payload = {
        "schema_version": 1,
        "language": "en",
        "source_file": str(args.input),
        "source_sha256": hashlib.sha256(raw_bytes).hexdigest(),
        "limits": {
            "min_chars": args.min_chars,
            "target_chars": args.target_chars,
            "max_chars": args.max_chars,
        },
        "chunks": [
            {"chunk_index": index, "char_count": len(chunk), "text": chunk}
            for index, chunk in enumerate(chunks, start=1)
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix(args.output.suffix + ".part")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary.replace(args.output)

    lengths = [len(chunk) for chunk in chunks]
    print(
        f"Wrote {len(chunks)} English chunks to {args.output}; "
        f"min={min(lengths)}, max={max(lengths)}, "
        f"total={sum(lengths)} characters."
    )

    # ── Inline QA ────────────────────────────────────────────────────────────
    qa_issues: list[str] = []
    sentence_enders = {".", "!", "?", '"', "'", ")", "]", "}"}

    for i, chunk in enumerate(chunks, start=1):
        last_char = chunk.rstrip()[-1] if chunk.rstrip() else ""
        if last_char not in sentence_enders:
            qa_issues.append(
                f"  [QA WARN] Chunk {i}: does not end at sentence boundary "
                f"(last char: '{last_char}')"
            )

    # Verify total characters match source (allows for whitespace normalisation)
    source_chars = len(text)
    chunk_chars = sum(lengths)
    if abs(source_chars - chunk_chars) > max(50, int(source_chars * 0.01)):
        qa_issues.append(
            f"  [QA WARN] Character count mismatch: source={source_chars}, "
            f"chunks total={chunk_chars} (diff={abs(source_chars - chunk_chars)})"
        )

    if qa_issues:
        print("\nQA Warnings:")
        for issue in qa_issues:
            print(issue)
    else:
        print("QA: all chunks pass boundary and character-count checks.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
