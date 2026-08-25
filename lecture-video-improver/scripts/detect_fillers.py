#!/usr/bin/env python3
"""Scan a Scribe-shaped transcript JSON for filler words and stutter repeats.

Pure pattern matching -- literal word/phrase lookups, no understanding of
meaning. That's the boundary: this script can find CANDIDATES mechanically,
but deciding which discourse fillers are genuinely disposable versus
legitimate usage ("like" as a verbal tic vs. "like" meaning "similar to"),
and rewording the surrounding speech, both require understanding what the
speaker meant -- that stays an agent/human judgment call, same as every
other meaning-dependent step in this plugin (see run_stage1_improve.py's
needs_cleanup pause). Run this first to narrow the search space, not as a
substitute for actually reading the transcript.

Outputs a JSON report of candidate spans, split into:
  - definite: um/uh variants -> always safe to cut
  - discourse: like/you know/i mean/sort of/kind of -> context-dependent, needs review
  - repeats: immediate word repetition (stutter), e.g. "that's that's" -> cut the earlier occurrence(s)

Usage:
    python3 detect_fillers.py <transcript.json>
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

DEFINITE_FILLERS = {"um", "umm", "uh", "uhh"}
DISCOURSE_FILLERS = {"like", "you know", "i mean", "sort of", "kind of"}


def norm(text: str) -> str:
    return re.sub(r"[^\w']", "", text.lower())


def find_definite(words: list[dict]) -> list[dict]:
    out = []
    for w in words:
        if w.get("type") != "word":
            continue
        if norm(w.get("text", "")) in DEFINITE_FILLERS:
            out.append({"start": w["start"], "end": w["end"], "text": w["text"]})
    return out


def find_repeats(words: list[dict]) -> list[dict]:
    out = []
    real = [w for w in words if w.get("type") == "word"]
    i = 0
    while i < len(real) - 1:
        a, b = real[i], real[i + 1]
        na, nb = norm(a.get("text", "")), norm(b.get("text", ""))
        if na and na == nb:
            out.append({
                "start": a["start"], "end": b["end"],
                "text": f'{a["text"]} {b["text"]}',
                "cut_start": a["start"], "cut_end": a["end"],
                "note": "immediate repeat; candidate: cut first occurrence",
            })
            i += 2
            continue
        i += 1
    return out


def find_discourse(words: list[dict]) -> list[dict]:
    real = [w for w in words if w.get("type") == "word"]
    texts = [norm(w.get("text", "")) for w in real]
    out = []
    i = 0
    while i < len(real):
        matched = None
        for phrase in sorted(DISCOURSE_FILLERS, key=lambda p: -len(p.split())):
            plen = len(phrase.split())
            if texts[i:i + plen] == phrase.split():
                matched = (phrase, plen)
                break
        if matched:
            phrase, plen = matched
            span = real[i:i + plen]
            ctx_start = max(0, i - 4)
            ctx_end = min(len(real), i + plen + 4)
            context = " ".join(w["text"] for w in real[ctx_start:ctx_end])
            out.append({
                "start": span[0]["start"], "end": span[-1]["end"],
                "text": phrase, "context": context,
            })
            i += plen
        else:
            i += 1
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("transcript", type=Path)
    ap.add_argument("-o", "--output", type=Path, default=None)
    args = ap.parse_args()

    data = json.loads(args.transcript.read_text())
    words = data.get("words", [])

    report = {
        "definite_fillers": find_definite(words),
        "repeats": find_repeats(words),
        "discourse_fillers": find_discourse(words),
    }

    out_path = args.output or args.transcript.with_name(args.transcript.stem + ".fillers.json")
    out_path.write_text(json.dumps(report, indent=2))

    print(f"definite fillers (um/uh): {len(report['definite_fillers'])}")
    print(f"stutter repeats: {len(report['repeats'])}")
    print(f"discourse fillers (like/you know/i mean/...): {len(report['discourse_fillers'])}")
    print(f"wrote {out_path}")


if __name__ == "__main__":
    main()
