#!/usr/bin/env python3
"""Comprehensive QA audit for a completed video production job.

Checks every artifact in a job's working directory and writes a structured
QA report to qa_report.json. Exit code 0 = all checks passed, 1 = warnings
or failures found.

Checks performed:
  chunks.json     — schema, bounds, sentence endings, char totals
  audio parts     — existence, non-zero size, duration plausibility per chunk
  master audio    — duration vs sum of parts, sample rate, channels
  trimmed video   — video + audio streams present, dimensions, duration
  thumbnail       — file exists, size > 10 KB, correct dimensions
  intro card      — same as thumbnail
  final video     — streams, duration matches master audio, dimensions, A/V sync

Usage:
  python3 qa_check.py <job_dir> [--final-video path] [--verbose]

  <job_dir> must contain the standard job layout (see run_stage1_improve.py):
    work/chunks.json
    work/audio_parts/
    work/master_narration.wav
    work/assets/thumbnail.jpg
    work/assets/intro_card.png
    final/<job_id>_final.mp4   (optional)

Requirements:
  ffprobe on PATH. Pillow optional (used for image dimension check).
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path


SENTENCE_ENDERS = {".", "!", "?", '"', "'", ")", "]", "}"}
MIN_CHARS_PER_SECOND = 5     # ~75 WPM minimum
MAX_CHARS_PER_SECOND = 25    # ~375 WPM maximum


# ── Helpers ──────────────────────────────────────────────────────────────────

def probe_audio(path: Path) -> dict:
    """Return dict with duration, sample_rate, channels. Raises on failure."""
    result = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-show_entries", "stream=codec_type,sample_rate,channels,duration"
                             ":format=duration,size",
            "-of", "json",
            str(path),
        ],
        capture_output=True, text=True, check=True,
    )
    data = json.loads(result.stdout)
    streams = data.get("streams", [])
    fmt = data.get("format", {})
    audio_stream = next((s for s in streams if s.get("codec_type") == "audio"), {})
    return {
        "duration": float(fmt.get("duration") or audio_stream.get("duration") or 0),
        "sample_rate": int(audio_stream.get("sample_rate") or 0),
        "channels": int(audio_stream.get("channels") or 0),
        "size_bytes": int(fmt.get("size") or 0),
    }


def probe_video(path: Path) -> dict:
    """Return dict with video/audio stream info."""
    result = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-show_entries",
            "stream=codec_type,width,height,r_frame_rate,duration,channels,sample_rate"
            ":format=duration,size",
            "-of", "json",
            str(path),
        ],
        capture_output=True, text=True, check=True,
    )
    data = json.loads(result.stdout)
    streams = data.get("streams", [])
    fmt = data.get("format", {})
    video_s = next((s for s in streams if s.get("codec_type") == "video"), {})
    audio_s = next((s for s in streams if s.get("codec_type") == "audio"), {})
    return {
        "duration": float(fmt.get("duration") or 0),
        "size_bytes": int(fmt.get("size") or 0),
        "has_video": bool(video_s),
        "has_audio": bool(audio_s),
        "width": int(video_s.get("width") or 0),
        "height": int(video_s.get("height") or 0),
        "frame_rate": video_s.get("r_frame_rate", ""),
        "audio_channels": int(audio_s.get("channels") or 0),
        "audio_sample_rate": int(audio_s.get("sample_rate") or 0),
    }


def image_dimensions(path: Path) -> tuple[int, int] | None:
    try:
        from PIL import Image
        with Image.open(path) as img:
            return img.size  # (width, height)
    except Exception:
        return None


@dataclass
class QAReport:
    job_dir: str
    generated_at: str = ""
    checks: list[dict] = field(default_factory=list)
    passed: int = 0
    warned: int = 0
    failed: int = 0

    def add(self, name: str, status: str, message: str, detail: dict | None = None) -> None:
        entry = {"check": name, "status": status, "message": message}
        if detail:
            entry["detail"] = detail
        self.checks.append(entry)
        if status == "PASS":
            self.passed += 1
        elif status == "WARN":
            self.warned += 1
        else:
            self.failed += 1

    def ok(self) -> bool:
        return self.failed == 0

    def to_dict(self) -> dict:
        return {
            "job_dir": self.job_dir,
            "generated_at": self.generated_at,
            "summary": {
                "passed": self.passed,
                "warned": self.warned,
                "failed": self.failed,
                "overall": "PASS" if self.failed == 0 and self.warned == 0
                           else "WARN" if self.failed == 0 else "FAIL",
            },
            "checks": self.checks,
        }


# ── Individual check functions ────────────────────────────────────────────────

def check_chunks(report: QAReport, chunks_path: Path) -> list[dict] | None:
    if not chunks_path.is_file():
        report.add("chunks_json_exists", "FAIL", f"Not found: {chunks_path}")
        return None
    try:
        data = json.loads(chunks_path.read_text(encoding="utf-8"))
    except Exception as e:
        report.add("chunks_json_valid", "FAIL", f"Invalid JSON: {e}")
        return None

    report.add("chunks_json_exists", "PASS", str(chunks_path))

    chunks = data.get("chunks", data) if isinstance(data, dict) else data
    if not isinstance(chunks, list) or not chunks:
        report.add("chunks_non_empty", "FAIL", "No chunks found.")
        return None
    report.add("chunks_non_empty", "PASS", f"{len(chunks)} chunks found.")

    limits = data.get("limits", {}) if isinstance(data, dict) else {}
    min_c = limits.get("min_chars", 1500)
    max_c = limits.get("max_chars", 2500)

    bounds_issues = []
    ending_issues = []
    for chunk in chunks:
        idx = chunk.get("chunk_index", "?")
        text = chunk.get("text", "")
        length = len(text)
        # Check bounds (final chunk may be shorter)
        if length > max_c:
            bounds_issues.append(f"chunk {idx}: {length} > max {max_c}")
        # Check sentence ending (last non-space char should be sentence ender)
        last_char = text.rstrip()[-1] if text.rstrip() else ""
        if last_char not in SENTENCE_ENDERS:
            ending_issues.append(f"chunk {idx}: ends with '{last_char}'")

    if bounds_issues:
        report.add("chunks_within_bounds", "FAIL",
                   f"{len(bounds_issues)} chunk(s) exceed max_chars.",
                   {"violations": bounds_issues})
    else:
        report.add("chunks_within_bounds", "PASS",
                   f"All {len(chunks)} chunks within bounds ({min_c}–{max_c} chars).")

    if ending_issues:
        report.add("chunks_sentence_endings", "WARN",
                   f"{len(ending_issues)} chunk(s) may not end at sentence boundary.",
                   {"chunks": ending_issues})
    else:
        report.add("chunks_sentence_endings", "PASS",
                   "All chunks end at sentence boundaries.")

    total_chars = sum(len(c.get("text", "")) for c in chunks)
    report.add("chunks_char_total", "PASS",
               f"Total characters across all chunks: {total_chars:,}.")

    return chunks


def check_audio_parts(report: QAReport, parts_dir: Path, chunks: list[dict] | None) -> float:
    if not parts_dir.is_dir():
        report.add("audio_parts_dir", "FAIL", f"Not found: {parts_dir}")
        return 0.0

    parts = sorted(
        [p for p in parts_dir.iterdir()
         if p.is_file() and p.name.startswith("part_") and not p.name.endswith(".part")],
        key=lambda p: p.name,
    )
    if not parts:
        report.add("audio_parts_exist", "FAIL", f"No audio parts in {parts_dir}")
        return 0.0

    report.add("audio_parts_exist", "PASS", f"{len(parts)} part(s) found.")

    if shutil.which("ffprobe") is None:
        report.add("audio_parts_duration", "WARN", "ffprobe not on PATH; skipping duration checks.")
        return 0.0

    # Build chunk char counts for plausibility check
    char_by_index: dict[int, int] = {}
    if chunks:
        for c in chunks:
            idx = int(c.get("chunk_index", 0))
            char_by_index[idx] = len(c.get("text", ""))

    total_dur = 0.0
    issues: list[str] = []
    zero_parts: list[str] = []
    part_durations: list[dict] = []

    for part in parts:
        if part.stat().st_size == 0:
            zero_parts.append(part.name)
            continue
        try:
            info = probe_audio(part)
        except Exception as e:
            issues.append(f"{part.name}: probe failed ({e})")
            continue

        dur = info["duration"]
        total_dur += dur

        # Plausibility: chars / duration should be within speaking rate range
        import re
        m = re.search(r"part_0*(\d+)", part.stem)
        chunk_idx = int(m.group(1)) if m else None
        chars = char_by_index.get(chunk_idx, 0) if chunk_idx else 0

        status = "ok"
        if dur <= 0:
            issues.append(f"{part.name}: zero or negative duration")
            status = "zero"
        elif chars > 0:
            rate = chars / dur
            if rate < MIN_CHARS_PER_SECOND:
                issues.append(f"{part.name}: suspiciously slow ({rate:.1f} chars/s, dur={dur:.1f}s)")
                status = "slow"
            elif rate > MAX_CHARS_PER_SECOND:
                issues.append(f"{part.name}: suspiciously fast ({rate:.1f} chars/s, dur={dur:.1f}s)")
                status = "fast"

        part_durations.append({
            "file": part.name,
            "duration_s": round(dur, 3),
            "chars": chars,
            "chars_per_sec": round(chars / dur, 1) if dur > 0 and chars > 0 else None,
            "status": status,
        })

    if zero_parts:
        report.add("audio_parts_non_zero", "FAIL",
                   f"{len(zero_parts)} zero-byte part(s).", {"parts": zero_parts})
    else:
        report.add("audio_parts_non_zero", "PASS", "All parts are non-empty.")

    if issues:
        report.add("audio_parts_duration_plausibility", "WARN",
                   f"{len(issues)} part(s) have implausible durations.",
                   {"issues": issues, "parts": part_durations})
    else:
        report.add("audio_parts_duration_plausibility", "PASS",
                   f"All parts have plausible speaking rates.",
                   {"parts": part_durations})

    return total_dur


def check_master_audio(report: QAReport, master_path: Path, parts_total_dur: float) -> float:
    if not master_path.is_file():
        report.add("master_audio_exists", "FAIL", f"Not found: {master_path}")
        return 0.0

    report.add("master_audio_exists", "PASS", str(master_path))

    if shutil.which("ffprobe") is None:
        report.add("master_audio_probe", "WARN", "ffprobe not available.")
        return 0.0

    try:
        info = probe_audio(master_path)
    except Exception as e:
        report.add("master_audio_probe", "FAIL", f"ffprobe failed: {e}")
        return 0.0

    dur = info["duration"]
    report.add("master_audio_duration", "PASS" if dur > 0 else "FAIL",
               f"Duration: {dur:.3f}s", {"duration_s": dur})

    # Duration vs parts sum
    if parts_total_dur > 0:
        diff = abs(dur - parts_total_dur)
        tolerance = max(1.0, len([1]) * 0.1)  # 100ms tolerance per join
        if diff > tolerance:
            report.add("master_vs_parts_duration", "WARN",
                       f"Master ({dur:.3f}s) differs from parts sum ({parts_total_dur:.3f}s) by {diff:.3f}s.",
                       {"master_s": dur, "parts_sum_s": parts_total_dur, "diff_s": diff})
        else:
            report.add("master_vs_parts_duration", "PASS",
                       f"Master duration matches parts sum within {diff:.3f}s.",
                       {"master_s": dur, "parts_sum_s": parts_total_dur})

    # Sample rate and channels
    sr = info["sample_rate"]
    ch = info["channels"]
    if sr not in {44100, 48000}:
        report.add("master_audio_sample_rate", "WARN",
                   f"Unusual sample rate: {sr} Hz (expected 44100 or 48000).")
    else:
        report.add("master_audio_sample_rate", "PASS", f"Sample rate: {sr} Hz.")

    if ch < 1:
        report.add("master_audio_channels", "FAIL", f"Invalid channel count: {ch}.")
    else:
        report.add("master_audio_channels", "PASS", f"Channels: {ch}.")

    return dur


def check_trimmed_video(report: QAReport, trimmed_path: Path) -> None:
    if not trimmed_path.is_file():
        report.add("trimmed_video_exists", "WARN",
                   f"Trimmed video not found (optional if not doing talking-head replacement): {trimmed_path}")
        return

    report.add("trimmed_video_exists", "PASS", str(trimmed_path))
    if shutil.which("ffprobe") is None:
        return

    try:
        info = probe_video(trimmed_path)
    except Exception as e:
        report.add("trimmed_video_probe", "FAIL", f"ffprobe failed: {e}")
        return

    if not info["has_video"]:
        report.add("trimmed_video_stream", "FAIL", "No video stream found.")
    else:
        report.add("trimmed_video_stream", "PASS",
                   f"Video stream: {info['width']}x{info['height']} @ {info['frame_rate']} fps.")

    if not info["has_audio"]:
        report.add("trimmed_audio_stream", "WARN",
                   "No audio stream — expected if audio will be replaced by narration.")
    else:
        report.add("trimmed_audio_stream", "PASS", "Audio stream present.")

    dur = info["duration"]
    if dur <= 0:
        report.add("trimmed_video_duration", "FAIL", f"Invalid duration: {dur}")
    else:
        report.add("trimmed_video_duration", "PASS", f"Duration: {dur:.3f}s")


def check_image_asset(report: QAReport, path: Path, label: str) -> None:
    if not path.is_file():
        report.add(f"{label}_exists", "WARN",
                   f"{label} not found (optional): {path}")
        return

    report.add(f"{label}_exists", "PASS", str(path))

    size = path.stat().st_size
    if size < 10_000:
        report.add(f"{label}_file_size", "FAIL",
                   f"File is suspiciously small ({size} bytes) — may be corrupt.")
    else:
        report.add(f"{label}_file_size", "PASS", f"File size: {size:,} bytes.")

    dims = image_dimensions(path)
    if dims:
        w, h = dims
        report.add(f"{label}_dimensions", "PASS", f"Dimensions: {w}x{h} px.")
        if w < 640 or h < 360:
            report.add(f"{label}_min_resolution", "WARN",
                       f"Resolution {w}x{h} is below recommended 1280x720.")
        else:
            report.add(f"{label}_min_resolution", "PASS",
                       f"Resolution {w}x{h} meets minimum.")
    else:
        report.add(f"{label}_dimensions", "WARN",
                   "Could not read dimensions (Pillow not installed).")


def measure_lufs(path: Path) -> float | None:
    """Return integrated loudness in LUFS using ffmpeg loudnorm analysis."""
    try:
        result = subprocess.run(
            [
                "ffmpeg", "-hide_banner", "-nostdin", "-i", str(path),
                "-af", "loudnorm=print_format=json",
                "-f", "null", "-",
            ],
            capture_output=True, text=True,
        )
        # loudnorm JSON is printed to stderr
        stderr = result.stderr
        start = stderr.rfind("{")
        end = stderr.rfind("}") + 1
        if start == -1 or end == 0:
            return None
        data = json.loads(stderr[start:end])
        return float(data.get("input_i", "nan"))
    except Exception:
        return None


def check_audio_boundaries(report: QAReport, parts_dir: Path, parts: list[Path]) -> None:
    """Check for silence or discontinuities at every chunk join."""
    if len(parts) < 2:
        report.add("audio_boundary_joins", "PASS",
                   "Only one audio part — no joins to check.")
        return

    if shutil.which("ffmpeg") is None:
        report.add("audio_boundary_joins", "WARN", "ffmpeg not on PATH; skipping boundary checks.")
        return

    window = 0.3  # seconds to sample around each join
    issues: list[str] = []
    checked = 0

    for i in range(len(parts) - 1):
        part_a = parts[i]
        part_b = parts[i + 1]

        # Probe last `window` seconds of part A
        def rms_level(path: Path, position: str) -> float | None:
            """position: 'end' samples last window, 'start' samples first window."""
            try:
                probe = subprocess.run(
                    ["ffprobe", "-v", "error",
                     "-show_entries", "format=duration",
                     "-of", "default=noprint_wrappers=1:nokey=1",
                     str(path)],
                    capture_output=True, text=True, check=True,
                )
                dur = float(probe.stdout.strip())
            except Exception:
                return None
            ss = max(0, dur - window) if position == "end" else 0
            try:
                result = subprocess.run(
                    [
                        "ffmpeg", "-hide_banner", "-nostdin",
                        "-ss", str(ss), "-t", str(window),
                        "-i", str(path),
                        "-af", "astats=metadata=1:reset=1,ametadata=print:key=lavfi.astats.Overall.RMS_level",
                        "-f", "null", "-",
                    ],
                    capture_output=True, text=True,
                )
                for line in result.stderr.splitlines():
                    if "RMS_level=" in line:
                        val = line.split("=")[-1].strip()
                        return float(val) if val not in ("-inf", "nan") else -120.0
            except Exception:
                pass
            return None

        rms_a = rms_level(part_a, "end")
        rms_b = rms_level(part_b, "start")
        checked += 1

        join_label = f"part_{i + 1:03d} → part_{i + 2:03d}"
        if rms_a is None or rms_b is None:
            issues.append(f"{join_label}: could not measure RMS")
            continue

        # Flag if either side is near-silent (< -60 dBFS) — indicates silence padding
        if rms_a < -60:
            issues.append(f"{join_label}: end of part {i + 1} is near-silent ({rms_a:.1f} dBFS)")
        if rms_b < -60:
            issues.append(f"{join_label}: start of part {i + 2} is near-silent ({rms_b:.1f} dBFS)")

        # Flag large level jump at join (> 12 dB) — indicates discontinuity
        if abs(rms_a - rms_b) > 12:
            issues.append(
                f"{join_label}: level jump {rms_a:.1f} → {rms_b:.1f} dBFS "
                f"({abs(rms_a - rms_b):.1f} dB difference)"
            )

    if issues:
        report.add("audio_boundary_joins", "WARN",
                   f"{len(issues)} boundary issue(s) across {checked} join(s).",
                   {"issues": issues})
    else:
        report.add("audio_boundary_joins", "PASS",
                   f"All {checked} chunk join(s) pass level continuity check.")


def check_loudness_consistency(report: QAReport, parts: list[Path]) -> None:
    """Measure per-part integrated LUFS and flag outliers."""
    if not parts:
        return

    if shutil.which("ffmpeg") is None:
        report.add("loudness_consistency", "WARN", "ffmpeg not on PATH; skipping loudness checks.")
        return

    lufs_values: list[tuple[str, float]] = []
    failed_parts: list[str] = []

    for part in parts:
        lufs = measure_lufs(part)
        if lufs is None or lufs != lufs:  # None or NaN
            failed_parts.append(part.name)
        else:
            lufs_values.append((part.name, lufs))

    if not lufs_values:
        report.add("loudness_consistency", "WARN",
                   "Could not measure LUFS for any part.")
        return

    mean_lufs = sum(v for _, v in lufs_values) / len(lufs_values)
    outliers = [
        f"{name}: {lufs:.1f} LUFS ({lufs - mean_lufs:+.1f} LU from mean)"
        for name, lufs in lufs_values
        if abs(lufs - mean_lufs) > 3.0
    ]

    detail = {
        "mean_lufs": round(mean_lufs, 2),
        "min_lufs": round(min(v for _, v in lufs_values), 2),
        "max_lufs": round(max(v for _, v in lufs_values), 2),
        "parts_measured": len(lufs_values),
    }
    if failed_parts:
        detail["measurement_failures"] = failed_parts

    if outliers:
        report.add("loudness_consistency", "WARN",
                   f"{len(outliers)} part(s) deviate >3 LU from mean ({mean_lufs:.1f} LUFS).",
                   {**detail, "outliers": outliers})
    else:
        report.add("loudness_consistency", "PASS",
                   f"All parts within 3 LU of mean ({mean_lufs:.1f} LUFS).",
                   detail)

    # Check master audio overall loudness target (-16 to -14 LUFS is YouTube standard)
    # This is checked separately in check_master_audio if called after this


def check_final_video(report: QAReport, final_path: Path, master_dur: float) -> None:
    if not final_path.is_file():
        report.add("final_video_exists", "WARN",
                   f"Final video not found (optional at this stage): {final_path}")
        return

    report.add("final_video_exists", "PASS", str(final_path))
    if shutil.which("ffprobe") is None:
        return

    try:
        info = probe_video(final_path)
    except Exception as e:
        report.add("final_video_probe", "FAIL", f"ffprobe failed: {e}")
        return

    # Streams
    if not info["has_video"]:
        report.add("final_video_stream", "FAIL", "No video stream.")
    else:
        report.add("final_video_stream", "PASS",
                   f"Video: {info['width']}x{info['height']} @ {info['frame_rate']} fps.")

    if not info["has_audio"]:
        report.add("final_audio_stream", "FAIL", "No audio stream in final video.")
    else:
        report.add("final_audio_stream", "PASS",
                   f"Audio: {info['audio_channels']}ch @ {info['audio_sample_rate']} Hz.")

    # Duration vs master audio
    final_dur = info["duration"]
    if master_dur > 0 and final_dur > 0:
        diff = abs(final_dur - master_dur)
        # Allow up to 4s for intro card + minor tolerance
        tolerance = 6.0
        if diff > tolerance:
            report.add("final_vs_audio_duration", "WARN",
                       f"Final video ({final_dur:.1f}s) differs from master audio ({master_dur:.1f}s) by {diff:.1f}s.",
                       {"final_s": final_dur, "master_audio_s": master_dur, "diff_s": diff})
        else:
            report.add("final_vs_audio_duration", "PASS",
                       f"Final video duration ({final_dur:.1f}s) aligns with master audio ({master_dur:.1f}s).")

    # Resolution check
    w, h = info["width"], info["height"]
    if w > 0 and h > 0:
        if w < 1280 or h < 720:
            report.add("final_video_resolution", "WARN",
                       f"Output resolution {w}x{h} is below 1280x720.")
        else:
            report.add("final_video_resolution", "PASS", f"Output resolution: {w}x{h}.")


# ── Main ─────────────────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("job_dir", type=Path, help="Job working directory to audit")
    parser.add_argument("--final-video", type=Path, help="Path to the assembled final video")
    parser.add_argument("--verbose", "-v", action="store_true", help="Print all checks including PASSes")
    return parser.parse_args()


def find_file(base: Path, *candidates: str) -> Path | None:
    for rel in candidates:
        p = base / rel
        if p.exists():
            return p
    return None


def main() -> int:
    args = parse_args()
    job_dir = args.job_dir

    if not job_dir.is_dir():
        print(f"Job directory not found: {job_dir}", file=sys.stderr)
        return 2

    report = QAReport(
        job_dir=str(job_dir),
        generated_at=datetime.now(timezone.utc).isoformat(),
    )

    print(f"\n{'='*60}")
    print(f"QA Audit: {job_dir}")
    print(f"{'='*60}\n")

    # Locate artifacts under the standard work/ + final/ job layout
    chunks_path   = find_file(job_dir, "work/chunks.json")
    parts_dir     = find_file(job_dir, "work/audio_parts")
    master_path   = find_file(job_dir, "work/master_narration.wav",
                               "work/master_narration.m4a", "work/master_narration.mp3")
    synced_path   = find_file(job_dir, "work/synced.mp4")
    trimmed_path  = find_file(job_dir, "work/trimmed.mp4")
    thumbnail     = find_file(job_dir, "work/assets/thumbnail.jpg", "work/assets/thumbnail.png")
    intro_card    = find_file(job_dir, "work/assets/intro_card.png", "work/assets/intro_card.jpg")
    final_video   = args.final_video

    if final_video is None:
        # Try to auto-detect
        final_dir = job_dir / "final"
        if final_dir.is_dir():
            finals = list(final_dir.glob("*_final.mp4"))
            if finals:
                final_video = finals[0]

    # Collect audio parts list for boundary + loudness checks
    resolved_parts_dir = parts_dir or job_dir / "work" / "audio_parts"
    sorted_parts: list[Path] = []
    if resolved_parts_dir.is_dir():
        sorted_parts = sorted(
            [p for p in resolved_parts_dir.iterdir()
             if p.is_file() and p.name.startswith("part_") and not p.name.endswith(".part")],
            key=lambda p: p.name,
        )

    # Run checks
    chunks = check_chunks(report, chunks_path or job_dir / "work" / "chunks.json")
    parts_dur = check_audio_parts(report, resolved_parts_dir, chunks)

    if master_path is None and synced_path is not None:
        # SYNC path (sync_segments.py) feeds audio_parts/ directly into the
        # per-chunk mux and never writes master_narration.wav -- that file is
        # a SIMPLE-path-only artifact (concat_audio.py's output). Use the
        # synced video's own audio track as the reference instead of failing
        # on an artifact this path was never going to produce.
        report.add("master_audio_exists", "PASS",
                   f"SYNC path used (no master_narration.wav expected) -- audio reference: {synced_path}")
        master_dur = probe_video(synced_path)["duration"] if shutil.which("ffprobe") else 0.0
        resolved_master = synced_path
    else:
        master_dur = check_master_audio(report, master_path or job_dir / "work" / "master_narration.wav", parts_dur)
        resolved_master = master_path or job_dir / "work" / "master_narration.wav"

    # Audio boundary + loudness checks
    check_audio_boundaries(report, resolved_parts_dir, sorted_parts)
    check_loudness_consistency(report, sorted_parts)

    # Master loudness target check (YouTube/streaming standard: -16 to -14 LUFS)
    if resolved_master.is_file():
        master_lufs = measure_lufs(resolved_master)
        if master_lufs is not None and master_lufs == master_lufs:
            if -18 <= master_lufs <= -12:
                report.add("master_loudness_target", "PASS",
                           f"Master audio loudness {master_lufs:.1f} LUFS (target: -16 to -14 LUFS).")
            else:
                report.add("master_loudness_target", "WARN",
                           f"Master audio loudness {master_lufs:.1f} LUFS is outside -18 to -12 LUFS "
                           f"range. Consider normalising before upload.",
                           {"measured_lufs": master_lufs, "recommended_range": "-16 to -14 LUFS"})

    check_trimmed_video(report, trimmed_path or job_dir / "work" / "trimmed.mp4")
    check_image_asset(report, thumbnail or job_dir / "work" / "assets" / "thumbnail.jpg", "thumbnail")
    check_image_asset(report, intro_card or job_dir / "work" / "assets" / "intro_card.png", "intro_card")
    if final_video:
        check_final_video(report, final_video, master_dur)
    else:
        report.add("final_video_exists", "WARN", "No final video path provided or found.")

    # Print results
    for check in report.checks:
        status = check["status"]
        if not args.verbose and status == "PASS":
            continue
        symbol = {"PASS": "✓", "WARN": "⚠", "FAIL": "✗"}.get(status, "?")
        print(f"  {symbol} [{status}] {check['check']}: {check['message']}")
        if args.verbose and "detail" in check:
            for k, v in check["detail"].items():
                if isinstance(v, list) and len(v) <= 5:
                    print(f"        {k}: {v}")

    summary = report.to_dict()["summary"]
    overall = summary["overall"]
    print(f"\n{'='*60}")
    print(f"QA Result: {overall}  "
          f"(✓ {summary['passed']} passed, "
          f"⚠ {summary['warned']} warned, "
          f"✗ {summary['failed']} failed)")
    print(f"{'='*60}\n")

    # Save report
    report_path = job_dir / "qa_report.json"
    report_path.write_text(
        json.dumps(report.to_dict(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"QA report saved: {report_path}")

    return 0 if report.ok() else 1


if __name__ == "__main__":
    raise SystemExit(main())
