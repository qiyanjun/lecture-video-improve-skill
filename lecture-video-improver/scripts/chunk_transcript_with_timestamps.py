#!/usr/bin/env python3
"""Produce timestamp-anchored chunks for sync_segments.py.

chunk_transcript.py splits cleaned text purely by character count (1,500-
2,500 chars) with no tie back to the source recording -- fine for narration
laid over B-roll, but useless for sync_segments.py, which needs to know
exactly which span of the ORIGINAL video each chunk of cleaned text
replaces.

This script takes a word-level source transcript (the kind produced by
ElevenLabs Scribe, Fish Audio, or any ASR with per-word start/end times)
and an ordered list of {anchor, clean} entries, where `anchor` is a short
VERBATIM quote (3-6 words) from the ORIGINAL transcript marking where that
chunk begins. It locates each anchor by searching forward sequentially
from the previous match -- critical for transcripts with repeated filler
words ("so", "um") where a naive first-match search would misfire. Each
chunk's end = the next chunk's anchor start (or the transcript's final
word, for the last chunk).

Source transcript format (word-level, matches ElevenLabs Scribe output):
  {"words": [{"type": "word", "text": "...", "start": 0.24, "end": 0.58}, ...]}

Script format (ordered list):
  {"chunks": [{"anchor": "welcome to the course", "clean": "Welcome to the course."}, ...]}

Usage:
  python3 chunk_transcript_with_timestamps.py source_transcript.json script.json output.json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


def norm(text: str) -> str:
    return re.sub(r"[^\w']", "", text.lower())


def find_anchor_from(texts: list[str], anchor_tokens: list[str], from_idx: int) -> int | None:
    n = len(anchor_tokens)
    for i in range(from_idx, len(texts) - n + 1):
        if texts[i:i + n] == anchor_tokens:
            return i
    return None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("source_transcript", type=Path)
    parser.add_argument("script", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.source_transcript.is_file():
        print(f"Source transcript not found: {args.source_transcript}", file=sys.stderr)
        return 2
    if not args.script.is_file():
        print(f"Script not found: {args.script}", file=sys.stderr)
        return 2
    if args.output.exists() and not args.overwrite:
        print(f"Output exists; use --overwrite: {args.output}", file=sys.stderr)
        return 2

    transcript = json.loads(args.source_transcript.read_text(encoding="utf-8"))
    real = [w for w in transcript.get("words", []) if w.get("type") == "word"]
    if not real:
        print("Source transcript has no word-level entries.", file=sys.stderr)
        return 2

    # Some source transcripts (seen across several lectures in this series)
    # contain fused multi-word tokens with an embedded space -- a single
    # "word" entry whose text is e.g. "i mean" or "you know", not two
    # separate entries. An anchor written the natural way ("...it really is
    # i mean in my...") splits into separate "i"/"mean" tokens and silently
    # fails to match a transcript where those are ONE token, derailing every
    # anchor after it in the sequential search. Split on whitespace here so
    # matching operates on individual words regardless of how the source
    # transcript grouped them; token_to_word_idx maps each match-token back
    # to its real word entry for timestamp lookup (a fused token's pieces
    # all point at the same start/end, which is the best available anyway).
    texts: list[str] = []
    token_to_word_idx: list[int] = []
    for i, w in enumerate(real):
        for piece in w.get("text", "").split():
            n = norm(piece)
            if n:
                texts.append(n)
                token_to_word_idx.append(i)

    script = json.loads(args.script.read_text(encoding="utf-8"))
    chunks = script.get("chunks", script if isinstance(script, list) else None)
    if not chunks:
        print("Script must contain a non-empty 'chunks' list (or be one).", file=sys.stderr)
        return 2

    match_word_idx: list[int | None] = []
    search_from = 0
    failed: list[str] = []
    for c in chunks:
        anchor_tokens = [norm(t) for t in c["anchor"].split() if norm(t)]
        idx = find_anchor_from(texts, anchor_tokens, search_from)
        if idx is None:
            failed.append(c["anchor"])
            match_word_idx.append(None)
            continue
        match_word_idx.append(idx)
        search_from = idx + 1

    if failed:
        print(f"{len(failed)} anchor(s) failed to locate -- fix the script before proceeding:", file=sys.stderr)
        for a in failed:
            print(f"  {a!r}", file=sys.stderr)
        return 1

    resolved = []
    for i, c in enumerate(chunks):
        start_idx = token_to_word_idx[match_word_idx[i]]
        start = real[start_idx]["start"]
        if i + 1 < len(chunks):
            end = real[token_to_word_idx[match_word_idx[i + 1]]]["start"]
        else:
            end = real[-1]["end"]
        resolved.append({
            "chunk_index": i + 1,
            "anchor": c["anchor"],
            "text": c["clean"],
            "start": round(start, 3),
            "end": round(end, 3),
        })

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(resolved, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    total_span = resolved[-1]["end"] - resolved[0]["start"] if resolved else 0
    print(f"Resolved {len(resolved)} chunks, span {resolved[0]['start']:.2f}s - {resolved[-1]['end']:.2f}s "
          f"({total_span:.1f}s) -> {args.output}")

    # ── Inline QA: flag any chunk with an implausibly wide or narrow span ────
    for r in resolved:
        dur = r["end"] - r["start"]
        chars = len(r["text"])
        if dur > 0 and chars > 0:
            rate = chars / dur
            if not (2 <= rate <= 40):
                print(f"  [QA WARN] chunk {r['chunk_index']}: {chars} chars over {dur:.2f}s "
                      f"({rate:.1f} chars/s) -- unusually wide/narrow original span for its text length.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
