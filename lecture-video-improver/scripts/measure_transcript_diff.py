#!/usr/bin/env python3
"""Quantify how much a cleaned transcript changed from the original before
sending it to TTS.

Run this BEFORE generating any audio, every time a transcript gets rewritten
for filler removal and/or English improvement -- not just when something
seems off. Word-level diff (difflib.SequenceMatcher) against the original,
reported as a percentage, gives a concrete number to sign off on instead of
"this reads cleaner" intuition. Optionally enforce a cap (e.g. "keep changes
under 40%") and exit non-zero if exceeded, so a batch job doesn't silently
ship an over-aggressive rewrite for one video out of 70.

The lexical diff alone can be misleading: a heavy paraphrase that preserves
meaning and a light edit that flips meaning can show similar word-change %.
--semantic adds an actual meaning check via OpenAI embeddings
(text-embedding-3-small, cosine similarity) -- cheap (a batch of ~150 short
chunks is a fraction of a cent) but a real paid API call, so it's opt-in, not
automatic. The combination that actually matters is high lexical change PLUS
low semantic similarity -- that's the signature of a rewrite that drifted
from what the speaker meant, not just how they said it.

Usage:
  python3 measure_transcript_diff.py original.txt cleaned.txt [--max-change-pct 40]

Or, for chunked JSON from chunk_transcript_with_timestamps.py, pass a source
transcript + the chunks file to measure against the original ASR words
directly (accounts for words dropped as pure filler separately from words
that were reworded):

  python3 measure_transcript_diff.py --source-transcript transcript.json \\
      --chunks chunks_with_timestamps.json [--max-change-pct 40] [--semantic]

Environment variables:
  OPENAI_API_KEY   Required for --semantic.
"""

from __future__ import annotations

import argparse
import difflib
import json
import math
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

OPENAI_EMBEDDINGS_URL = "https://api.openai.com/v1/embeddings"
DEFAULT_EMBEDDING_MODEL = "text-embedding-3-small"
LOW_SIMILARITY_THRESHOLD = 0.75  # below this, flag for review regardless of lexical %


