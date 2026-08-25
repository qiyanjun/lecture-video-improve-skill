#!/usr/bin/env python3
"""Concatenate ordered audio parts into a verified master audio file."""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def natural_key(path: Path) -> list[object]:
    return [
        int(part) if part.isdigit() else part.lower()
        for part in re.split(r"(\d+)", path.name)
    ]


def concat_escape(path: Path) -> str:
    return path.resolve().as_posix().replace("'", "'\\''")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("parts_dir", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--pattern", default="part_*.*")
    parser.add_argument("--sample-rate", type=int, default=48000)
    parser.add_argument("--channels", type=int, default=2)
    parser.add_argument("--bitrate", default="192k")
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()


def codec_args(
    output: Path, sample_rate: int, channels: int, bitrate: str
) -> list[str]:
    suffix = output.suffix.lower()
    common = ["-ar", str(sample_rate), "-ac", str(channels)]
    if suffix == ".wav":
        return ["-c:a", "pcm_s16le", *common]
    if suffix in {".m4a", ".mp4"}:
        return ["-c:a", "aac", "-b:a", bitrate, *common]
    if suffix == ".mp3":
        return ["-c:a", "libmp3lame", "-b:a", bitrate, *common]
    raise ValueError("Output extension must be .wav, .m4a, .mp4, or .mp3.")


def main() -> int:
    args = parse_args()
    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        print("ffmpeg and ffprobe must be available on PATH.", file=sys.stderr)
        return 2
    if not args.parts_dir.is_dir():
        print(f"Parts directory not found: {args.parts_dir}", file=sys.stderr)
        return 2
    parts = sorted(
        [
            path
            for path in args.parts_dir.glob(args.pattern)
            if path.is_file()
            and not path.name.endswith(".part")
            and path.stat().st_size > 0
        ],
        key=natural_key,
    )
    if not parts:
        print(
            f"No non-empty parts matched {args.pattern!r} in {args.parts_dir}",
            file=sys.stderr,
        )
        return 2
    suffixes = {path.suffix.lower() for path in parts}
    if len(suffixes) != 1:
        print(
            f"Audio parts use mixed formats: {sorted(suffixes)}", file=sys.stderr
        )
        return 2
    indexes: list[int] = []
    for part in parts:
        match = re.fullmatch(r"part_(\d+)\.[^.]+", part.name)
        if match is None:
            print(f"Unexpected part filename: {part.name}", file=sys.stderr)
            return 2
        indexes.append(int(match.group(1)))
    expected_indexes = list(range(1, len(parts) + 1))
    if indexes != expected_indexes:
        print(
            f"Audio part sequence is incomplete: found {indexes}, "
            f"expected {expected_indexes}",
            file=sys.stderr,
        )
        return 2
    if args.output.exists() and not args.overwrite:
        print(f"Output exists; use --overwrite: {args.output}", file=sys.stderr)
        return 2
    try:
        encoding = codec_args(
            args.output, args.sample_rate, args.channels, args.bitrate
        )
    except ValueError as error:
        print(str(error), file=sys.stderr)
        return 2

    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary_output = args.output.with_name(
        f"{args.output.stem}.part{args.output.suffix}"
    )
    if temporary_output.exists():
        print(
            f"Temporary output exists; inspect or remove it first: {temporary_output}",
            file=sys.stderr,
        )
        return 2

    list_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            suffix=".ffconcat",
            prefix="audio_parts_",
            dir=args.output.parent,
            delete=False,
        ) as handle:
            list_path = Path(handle.name)
            for part in parts:
                handle.write(f"file '{concat_escape(part)}'\n")

        command = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-nostdin",
            "-n",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(list_path),
            "-vn",
            *encoding,
            str(temporary_output),
        ]
        subprocess.run(command, check=True)
        temporary_output.replace(args.output)
    except subprocess.CalledProcessError as error:
        print(f"ffmpeg failed with exit code {error.returncode}.", file=sys.stderr)
        return error.returncode or 1
    finally:
        if list_path is not None:
            list_path.unlink(missing_ok=True)

    probe = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(args.output),
        ],
        check=True,
        text=True,
        capture_output=True,
    )
    duration = float(probe.stdout.strip())
    if duration <= 0:
        print("Master audio has a non-positive duration.", file=sys.stderr)
        return 1
    print(
        f"Wrote {args.output} from {len(parts)} parts; duration={duration:.3f}s"
    )

    # ── Inline QA: verify master duration vs sum of parts ────────────────────
    parts_total = 0.0
    qa_issues: list[str] = []
    for part in parts:
        part_probe = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(part)],
            check=True, text=True, capture_output=True,
        )
        try:
            parts_total += float(part_probe.stdout.strip())
        except ValueError:
            qa_issues.append(f"  [QA WARN] Could not probe duration of {part.name}")

    if parts_total > 0:
        diff = abs(duration - parts_total)
        if diff > max(1.0, len(parts) * 0.1):
            qa_issues.append(
                f"  [QA WARN] Master duration ({duration:.3f}s) differs from "
                f"parts sum ({parts_total:.3f}s) by {diff:.3f}s"
            )
        else:
            print(f"QA: master duration matches parts sum within {diff:.3f}s.")

    # Verify sample rate and channel count from master
    detail_probe = subprocess.run(
        ["ffprobe", "-v", "error",
         "-show_entries", "stream=sample_rate,channels",
         "-of", "default=noprint_wrappers=1", str(args.output)],
        capture_output=True, text=True,
    )
    for line in detail_probe.stdout.splitlines():
        if "sample_rate" in line:
            sr = int(line.split("=")[-1].strip())
            if sr not in {44100, 48000}:
                qa_issues.append(f"  [QA WARN] Unusual sample rate: {sr} Hz")
        if "channels" in line:
            ch = int(line.split("=")[-1].strip())
            if ch < 1:
                qa_issues.append(f"  [QA WARN] Invalid channel count: {ch}")

    if qa_issues:
        print("\nQA Warnings:")
        for issue in qa_issues:
            print(issue)
    else:
        print("QA: all checks passed.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
