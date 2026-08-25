#!/usr/bin/env python3
"""Assemble a video where each narration chunk's video segment is trimmed or
freeze-extended to exactly match that chunk's TTS audio duration.

Why this exists: the original pipeline (trim_video.py + concat_audio.py)
treats video trimming and narration generation as two independent steps,
then only checks in qa_check.py that the FINAL video duration is within ~6s
of the master audio duration. That tolerance hides real per-chunk drift —
if one chunk's narration runs long or short, everything after it in the
video silently falls out of sync with what's on screen, and the aggregate
duration check can still pass. This script guarantees sync at the chunk
level instead of only checking it in aggregate at the end.

Requires chunks with source timestamps (start/end in the ORIGINAL source
video), not just chunk_transcript.py's character-count chunks. Use
chunk_transcript_with_timestamps.py to produce these when you need
frame-accurate sync (e.g. replacing narration in an existing talking-head
recording). For pure narration-over-B-roll jobs where no such mapping
exists, the original trim_video.py + concat_audio.py path is still fine.

Per-chunk behavior:
  - TTS audio duration <= original span duration -> trim video (keep the
    first N seconds of the span, drop the tail).
  - TTS audio duration >  original span duration -> freeze the video's last
    frame for the difference (ffmpeg tpad). Works best when the chunk lands
    on static content (slides, screen-share); flag chunks where padding
    exceeds --max-pad-warn for manual review, since freeze-padding over
    moving footage (e.g. talking-head with visible motion) will look wrong.

Usage:
  python3 sync_segments.py chunks_with_timestamps.json audio_parts/ source.mp4 output.mp4 \
      [--preview] [--max-pad-warn 3.0]

chunks_with_timestamps.json format (one entry per chunk, in order):
  [{"chunk_index": 1, "start": 0.24, "end": 2.8, "text": "..."}, ...]

audio_parts/ must contain part_001.<ext>, part_002.<ext>, ... matching
chunk_index, already loudness-normalized (see generate_tts_fish.py /
generate_tts.py normalize_loudness()).
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def probe_duration(path: Path) -> float:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        capture_output=True, text=True, check=True,
    )
    return float(result.stdout.strip())


def find_audio_part(parts_dir: Path, chunk_index: int) -> Path:
    matches = list(parts_dir.glob(f"part_{chunk_index:03d}.*"))
    matches = [m for m in matches if not m.name.endswith(".part")]
    if not matches:
        raise FileNotFoundError(f"No audio part found for chunk_index={chunk_index} in {parts_dir}")
    return matches[0]


def probe_width(path: Path) -> int | None:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True,
    )
    try:
        return int(result.stdout.strip())
    except ValueError:
        return None


def build_video_segment(
    source: Path, src_start: float, src_end: float, target_dur: float,
    preview: bool, out_path: Path, target_width: int,
) -> float:
    """Returns the padding applied (0 if trimmed or exact)."""
    span_dur = src_end - src_start
    preset, crf = ("medium", "22") if preview else ("fast", "20")
    scale = f"scale={target_width}:-2"

    if target_dur <= span_dur + 0.005:
        cmd = [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
            "-ss", f"{src_start:.3f}", "-i", str(source),
            "-t", f"{target_dur:.3f}",
            "-vf", scale, "-an",
            "-c:v", "libx264", "-preset", preset, "-crf", crf,
            "-pix_fmt", "yuv420p", "-r", "24",
            str(out_path),
        ]
        pad = 0.0
    else:
        pad = target_dur - span_dur
        cmd = [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
            "-ss", f"{src_start:.3f}", "-i", str(source),
            "-t", f"{span_dur:.3f}",
            "-vf", f"{scale},tpad=stop_mode=clone:stop_duration={pad:.3f}",
            "-an",
            "-c:v", "libx264", "-preset", preset, "-crf", crf,
            "-pix_fmt", "yuv420p", "-r", "24",
            str(out_path),
        ]
    subprocess.run(cmd, check=True)
    return pad


def mux_segment(video_path: Path, audio_path: Path, out_path: Path) -> None:
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
         "-i", str(video_path), "-i", str(audio_path),
         "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
         "-shortest", str(out_path)],
        check=True,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("chunks", type=Path, help="chunks_with_timestamps.json")
    parser.add_argument("audio_parts_dir", type=Path)
    parser.add_argument("source_video", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--preview", action="store_true", help="Faster/lower-quality encode for review")
    parser.add_argument("--max-pad-warn", type=float, default=3.0,
                        help="Warn if a single chunk needs more than this many seconds of freeze-padding")
    parser.add_argument(
        "--target-width", type=int, default=None,
        help="Force every segment to this width (height auto, aspect preserved). Default: the "
             "source video's own native width -- no forced up/downscale. Only pass this to make "
             "several videos in a series share one fixed output resolution even if their source "
             "recordings differ.",
    )
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        print("ffmpeg and ffprobe must be on PATH.", file=sys.stderr)
        return 2
    if not args.chunks.is_file():
        print(f"Chunks file not found: {args.chunks}", file=sys.stderr)
        return 2
    if not args.source_video.is_file():
        print(f"Source video not found: {args.source_video}", file=sys.stderr)
        return 2
    if args.output.exists() and not args.overwrite:
        print(f"Output exists; use --overwrite: {args.output}", file=sys.stderr)
        return 2

    chunks = json.loads(args.chunks.read_text(encoding="utf-8"))
    if not isinstance(chunks, list) or not chunks:
        print("Chunks file must be a non-empty JSON list.", file=sys.stderr)
        return 2
    for c in chunks:
        for key in ("chunk_index", "start", "end"):
            if key not in c:
                print(f"Chunk missing required field {key!r}: {c}", file=sys.stderr)
                return 2

    target_width = args.target_width or probe_width(args.source_video)
    if target_width is None:
        print(f"Could not determine {args.source_video}'s width -- pass --target-width explicitly.",
              file=sys.stderr)
        return 2
    print(f"Target width: {target_width}px (source: {'--target-width override' if args.target_width else 'native'})")

    warnings: list[str] = []
    trimmed_count = 0
    extended_count = 0

    with tempfile.TemporaryDirectory(prefix="sync_segments_") as tmp:
        tmp_path = Path(tmp)
        segment_paths: list[Path] = []

        for i, c in enumerate(chunks):
            idx = c["chunk_index"]
            audio_part = find_audio_part(args.audio_parts_dir, idx)
            target_dur = probe_duration(audio_part)

            video_seg = tmp_path / f"video_{i:04d}.mp4"
            final_seg = tmp_path / f"seg_{i:04d}.mp4"

            print(f"[{i+1}/{len(chunks)}] chunk {idx}: "
                  f"span={c['end']-c['start']:.2f}s target={target_dur:.2f}s")
            pad = build_video_segment(args.source_video, c["start"], c["end"], target_dur, args.preview, video_seg, target_width)
            mux_segment(video_seg, audio_part, final_seg)
            segment_paths.append(final_seg)
            video_seg.unlink(missing_ok=True)

            if pad > 0:
                extended_count += 1
                if pad > args.max_pad_warn:
                    warnings.append(
                        f"chunk {idx}: freeze-padded {pad:.2f}s (> {args.max_pad_warn}s) -- "
                        f"verify this lands on static content, not motion"
                    )
            else:
                trimmed_count += 1

        concat_list = tmp_path / "concat.txt"
        concat_list.write_text(
            "".join(f"file '{p.resolve().as_posix()}'\n" for p in segment_paths),
            encoding="utf-8",
        )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
             "-f", "concat", "-safe", "0", "-i", str(concat_list),
             "-c", "copy", str(args.output)],
            check=True,
        )

    final_dur = probe_duration(args.output)
    print(f"\nDone: {args.output} ({final_dur:.1f}s)")
    print(f"  {trimmed_count} chunk(s) trimmed, {extended_count} chunk(s) freeze-extended")
    if warnings:
        print(f"\n  [REVIEW NEEDED] {len(warnings)} chunk(s) with large freeze-pads:")
        for w in warnings:
            print(f"    - {w}")
        print("  Use timeline_view-style frame sampling to confirm these land on static content.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
