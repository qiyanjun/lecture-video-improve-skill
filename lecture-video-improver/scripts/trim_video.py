#!/usr/bin/env python3
"""Trim a video to keep only specified time segments (removing filler/dead air).

Claude analyzes the verbatim transcript and produces a keep-segments JSON file.
This script reads that file and uses FFmpeg's concat demuxer to extract and
stitch the approved segments into a single trimmed output video.

Keep-segments JSON format:
  {
    "source": "raw/video.mp4",
    "segments": [
      {"start": 2.1,  "end": 45.8},
      {"start": 47.3, "end": 120.0},
      ...
    ]
  }

Usage:
  python3 trim_video.py segments.json trimmed_output.mp4 [--overwrite]

Requirements:
  ffmpeg and ffprobe on PATH.
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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "segments_json",
        type=Path,
        help="JSON file with source path and keep-segment list",
    )
    parser.add_argument("output", type=Path, help="Trimmed output video path")
    parser.add_argument(
        "--overwrite", action="store_true", help="Overwrite output if it exists"
    )
    parser.add_argument(
        "--video-codec",
        default="copy",
        help="FFmpeg video codec (default: copy — fast, no re-encode). Use libx264 if sources differ.",
    )
    parser.add_argument(
        "--audio-codec",
        default="aac",
        help="FFmpeg audio codec for output (default: aac)",
    )
    parser.add_argument(
        "--audio-bitrate",
        default="192k",
        help="Audio bitrate (default: 192k)",
    )
    return parser.parse_args()


def probe_duration(path: Path) -> float:
    result = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        check=True, capture_output=True, text=True,
    )
    return float(result.stdout.strip())


def validate_segments(segments: list[dict], source_duration: float) -> None:
    if not segments:
        raise ValueError("segments list is empty.")
    prev_end = -1.0
    for i, seg in enumerate(segments):
        start = seg.get("start")
        end = seg.get("end")
        if not isinstance(start, (int, float)) or not isinstance(end, (int, float)):
            raise ValueError(f"Segment {i}: start/end must be numbers.")
        if start < 0:
            raise ValueError(f"Segment {i}: start ({start}) is negative.")
        if end <= start:
            raise ValueError(f"Segment {i}: end ({end}) must be greater than start ({start}).")
        if start < prev_end:
            raise ValueError(f"Segment {i}: overlaps or is not in order (start={start}, prev_end={prev_end}).")
        if end > source_duration + 0.5:
            raise ValueError(
                f"Segment {i}: end ({end:.3f}s) exceeds source duration ({source_duration:.3f}s)."
            )
        prev_end = end


def build_ffmpeg_trim_command(
    source: Path,
    segment: dict,
    seg_index: int,
    tmp_dir: Path,
    video_codec: str,
    audio_codec: str,
    audio_bitrate: str,
) -> tuple[list[str], Path]:
    """Extract a single segment to a temp file."""
    start = segment["start"]
    end = segment["end"]
    duration = end - start
    out_path = tmp_dir / f"seg_{seg_index:04d}{source.suffix}"
    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
        "-ss", str(start),
        "-i", str(source),
        "-t", str(duration),
        "-c:v", video_codec,
        "-c:a", audio_codec,
        "-b:a", audio_bitrate,
        "-avoid_negative_ts", "make_zero",
        str(out_path),
    ]
    return cmd, out_path


def main() -> int:
    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        print("ffmpeg and ffprobe must be available on PATH.", file=sys.stderr)
        return 2

    args = parse_args()

    if not args.segments_json.is_file():
        print(f"Segments file not found: {args.segments_json}", file=sys.stderr)
        return 2
    if args.output.exists() and not args.overwrite:
        print(f"Output exists; use --overwrite: {args.output}", file=sys.stderr)
        return 2

    try:
        data = json.loads(args.segments_json.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        print(f"Invalid JSON: {error}", file=sys.stderr)
        return 2

    source_path = Path(data.get("source", ""))
    segments = data.get("segments", [])

    if not source_path or not source_path.is_file():
        print(f"Source video not found: {source_path}", file=sys.stderr)
        return 2

    print(f"Source: {source_path}")
    try:
        source_duration = probe_duration(source_path)
    except (subprocess.CalledProcessError, ValueError) as error:
        print(f"Could not probe source duration: {error}", file=sys.stderr)
        return 2
    print(f"Source duration: {source_duration:.3f}s")

    try:
        validate_segments(segments, source_duration)
    except ValueError as error:
        print(str(error), file=sys.stderr)
        return 2

    kept_duration = sum(s["end"] - s["start"] for s in segments)
    removed_duration = source_duration - kept_duration
    print(
        f"Keeping {len(segments)} segment(s): {kept_duration:.1f}s "
        f"(removing {removed_duration:.1f}s / {removed_duration / source_duration * 100:.0f}%)"
    )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    tmp_output = args.output.with_name(args.output.stem + ".part" + args.output.suffix)

    with tempfile.TemporaryDirectory(prefix="trim_segs_") as tmp_dir:
        tmp_path = Path(tmp_dir)
        seg_files: list[Path] = []

        # Extract each segment to a temp file
        for i, seg in enumerate(segments):
            cmd, seg_path = build_ffmpeg_trim_command(
                source_path, seg, i,
                tmp_path,
                args.video_codec, args.audio_codec, args.audio_bitrate,
            )
            print(
                f"  Extracting segment {i + 1}/{len(segments)}: "
                f"{seg['start']:.2f}s – {seg['end']:.2f}s"
            )
            try:
                subprocess.run(cmd, check=True)
            except subprocess.CalledProcessError as error:
                print(f"FFmpeg failed on segment {i}: {error}", file=sys.stderr)
                return 1
            seg_files.append(seg_path)

        # Build concat list
        concat_list = tmp_path / "concat.txt"
        with concat_list.open("w", encoding="utf-8") as f:
            for seg_file in seg_files:
                safe = seg_file.resolve().as_posix().replace("'", "'\\''")
                f.write(f"file '{safe}'\n")

        # Concatenate segments into final output
        print("Concatenating segments...")
        concat_cmd = [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
            "-f", "concat", "-safe", "0",
            "-i", str(concat_list),
            "-c", "copy",
            "-movflags", "+faststart",
            str(tmp_output),
        ]
        try:
            subprocess.run(concat_cmd, check=True)
        except subprocess.CalledProcessError as error:
            print(f"FFmpeg concat failed: {error}", file=sys.stderr)
            return 1

    tmp_output.replace(args.output)

    # Verify output
    try:
        out_duration = probe_duration(args.output)
    except Exception:
        out_duration = 0.0
    tolerance = 0.5 * len(segments)  # allow 0.5s per segment join
    if abs(out_duration - kept_duration) > tolerance:
        print(
            f"Warning: output duration {out_duration:.3f}s differs from "
            f"expected {kept_duration:.3f}s by more than {tolerance:.1f}s.",
            file=sys.stderr,
        )

    print(f"Trimmed video saved: {args.output} ({out_duration:.1f}s)")

    # ── Inline QA: verify streams present ────────────────────────────────────
    qa_issues: list[str] = []
    stream_probe = subprocess.run(
        ["ffprobe", "-v", "error",
         "-show_entries", "stream=codec_type,width,height",
         "-of", "default=noprint_wrappers=1", str(args.output)],
        capture_output=True, text=True,
    )
    stream_output = stream_probe.stdout
    has_video = "codec_type=video" in stream_output
    has_audio = "codec_type=audio" in stream_output
    if not has_video:
        qa_issues.append("  [QA WARN] No video stream found in trimmed output.")
    if not has_audio:
        qa_issues.append("  [QA WARN] No audio stream found in trimmed output.")

    # Check output dimensions match source
    src_probe = subprocess.run(
        ["ffprobe", "-v", "error",
         "-show_entries", "stream=width,height",
         "-of", "default=noprint_wrappers=1", str(source_path)],
        capture_output=True, text=True,
    )
    def extract_dim(text: str, key: str) -> int:
        for line in text.splitlines():
            if line.startswith(key + "="):
                try:
                    return int(line.split("=")[-1])
                except ValueError:
                    pass
        return 0

    src_w = extract_dim(src_probe.stdout, "width")
    src_h = extract_dim(src_probe.stdout, "height")
    out_w = extract_dim(stream_output, "width")
    out_h = extract_dim(stream_output, "height")

    if src_w and out_w and (src_w != out_w or src_h != out_h):
        qa_issues.append(
            f"  [QA WARN] Output dimensions {out_w}x{out_h} differ from source {src_w}x{src_h}."
        )

    if qa_issues:
        print("\nQA Warnings:")
        for issue in qa_issues:
            print(issue)
    else:
        print("QA: video and audio streams verified, dimensions match source.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
