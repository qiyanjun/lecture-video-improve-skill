#!/usr/bin/env python3
"""Resolve a video and/or transcript input that may live on YouTube instead
of as a local file.

Two independent capabilities, since the video and the transcript can come
from different places (e.g. a local video file whose recording was also
separately uploaded to YouTube, where you want the video from disk but the
transcript pulled from YouTube's free auto-captions):

  --download-video <url>       Download the video itself via yt-dlp.
  --download-captions <url>    Extract YouTube's caption track and convert
                                it into the same word-level JSON schema the
                                rest of this pipeline expects (matches
                                ElevenLabs Scribe's output shape: a flat
                                "words" list with type/text/start/end).

Why captions need real scrutiny before trusting them for filler removal:
  - YouTube auto-generated captions are usually verbatim (fillers like "um"/
    "uh" survive) since nothing intentionally cleans them up.
  - MANUALLY uploaded / creator-edited captions are very often cleaned up by
    a human for readability -- exactly the fillers you're trying to find and
    remove will frequently already be gone. This script lists which caption
    tracks exist and flags if only non-auto tracks are available, so you
    don't silently treat a pre-cleaned transcript as ground truth.
  - Caption timing is per-word START only (no explicit end); this script
    infers end = next word's start, clamped to a max plausible word
    duration, which is what group_into_phrases()-style downstream tooling
    needs for phrase segmentation (see chunk_transcript_with_timestamps.py).

Usage:
  # See what's available without downloading anything
  python3 resolve_youtube_source.py --list-captions "https://youtube.com/watch?v=..."

  # Download the video
  python3 resolve_youtube_source.py --download-video "https://youtube.com/watch?v=..." \\
      -o source.mp4 --execute

  # Extract + adapt captions, with a sanity check against a local video's duration
  python3 resolve_youtube_source.py --download-captions "https://youtube.com/watch?v=..." \\
      -o transcript.json --compare-against source.mp4 --execute

Requires yt-dlp and ffprobe on PATH.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

MAX_WORD_MS = 700
DEFAULT_LAST_WORD_MS = 400


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--list-captions", metavar="URL", help="List available caption tracks (auto vs manual) and exit")
    parser.add_argument("--download-video", metavar="URL", help="Download the video from this YouTube URL")
    parser.add_argument("--download-captions", metavar="URL", help="Extract+convert captions from this YouTube URL")
    parser.add_argument("-o", "--output", type=Path, help="Output path (required with --download-video/--download-captions)")
    parser.add_argument("--lang", default="en-orig", help="Caption language/track (default: en-orig, the original English auto track)")
    parser.add_argument("--compare-against", type=Path, help="Local video file to sanity-check caption-track duration against")
    parser.add_argument("--execute", action="store_true", help="Actually download (dry run by default -- shows the plan only)")
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()


def require_ytdlp() -> None:
    if shutil.which("yt-dlp") is None:
        sys.exit("yt-dlp not found on PATH. Install with: brew install yt-dlp")


def list_captions(url: str) -> None:
    require_ytdlp()
    subprocess.run(["yt-dlp", "--list-subs", "--no-playlist", url], check=True)
    print(
        "\nNote: entries under 'Available subtitles' are manually uploaded/creator-edited -- "
        "these are commonly cleaned up and may already have fillers removed. Entries under "
        "'Available automatic captions' are YouTube's own ASR and are much more likely to be "
        "verbatim. Prefer an automatic track (e.g. 'en-orig') for filler-removal work, and "
        "verify with a spot check regardless."
    )


def download_video(url: str, output: Path, execute: bool) -> None:
    if not execute:
        print(f"Plan: download video from {url} -> {output}")
        print("Dry run only. Add --execute after approval.")
        return
    require_ytdlp()
    output.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["yt-dlp", "--no-playlist", "-f", "bv*+ba/b", "--merge-output-format", "mp4",
           "-o", str(output), url]
    try:
        subprocess.run(cmd, check=True)
    except subprocess.CalledProcessError as error:
        print(
            "Video download failed (see yt-dlp output above). Unlike caption extraction, actual "
            "video-stream downloads need YouTube's current anti-bot workarounds and are much more "
            "sensitive to yt-dlp being out of date -- YouTube changes its extraction requirements "
            "often. In order, try:\n"
            "  1. Update yt-dlp: `brew upgrade yt-dlp` (or `pip install -U yt-dlp`). This alone "
            "fixes most HTTP 403 errors on this step -- confirmed in practice, a ~6-week-old "
            "yt-dlp version was the actual cause of a 403 that looked credential-related.\n"
            "  2. Check impersonation support is available: `yt-dlp --list-impersonate-targets`. "
            "If everything shows '(unavailable)', install a compatible curl_cffi: yt-dlp pins a "
            "specific supported range (check the error from "
            "`python3 -c 'import yt_dlp.networking._curlcffi'` under yt-dlp's own Python "
            "interpreter -- for a Homebrew install that's "
            "$(brew --prefix)/Cellar/yt-dlp/*/libexec/bin/python). A too-new curl_cffi (e.g. "
            "latest via plain `pip install curl_cffi`) can be silently incompatible.\n"
            "  3. Only if the video genuinely requires being logged in (private/unlisted/age-gated "
            "-- not the case for an ordinary public video): `yt-dlp --cookies-from-browser "
            "<browser>` reuses the user's own logged-in session. Ask before using this -- it "
            "shares real browser session data with yt-dlp, which is a different, more "
            "sensitive kind of access than the anonymous download this script does by default.",
            file=sys.stderr,
        )
        sys.exit(1)
    if not output.is_file():
        sys.exit(f"Download reported success but {output} was not created -- check yt-dlp output above.")
    print(f"Downloaded: {output}")


def norm(text: str) -> str:
    return re.sub(r"[^\w']", "", text.lower())


def flatten_words(events: list[dict]) -> list[dict]:
    tokens: list[dict] = []
    for e in events:
        base = e.get("tStartMs")
        if base is None:
            continue
        for seg in e.get("segs", []):
            text = (seg.get("utf8") or "").strip()
            if not text:
                continue
            off = seg.get("tOffsetMs", 0)
            tokens.append({"start_ms": base + off, "text": text})
    tokens.sort(key=lambda t: t["start_ms"])
    return tokens


def build_words(tokens: list[dict], track_end_ms: int) -> list[dict]:
    words: list[dict] = []
    for i, tok in enumerate(tokens):
        start_ms = tok["start_ms"]
        if i + 1 < len(tokens):
            next_start = tokens[i + 1]["start_ms"]
            end_ms = min(next_start, start_ms + MAX_WORD_MS)
            end_ms = max(end_ms, start_ms + 40)
        else:
            end_ms = max(start_ms + DEFAULT_LAST_WORD_MS, min(track_end_ms, start_ms + MAX_WORD_MS))
        words.append({
            "text": tok["text"], "type": "word",
            "start": round(start_ms / 1000, 3), "end": round(end_ms / 1000, 3),
            "speaker_id": "0",
        })
    return words


def probe_duration(path: Path) -> float:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        capture_output=True, text=True, check=True,
    )
    return float(result.stdout.strip())


SEVERE_MISMATCH_PCT = 15.0  # beyond this, treat it as a hard failure, not just a warning


def download_captions(url: str, lang: str, output: Path, execute: bool, compare_against: Path | None) -> bool:
    """Returns False on a severe duration mismatch (caller should exit non-zero); True otherwise."""
    if not execute:
        print(f"Plan: extract '{lang}' caption track from {url}, convert to word-level JSON -> {output}")
        if compare_against:
            print(f"      then sanity-check total duration against {compare_against}")
        print("Dry run only. Add --execute after approval.")
        return True
    require_ytdlp()
    output.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="ytcaptions_") as tmp:
        tmp_path = Path(tmp)
        cmd = ["yt-dlp", "--no-playlist", "--skip-download", "--write-auto-sub",
               "--sub-lang", lang, "--sub-format", "json3",
               "-o", str(tmp_path / "caption.%(ext)s"), url]
        subprocess.run(cmd, check=True)
        json3_files = list(tmp_path.glob(f"caption.{lang}.json3"))
        if not json3_files:
            sys.exit(
                f"No '{lang}' auto-caption track downloaded. Run --list-captions to see what's "
                f"actually available for this video and pick a real language/track code."
            )
        data = json.loads(json3_files[0].read_text())

    events = data.get("events", [])
    track_end_ms = 0
    for e in events:
        t, d = e.get("tStartMs"), e.get("dDurationMs")
        if t is not None and d is not None:
            track_end_ms = max(track_end_ms, t + d)

    tokens = flatten_words(events)
    if not tokens:
        sys.exit("Caption track had no usable word tokens -- likely empty or a non-speech-only track.")
    words = build_words(tokens, track_end_ms)
    non_speech = [w for w in words if w["text"].strip().startswith("[")]

    payload = {
        "language_code": lang.split("-")[0],
        "text": " ".join(w["text"] for w in words),
        "words": words,
        "source": "youtube_auto_caption",
        "source_url": url,
        "source_lang": lang,
    }
    output.write_text(json.dumps(payload, indent=2))

    track_duration_s = track_end_ms / 1000
    print(f"Wrote {output} ({len(words)} word tokens, {len(non_speech)} non-speech tags filtered downstream)")
    print(f"Caption track duration: {track_duration_s:.1f}s")

    if compare_against:
        if not compare_against.is_file():
            print(f"  [WARN] Cannot sanity-check duration -- {compare_against} not found.", file=sys.stderr)
            return True
        local_dur = probe_duration(compare_against)
        diff = abs(local_dur - track_duration_s)
        pct = 100 * diff / max(local_dur, 1)
        if pct > SEVERE_MISMATCH_PCT:
            print(
                f"  [FAIL] Duration mismatch: local video {local_dur:.1f}s vs caption track "
                f"{track_duration_s:.1f}s ({diff:.1f}s / {pct:.1f}% difference, exceeds the "
                f"{SEVERE_MISMATCH_PCT:.0f}% severe-mismatch threshold). This is very unlikely to be "
                f"the same recording -- {output} was still written for inspection, but do not use it "
                f"as the transcript for {compare_against} without checking by hand.",
                file=sys.stderr,
            )
            return False
        if pct > 2:
            print(
                f"  [WARN] Duration mismatch: local video {local_dur:.1f}s vs caption track "
                f"{track_duration_s:.1f}s ({diff:.1f}s / {pct:.1f}% difference). Small mismatches can "
                f"be normal (intro/outro trimmed differently between uploads) -- spot-check before "
                f"trusting this transcript for {compare_against}.",
                file=sys.stderr,
            )
        else:
            print(f"  Duration check OK: local video {local_dur:.1f}s vs caption track "
                  f"{track_duration_s:.1f}s ({diff:.1f}s difference) -- looks like the same recording.")
    return True


def main() -> int:
    args = parse_args()

    actions = [a for a in (args.list_captions, args.download_video, args.download_captions) if a]
    if len(actions) != 1:
        print("Pass exactly one of --list-captions, --download-video, --download-captions.", file=sys.stderr)
        return 2

    if args.list_captions:
        list_captions(args.list_captions)
        return 0

    if not args.output:
        print("--output is required with --download-video/--download-captions.", file=sys.stderr)
        return 2
    if args.output.exists() and not args.overwrite and args.execute:
        print(f"Output exists; use --overwrite: {args.output}", file=sys.stderr)
        return 2

    if args.download_video:
        download_video(args.download_video, args.output, args.execute)
    elif args.download_captions:
        ok = download_captions(args.download_captions, args.lang, args.output, args.execute, args.compare_against)
        if not ok:
            return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
