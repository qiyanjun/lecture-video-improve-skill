#!/usr/bin/env python3
"""Prepend a short, spoken welcome/branding message to an already-assembled
final video -- e.g. "Welcome to the course. This is Lecture 3." -- using
the SAME TTS voice as the rest of the narration.

Optional, standalone step. Not part of run_stage1_improve.py's core 0-5
step flow -- it runs, if requested, as an extra pass over an already-built
final_output, after prepend_intro_card(). See run_stage1_improve.py's
"welcome_message" manifest field to enable it for a whole batch.

Why this needs its own script rather than a naive `ffmpeg -f concat`:

  1. NON-MONOTONIC DTS from stream-copy concat. Concatenating a fresh clip
     in front of an existing final video via the concat DEMUXER with
     `-c copy` is fast, but real Stage-1 final videos are themselves built
     from many individually trimmed/freeze-extended chunk segments (see
     sync_segments.py) -- their internal audio-stream timestamps are not
     guaranteed clean. Verified directly on a real 71-video batch: stream-
     copy concat produced a *64x too long* reported duration (44730s for a
     572s video) with a silent "Non-monotonic DTS" warning buried in
     ffmpeg's stderr, not a hard failure -- easy to ship without noticing.
     The fix is the concat FILTER instead (decodes and re-times both
     inputs), which is what prepend_intro_card() already does above in
     this same file, for the same underlying reason. This script follows
     that same established pattern rather than reintroducing the bug.

  2. REDUNDANT SILENCE if there's already an intro card. prepend_intro_card()
     always holds its card for a fixed 3s of SILENCE before the real
     content starts (see INTRO_CARD_HOLD_SECONDS below). A welcome message
     spoken over a duplicate of the video's own first frame, prepended in
     front of that, leaves the original 3s silent hold sitting right after
     the spoken welcome ends -- dead air the viewer notices immediately.
     Confirmed directly with ffmpeg's silencedetect filter on a real batch.
     This script trims that many seconds back out of the target video by
     default (--trim-seconds, matching INTRO_CARD_HOLD_SECONDS) so the cut
     lands exactly where the real content begins. Pass --trim-seconds 0 if
     the target video has no intro-card hold to remove.

Usage:
  # Dry run (no API charges) -- just prints the plan
  python3 prepend_welcome.py final/lecture03_final.mp4 \\
      --text "Welcome to the course. This is Lecture 3." \\
      --voice-id abc123

  # Execute: generate the clip, prepend it, trim the redundant silent hold
  python3 prepend_welcome.py final/lecture03_final.mp4 \\
      --text "Welcome to the course..." --voice-id abc123 --execute
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).parent
sys.path.insert(0, str(SCRIPTS_DIR))

from generate_tts_fish import request_audio, normalize_loudness  # noqa: E402

# Must match prepend_intro_card()'s hardcoded `-t "3"` intro-card hold in
# run_stage1_improve.py. If that constant ever changes, this one should too.
INTRO_CARD_HOLD_SECONDS = 3.0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("video", type=Path, help="Final video to prepend the welcome message to (modified in place unless -o is given)")
    parser.add_argument("--text", required=True, help="Welcome message text (already resolved -- no template substitution here)")
    parser.add_argument("--voice-id", help="Fish Audio reference_id (voice model ID). Should match the rest of the video's narration voice.")
    parser.add_argument("-o", "--output", type=Path, help="Output path (default: overwrite the input video in place)")
    parser.add_argument("--trim-seconds", type=float, default=INTRO_CARD_HOLD_SECONDS,
                         help=f"Seconds of silence to remove from the target video right after the welcome clip ends, to cancel out prepend_intro_card()'s silent hold (default: {INTRO_CARD_HOLD_SECONDS}, matching that hold's length). Pass 0 if the target has no intro-card hold to remove.")
    parser.add_argument("--target-lufs", type=float, default=-14.0, help="Loudness-normalization target for the welcome clip, matching the rest of the narration (default: -14 LUFS)")
    parser.add_argument("--execute", action="store_true", help="Actually call the TTS API and produce output (dry run by default)")
    return parser.parse_args()


def probe(video: Path, entries: str, stream: str | None = None) -> str:
    cmd = ["ffprobe", "-v", "error"]
    if stream:
        cmd += ["-select_streams", stream]
    cmd += ["-show_entries", entries, "-of", "csv=p=0", str(video)]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffprobe failed on {video}: {result.stderr.strip()}")
    return result.stdout.strip()


def run(cmd: list[str]) -> None:
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"Command failed: {' '.join(cmd)}\n{result.stderr[-2000:]}")


def main() -> int:
    args = parse_args()

    if not args.video.is_file():
        print(f"Video not found: {args.video}", file=sys.stderr)
        return 2

    duration_str = probe(args.video, "format=duration")
    video_duration = float(duration_str)
    res = probe(args.video, "stream=width,height", "v:0")
    width, height = (int(x) for x in res.split(","))
    fps = probe(args.video, "stream=r_frame_rate", "v:0")
    sample_rate = probe(args.video, "stream=sample_rate", "a:0")
    channels = probe(args.video, "stream=channels", "a:0")

    print(f"Plan: prepend a welcome clip ({len(args.text)} characters) to {args.video.name}")
    print(f"  Target video: {width}x{height} @ {fps}fps, audio {sample_rate}Hz/{channels}ch, {video_duration:.1f}s")
    print(f"  Text: {args.text!r}")
    print(f"  Will trim {args.trim_seconds}s of silence from the target after the welcome clip ends"
          f" (set --trim-seconds 0 to skip this).")

    if not args.execute:
        print("\nDry run only: no API request sent, no files changed. Add --execute after approval.")
        return 0

    import os
    api_key = os.getenv("FISH_API_KEY")
    if not api_key:
        print("FISH_API_KEY is not set. Get an API key at fish.audio/app/api-keys/", file=sys.stderr)
        return 2

    work_dir = args.video.parent / f".{args.video.stem}_welcome_tmp"
    work_dir.mkdir(exist_ok=True)
    raw_audio = work_dir / "welcome_raw.mp3"
    norm_audio = work_dir / "welcome_normalized.mp3"
    frame_png = work_dir / "first_frame.png"
    segment_mp4 = work_dir / "welcome_segment.mp4"
    output = args.output or args.video
    tmp_output = work_dir / "output.mp4"

    try:
        print("Generating welcome clip via Fish Audio...")
        audio = request_audio(
            api_key=api_key, voice_id=args.voice_id, output_format="mp3",
            mp3_bitrate=128, latency="normal", speed=1.0, text=args.text, timeout=60.0,
        )
        raw_audio.write_bytes(audio)
        normalize_loudness(raw_audio, target_lufs=args.target_lufs)
        raw_audio.replace(norm_audio)

        welcome_dur = float(probe(norm_audio, "format=duration"))
        print(f"  Welcome clip: {welcome_dur:.2f}s")

        run(["ffmpeg", "-y", "-v", "error", "-i", str(args.video), "-vframes", "1", str(frame_png)])

        run([
            "ffmpeg", "-y", "-v", "error",
            "-loop", "1", "-i", str(frame_png), "-i", str(norm_audio),
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", fps.split("/")[0] if "/" not in fps else str(eval(fps)),
            "-t", str(welcome_dur),
            "-c:a", "aac", "-ar", sample_rate, "-ac", channels, "-b:a", "192k",
            "-vf", f"scale={width}:{height}",
            "-shortest", str(segment_mp4),
        ])

        # The target video's own redundant silent hold (if any -- e.g.
        # prepend_intro_card()'s fixed 3s card hold) sits at the START of
        # ITS OWN timeline, [0, trim_seconds) -- NOT offset by welcome_dur.
        # The welcome clip is prepended separately (it's simply input 0,
        # concatenated first); the two trims are independent, not additive.
        if args.trim_seconds > 0:
            filter_complex = (
                f"[0:v]trim=0:{welcome_dur},setpts=PTS-STARTPTS[v0];"
                f"[0:a]atrim=0:{welcome_dur},asetpts=PTS-STARTPTS[a0];"
                f"[1:v]trim=start={args.trim_seconds},setpts=PTS-STARTPTS[v2];"
                f"[1:a]atrim=start={args.trim_seconds},asetpts=PTS-STARTPTS[a2];"
                f"[v0][a0][v2][a2]concat=n=2:v=1:a=1[outv][outa]"
            )
        else:
            filter_complex = (
                "[0:v][0:a][1:v][1:a]concat=n=2:v=1:a=1[outv][outa]"
            )
        run([
            "ffmpeg", "-y", "-v", "error",
            "-i", str(segment_mp4), "-i", str(args.video),
            "-filter_complex", filter_complex,
            "-map", "[outv]", "-map", "[outa]",
            "-c:v", "libx264", "-crf", "19", "-preset", "fast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "192k",
            str(tmp_output),
        ])

        decode = subprocess.run(["ffmpeg", "-v", "error", "-i", str(tmp_output), "-f", "null", "-"],
                                 capture_output=True, text=True)
        decode_errors = [l for l in decode.stderr.splitlines() if l.strip()]
        if decode_errors:
            print(f"Decode check found {len(decode_errors)} error(s) in the assembled output:", file=sys.stderr)
            for line in decode_errors[:10]:
                print(f"  {line}", file=sys.stderr)
            return 1

        tmp_output.replace(output)
        new_dur = float(probe(output, "format=duration"))
        expected = video_duration + welcome_dur - (args.trim_seconds if args.trim_seconds > 0 else 0)
        print(f"Wrote: {output} ({new_dur:.1f}s, expected ~{expected:.1f}s)")
        return 0
    finally:
        for f in (raw_audio, frame_png, segment_mp4, tmp_output):
            f.unlink(missing_ok=True)
        try:
            work_dir.rmdir()
        except OSError:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