def embed_texts(texts: list[str], api_key: str, model: str, timeout: float) -> list[list[float]]:
    """Batch-embed all texts in one request. Empty strings get a zero vector
    (cosine similarity against anything is 0, correctly flagging an empty
    rewrite as maximally different rather than crashing on it)."""
    non_empty = [(i, t) for i, t in enumerate(texts) if t.strip()]
    if not non_empty:
        return [[0.0]] * len(texts)

    payload = {"model": model, "input": [t for _, t in non_empty]}
    request = urllib.request.Request(
        OPENAI_EMBEDDINGS_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "User-Agent": "lecture-video-improver-plugin/1",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            result = json.loads(response.read())
    except urllib.error.HTTPError as error:
        detail = error.read(2000).decode("utf-8", errors="replace")
        raise RuntimeError(f"OpenAI embeddings HTTP {error.code}: {detail}") from error
    except urllib.error.URLError as error:
        raise RuntimeError(f"OpenAI embeddings request failed: {error.reason}") from error

    vectors = [item["embedding"] for item in result["data"]]
    out: list[list[float]] = [[0.0]] * len(texts)
    for (i, _), vec in zip(non_empty, vectors):
        out[i] = vec
    return out


def cosine_similarity(a: list[float], b: list[float]) -> float:
    if len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


def norm_words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9']+", text.lower())


def diff_stats(original_words: list[str], clean_words: list[str]) -> dict:
    sm = difflib.SequenceMatcher(None, original_words, clean_words)
    matched = sum(block.size for block in sm.get_matching_blocks())
    total = len(original_words)
    pct_survived = 100 * matched / total if total else 0.0
    return {
        "original_words": total,
        "clean_words": len(clean_words),
        "matched_words": matched,
        "pct_survived_verbatim": round(pct_survived, 1),
        "pct_changed": round(100 - pct_survived, 1),
        "pct_length_change": round(100 * (len(clean_words) - total) / total, 1) if total else 0.0,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("original", type=Path, nargs="?", help="Original transcript text file")
    parser.add_argument("cleaned", type=Path, nargs="?", help="Cleaned transcript text file")
    parser.add_argument("--source-transcript", type=Path, help="Word-level ASR JSON (alternative to plain-text original)")
    parser.add_argument("--chunks", type=Path, help="chunks_with_timestamps.json (alternative to plain-text cleaned)")
    parser.add_argument("--max-change-pct", type=float, default=None,
                        help="Exit 1 if pct_changed exceeds this (e.g. 40)")
    parser.add_argument("--per-chunk", action="store_true",
                        help="With --chunks, also report per-chunk change percentage")
    parser.add_argument("-o", "--output", type=Path,
                        help="Persist the stats as JSON to this path (so a caller can verify the "
                             "review actually ran, not just trust that someone eyeballed stdout)")
    parser.add_argument("--semantic", action="store_true",
                        help="Also compute meaning-preservation via OpenAI embeddings (requires "
                             "OPENAI_API_KEY). A real paid call, so opt-in rather than automatic.")
    parser.add_argument("--embedding-model", default=DEFAULT_EMBEDDING_MODEL,
                        help=f"OpenAI embedding model for --semantic (default: {DEFAULT_EMBEDDING_MODEL})")
    parser.add_argument("--min-semantic-similarity", type=float, default=None,
                        help="With --semantic: exit 1 if the overall similarity falls below this "
                             "(0-1). Separate from --max-change-pct -- this catches meaning drift "
                             "that a lexical-only cap can miss.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    if args.semantic and not os.environ.get("OPENAI_API_KEY"):
        print("--semantic requires OPENAI_API_KEY to be set.", file=sys.stderr)
        return 2

    per_chunk_semantic: list[tuple[int, str, str]] = []  # (chunk_index, orig_text, clean_text)
    whole_orig_text = ""
    whole_clean_text = ""

    if args.source_transcript and args.chunks:
        transcript = json.loads(args.source_transcript.read_text(encoding="utf-8"))
        words = [w for w in transcript.get("words", []) if w.get("type") == "word"]
        chunks = json.loads(args.chunks.read_text(encoding="utf-8"))

        original_words: list[str] = []
        clean_words: list[str] = []
        per_chunk_report = []
        for c in chunks:
            span_orig = [w["text"] for w in words if c["start"] <= w["start"] < c["end"]]
            span_orig_text = " ".join(span_orig)
            # Tokenize both sides identically (regex word-extraction) so
            # hyphenated/contracted forms don't silently miscount as changes.
            span_orig_norm = norm_words(span_orig_text)
            span_clean_norm = norm_words(c["text"])
            original_words.extend(span_orig_norm)
            clean_words.extend(span_clean_norm)
            if args.per_chunk or args.semantic:
                chunk_stats = diff_stats(span_orig_norm, span_clean_norm)
                per_chunk_report.append((c.get("chunk_index"), chunk_stats["pct_changed"]))
            if args.semantic:
                per_chunk_semantic.append((c.get("chunk_index"), span_orig_text, c["text"]))

        stats = diff_stats(original_words, clean_words)
        if args.per_chunk:
            print("Per-chunk change %:")
            for idx, pct in per_chunk_report:
                flag = "  <-- high" if pct > (args.max_change_pct or 100) else ""
                print(f"  chunk {idx}: {pct:.1f}%{flag}")
            print()

    elif args.original and args.cleaned:
        whole_orig_text = args.original.read_text(encoding="utf-8")
        whole_clean_text = args.cleaned.read_text(encoding="utf-8")
        original_words = norm_words(whole_orig_text)
        clean_words = norm_words(whole_clean_text)
        stats = diff_stats(original_words, clean_words)
    else:
        print("Provide either (original, cleaned) text files or (--source-transcript, --chunks).",
              file=sys.stderr)
        return 2

    print(f"original words:        {stats['original_words']}")
    print(f"clean words:            {stats['clean_words']} ({stats['pct_length_change']:+.1f}% length change)")
    print(f"survive verbatim:       {stats['pct_survived_verbatim']:.1f}%")
    print(f"changed (cut/reworded): {stats['pct_changed']:.1f}%")

    result = 0
    verdict = None
    if args.max_change_pct is not None:
        if stats["pct_changed"] > args.max_change_pct:
            print(f"\n[FAIL] {stats['pct_changed']:.1f}% exceeds the {args.max_change_pct:.0f}% cap.", file=sys.stderr)
            verdict = "fail"
            result = 1
        else:
            print(f"\n[OK] {stats['pct_changed']:.1f}% is within the {args.max_change_pct:.0f}% cap.")
            verdict = "ok"

    semantic_payload = None
    if args.semantic:
        api_key = os.environ["OPENAI_API_KEY"]
        try:
            if per_chunk_semantic:
                orig_texts = [o for _, o, _ in per_chunk_semantic]
                clean_texts = [c for _, _, c in per_chunk_semantic]
                orig_vecs = embed_texts(orig_texts, api_key, args.embedding_model, timeout=60.0)
                clean_vecs = embed_texts(clean_texts, api_key, args.embedding_model, timeout=60.0)
                per_chunk_sims = [
                    (idx, cosine_similarity(ov, cv))
                    for (idx, _, _), ov, cv in zip(per_chunk_semantic, orig_vecs, clean_vecs)
                ]
                mean_sim = sum(s for _, s in per_chunk_sims) / len(per_chunk_sims)
                lexical_by_idx = dict(per_chunk_report)
                low = [
                    (idx, sim, lexical_by_idx.get(idx, 0.0))
                    for idx, sim in per_chunk_sims if sim < LOW_SIMILARITY_THRESHOLD
                ]
                print(f"\nsemantic similarity:   {mean_sim:.3f} mean (0-1, {args.embedding_model})")
                if low:
                    print(f"  {len(low)} chunk(s) below {LOW_SIMILARITY_THRESHOLD} similarity -- meaning may have drifted, review:")
                    for idx, sim, lex_pct in sorted(low, key=lambda x: x[1]):
                        print(f"    chunk {idx}: similarity={sim:.2f}, lexical_change={lex_pct:.1f}%")
                semantic_payload = {
                    "embedding_model": args.embedding_model,
                    "mean_similarity": round(mean_sim, 3),
                    "per_chunk": [{"chunk_index": idx, "similarity": round(sim, 3)} for idx, sim in per_chunk_sims],
                    "low_similarity_chunks": [idx for idx, _, _ in low],
                }
            else:
                orig_vecs = embed_texts([whole_orig_text], api_key, args.embedding_model, timeout=60.0)
                clean_vecs = embed_texts([whole_clean_text], api_key, args.embedding_model, timeout=60.0)
                sim = cosine_similarity(orig_vecs[0], clean_vecs[0])
                print(f"\nsemantic similarity:   {sim:.3f} (0-1, {args.embedding_model})")
                semantic_payload = {"embedding_model": args.embedding_model, "mean_similarity": round(sim, 3)}

            if args.min_semantic_similarity is not None:
                mean_for_gate = semantic_payload["mean_similarity"]
                if mean_for_gate < args.min_semantic_similarity:
                    print(f"\n[FAIL] semantic similarity {mean_for_gate:.3f} is below the "
                          f"{args.min_semantic_similarity:.2f} floor.", file=sys.stderr)
                    result = 1
                else:
                    print(f"\n[OK] semantic similarity {mean_for_gate:.3f} meets the "
                          f"{args.min_semantic_similarity:.2f} floor.")
        except RuntimeError as error:
            print(f"\n[WARN] semantic similarity skipped: {error}", file=sys.stderr)

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        payload = {**stats, "max_change_pct": args.max_change_pct, "verdict": verdict,
                   "semantic": semantic_payload}
        args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    return result


if __name__ == "__main__":
    raise SystemExit(main())
