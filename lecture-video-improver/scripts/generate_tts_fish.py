#!/usr/bin/env python3
"""Plan or execute sequential English Fish Audio TTS from chunks JSON.

Mirrors the interface of generate_tts.py so the same chunks.json and
concat_audio.py workflow works with either provider.

Environment variables:
  FISH_API_KEY          Required for --execute. Get one at fish.audio/app/api-keys/
  FISH_AUDIO_VOICE_ID   Default voice (reference_id). Pass --voice-id to override.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


FISH_AUDIO_BASE_URL = "https://api.fish.audio"
TTS_ENDPOINT = f"{FISH_AUDIO_BASE_URL}/v1/tts"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "chunks", type=Path, help="JSON produced by chunk_transcript.py"
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--voice-id",
        default=os.getenv("FISH_AUDIO_VOICE_ID"),
        help="Fish Audio reference_id (voice model ID). Optional — omit to use the platform default.",
    )
    parser.add_argument(
        "--format",
        default="mp3",
        choices=["mp3", "wav", "opus", "flac", "pcm"],
        help="Audio output format (default: mp3)",
    )
    parser.add_argument(
        "--mp3-bitrate",
        type=int,
        default=128,
        help="MP3 bitrate in kbps (default: 128). Only used when --format=mp3.",
    )
    parser.add_argument(
        "--latency",
        default="normal",
        choices=["normal", "balanced"],
        help="normal = higher quality; balanced = faster response (default: normal)",
    )
    parser.add_argument(
        "--speed",
        type=float,
        default=1.0,
        help="Speaking speed multiplier, 0.5–2.0 (default: 1.0)",
    )
    parser.add_argument(
        "--only",
        help="Comma-separated chunk indexes to generate, e.g. 2,4",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Skip existing non-empty parts verified by the manifest",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Replace selected derived parts",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Send paid API requests (dry run by default)",
    )
    parser.add_argument(
        "--no-normalize",
        action="store_true",
        help="Skip per-part loudness normalization (not recommended -- see normalize_loudness() docstring)",
    )
    parser.add_argument(
        "--allow-default-voice",
        action="store_true",
        help="Proceed with Fish Audio's platform default voice when no --voice-id/FISH_AUDIO_VOICE_ID "
             "is set. Without this flag, --execute refuses to run with no voice specified -- see "
             "the 'Voice selection' section in skills/build-video-voiceovers/SKILL.md for why.",
    )
    parser.add_argument("--timeout", type=float, default=180.0)
    return parser.parse_args()


def load_chunks(path: Path) -> list[dict]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, dict) and payload.get("language") not in {None, "en"}:
        raise ValueError("This generator accepts English chunks only.")
    chunks = payload if isinstance(payload, list) else payload.get("chunks")
    if not isinstance(chunks, list) or not chunks:
        raise ValueError(
            "Chunks JSON must contain a non-empty 'chunks' list or be a list."
        )
    normalized: list[dict] = []
    seen: set[int] = set()
    for position, item in enumerate(chunks, start=1):
        if not isinstance(item, dict) or not isinstance(item.get("text"), str):
            raise ValueError(f"Invalid chunk at position {position}.")
        index = int(item.get("chunk_index", position))
        text = item["text"].strip()
        if index < 1 or index in seen or not text:
            raise ValueError(f"Invalid or duplicate chunk index {index}.")
        seen.add(index)
        normalized.append({"chunk_index": index, "text": text})
    normalized.sort(key=lambda item: item["chunk_index"])
    return normalized


def parse_only(value: str | None) -> set[int] | None:
    if not value:
        return None
    try:
        result = {int(part.strip()) for part in value.split(",") if part.strip()}
    except ValueError as error:
        raise ValueError("--only must be comma-separated positive integers.") from error
    if not result or any(index < 1 for index in result):
        raise ValueError("--only must contain positive chunk indexes.")
    return result


def atomic_json(path: Path, payload: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".part")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def request_audio(
    api_key: str,
    voice_id: str | None,
    output_format: str,
    mp3_bitrate: int,
    latency: str,
    speed: float,
    text: str,
    timeout: float,
) -> bytes:
    body: dict = {
        "text": text,
        "format": output_format,
        "latency": latency,
    }
    if voice_id:
        body["reference_id"] = voice_id
    if output_format == "mp3":
        body["mp3_bitrate"] = mp3_bitrate
    if speed != 1.0:
        body["prosody"] = {"speed": speed}

    request = urllib.request.Request(
        TTS_ENDPOINT,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "User-Agent": "lecture-video-improver-plugin/1",
        },
        method="POST",
    )
    try:
        chunks: list[bytes] = []
        with urllib.request.urlopen(request, timeout=timeout) as response:
            while True:
                block = response.read(65536)
                if not block:
                    break
                chunks.append(block)
        audio = b"".join(chunks)
    except urllib.error.HTTPError as error:
        detail = error.read(1000).decode("utf-8", errors="replace")
        raise RuntimeError(f"Fish Audio HTTP {error.code}: {detail}") from error
    except urllib.error.URLError as error:
        raise RuntimeError(f"Fish Audio request failed: {error.reason}") from error
    if not audio:
        raise RuntimeError("Fish Audio returned an empty audio response.")
    return audio


def normalize_loudness(path: Path, target_lufs: float = -14.0) -> None:
    """Loudness-normalize a TTS part in place (single-pass, -14 LUFS target).

    Raw TTS output commonly lands 15-20 LU quieter than natural recorded
    speech (observed directly with Fish Audio S2.1 Pro output measuring
    around -33 LUFS against a -14 LUFS video target). A single loudnorm pass
    on the final concatenated master is NOT sufficient to fix this: that
    pass corrects overall *programme* loudness, dominated by whichever
    audio is longest, and cannot locally re-level short quiet segments
    sitting inside a longer track. Normalizing every part BEFORE concat
    closes the gap at the source instead of relying on qa_check.py's
    loudness_consistency check to merely flag it after the fact.
    """
    import subprocess as _sp
    tmp = path.with_suffix(path.suffix + ".loudnorm_tmp" + path.suffix)
    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
        "-i", str(path),
        "-af", f"loudnorm=I={target_lufs}:TP=-1:LRA=11:linear=true",
        str(tmp),
    ]
    _sp.run(cmd, check=True)
    tmp.replace(path)


def main() -> int:
    args = parse_args()

    if not 0.5 <= args.speed <= 2.0:
        print("--speed must be between 0.5 and 2.0.", file=sys.stderr)
        return 2
    if not args.chunks.is_file():
        print(f"Chunks file not found: {args.chunks}", file=sys.stderr)
        return 2

    try:
        chunks = load_chunks(args.chunks)
        selected = parse_only(args.only)
    except (ValueError, json.JSONDecodeError) as error:
        print(str(error), file=sys.stderr)
        return 2

    available = {item["chunk_index"] for item in chunks}
    if selected is not None and not selected <= available:
        print(
            f"Unknown chunk indexes: {sorted(selected - available)}", file=sys.stderr
        )
        return 2

    planned = [
        item for item in chunks if selected is None or item["chunk_index"] in selected
    ]
    total_characters = sum(len(item["text"]) for item in planned)
    voice_label = args.voice_id or "<platform default>"
    print(
        f"Plan: {len(planned)} English request(s), {total_characters} characters, "
        f"provider=Fish Audio, voice={voice_label}, "
        f"format={args.format}, latency={args.latency}, speed={args.speed}."
    )
    if not args.execute:
        print("Dry run only: no API requests sent. Add --execute after approval.")
        return 0

    if not args.voice_id and not args.allow_default_voice:
        print(
            "No Fish Audio voice specified (no --voice-id, and FISH_AUDIO_VOICE_ID is not set).\n"
            "Do not silently fall back to the platform default voice -- ask the user which "
            "Fish Audio model/voice ID to use. If they don't already have one cloned, clone a "
            "sample and generate a short test phrase across candidates before committing (see "
            "'Voice selection' in skills/build-video-voiceovers/SKILL.md).\n"
            "Pass --voice-id once you have an answer, or --allow-default-voice if the platform "
            "default is genuinely what's wanted here.",
            file=sys.stderr,
        )
        return 2

    api_key = os.getenv("FISH_API_KEY")
    if not api_key:
        print(
            "FISH_API_KEY is not set. Get an API key at fish.audio/app/api-keys/",
            file=sys.stderr,
        )
        return 2

    args.output_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = args.output_dir / "tts_manifest.json"
    chunks_digest = hashlib.sha256(args.chunks.read_bytes()).hexdigest()
    manifest = {
        "schema_version": 1,
        "language": "en",
        "provider": "fish_audio",
        "chunks_file": str(args.chunks),
        "chunks_sha256": chunks_digest,
        "voice_id": args.voice_id or "",
        "format": args.format,
        "latency": args.latency,
        "speed": args.speed,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "parts": [],
    }

    # Load and validate existing manifest
    records: dict[int, dict] = {}
    if manifest_path.exists():
        try:
            existing = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as error:
            print(f"Cannot read existing manifest: {error}", file=sys.stderr)
            return 2
        expected = {
            "provider": "fish_audio",
            "chunks_sha256": chunks_digest,
            "voice_id": args.voice_id or "",
            "format": args.format,
        }
        mismatches = [
            key for key, value in expected.items() if existing.get(key) != value
        ]
        if mismatches:
            print(
                "Existing manifest has different production settings "
                f"({', '.join(mismatches)}); use a new output directory.",
                file=sys.stderr,
            )
            return 2
        if isinstance(existing.get("parts"), list):
            manifest["parts"] = existing["parts"]
            records = {
                int(item["chunk_index"]): item
                for item in manifest["parts"]
                if "chunk_index" in item
            }

    for chunk in chunks:
        index = chunk["chunk_index"]
        if selected is not None and index not in selected:
            continue

        output_path = args.output_dir / f"part_{index:03d}.{args.format}"

        if output_path.exists() and output_path.stat().st_size > 0:
            if args.resume and not args.overwrite:
                record = records.get(index)
                if (
                    record is None
                    or record.get("sha256") != sha256_file(output_path)
                    or record.get("char_count") != len(chunk["text"])
                ):
                    print(
                        f"Existing part is not verified by the manifest: {output_path}",
                        file=sys.stderr,
                    )
                    return 2
                print(f"Skipping existing: {output_path}")
                continue
            if not args.overwrite:
                print(
                    f"Output exists; use --resume or --overwrite: {output_path}",
                    file=sys.stderr,
                )
                return 2

        print(f"Generating chunk {index} ({len(chunk['text'])} characters)...")
        try:
            audio = request_audio(
                api_key=api_key,
                voice_id=args.voice_id,
                output_format=args.format,
                mp3_bitrate=args.mp3_bitrate,
                latency=args.latency,
                speed=args.speed,
                text=chunk["text"],
                timeout=args.timeout,
            )
        except RuntimeError as error:
            print(str(error), file=sys.stderr)
            return 1

        temporary = output_path.with_suffix(output_path.suffix + ".part")
        temporary.write_bytes(audio)
        temporary.replace(output_path)

        try:
            if not args.no_normalize:
                normalize_loudness(output_path)
        except Exception as error:
            print(f"  [WARN] Chunk {index}: loudness normalization failed ({error}); "
                  f"keeping raw TTS levels — re-check with qa_check.py.", file=sys.stderr)

        # ── Per-part QA: probe duration and check plausibility ───────────────
        part_duration: float | None = None
        try:
            import subprocess as _sp
            probe = _sp.run(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                 "-of", "default=noprint_wrappers=1:nokey=1", str(output_path)],
                capture_output=True, text=True, check=True,
            )
            part_duration = float(probe.stdout.strip())
            chars = len(chunk["text"])
            rate = chars / part_duration if part_duration > 0 else 0
            if part_duration <= 0:
                print(f"  [QA WARN] Chunk {index}: zero or negative duration — may be corrupt.")
            elif rate < 5:
                print(f"  [QA WARN] Chunk {index}: suspiciously slow ({rate:.1f} chars/s, {part_duration:.1f}s)")
            elif rate > 25:
                print(f"  [QA WARN] Chunk {index}: suspiciously fast ({rate:.1f} chars/s, {part_duration:.1f}s)")
        except Exception:
            pass  # ffprobe not available or probe failed; skip QA

        records[index] = {
            "chunk_index": index,
            "char_count": len(chunk["text"]),
            "file": str(output_path),
            "bytes": output_path.stat().st_size,
            # Hash the FINAL file on disk, not the in-memory `audio` bytes --
            # normalize_loudness() above rewrites output_path in place, so
            # hashing `audio` records the pre-normalization content and
            # --resume's verification against the actual (post-normalization)
            # file would then always fail. Real bug, found via a real resume
            # attempt on a previously-completed job.
            "sha256": sha256_file(output_path),
            **({"duration_s": round(part_duration, 3)} if part_duration is not None else {}),
        }
        manifest["parts"] = [records[key] for key in sorted(records)]
        manifest["updated_at"] = datetime.now(timezone.utc).isoformat()
        atomic_json(manifest_path, manifest)
        print(f"Wrote: {output_path}")

    print(f"Generation complete. Manifest: {manifest_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
