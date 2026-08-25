#!/usr/bin/env python3
"""Plan or execute sequential English ElevenLabs TTS from chunks JSON."""

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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "chunks", type=Path, help="JSON produced by chunk_transcript.py"
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--voice-id", default=os.getenv("ELEVENLABS_VOICE_ID"))
    parser.add_argument("--model-id", default="eleven_flash_v2")
    parser.add_argument("--output-format", default="mp3_44100_128")
    parser.add_argument("--context-chars", type=int, default=500)
    parser.add_argument("--seed", type=int)
    parser.add_argument("--only", help="Comma-separated chunk indexes, such as 2,4")
    parser.add_argument(
        "--resume", action="store_true", help="Skip existing non-empty parts"
    )
    parser.add_argument(
        "--overwrite", action="store_true", help="Replace selected derived parts"
    )
    parser.add_argument(
        "--execute", action="store_true", help="Send paid API requests"
    )
    parser.add_argument(
        "--no-normalize",
        action="store_true",
        help="Skip per-part loudness normalization (not recommended -- see normalize_loudness() docstring)",
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
        raise ValueError(
            "--only must be comma-separated positive integers."
        ) from error
    if not result or any(index < 1 for index in result):
        raise ValueError("--only must contain positive chunk indexes.")
    return result


def extension_for(output_format: str) -> str:
    codec = output_format.split("_", 1)[0].lower()
    return {
        "mp3": "mp3",
        "wav": "wav",
        "pcm": "pcm",
        "opus": "opus",
        "ulaw": "ulaw",
        "alaw": "alaw",
    }.get(codec, codec)


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
    voice_id: str,
    output_format: str,
    payload: dict,
    timeout: float,
) -> bytes:
    voice = urllib.parse.quote(voice_id, safe="")
    output = urllib.parse.quote(output_format, safe="")
    url = (
        f"https://api.elevenlabs.io/v1/text-to-speech/{voice}"
        f"?output_format={output}"
    )
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "xi-api-key": api_key,
            "Content-Type": "application/json",
            "Accept": "audio/*",
            "User-Agent": "lecture-video-improver-plugin/1",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            audio = response.read()
    except urllib.error.HTTPError as error:
        detail = error.read(1000).decode("utf-8", errors="replace")
        raise RuntimeError(f"ElevenLabs HTTP {error.code}: {detail}") from error
    except urllib.error.URLError as error:
        raise RuntimeError(f"ElevenLabs request failed: {error.reason}") from error
    if not audio:
        raise RuntimeError("ElevenLabs returned an empty audio response.")
    return audio


def normalize_loudness(path: Path, target_lufs: float = -14.0) -> None:
    """Loudness-normalize a TTS part in place (single-pass, -14 LUFS target).

    See the identical helper in generate_tts_fish.py for the full rationale:
    raw TTS output can land far quieter than natural speech, and a single
    loudnorm pass on the final concatenated master cannot fix that locally.
    Normalize every part before concat, not after.
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
    if args.context_chars < 0:
        print("--context-chars must be non-negative.", file=sys.stderr)
        return 2
    if "multilingual" in args.model_id.lower():
        print(
            "This English-only workflow does not use multilingual models.",
            file=sys.stderr,
        )
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
        item
        for item in chunks
        if selected is None or item["chunk_index"] in selected
    ]
    total_characters = sum(len(item["text"]) for item in planned)
    voice_label = args.voice_id or "<not set>"
    print(
        f"Plan: {len(planned)} English request(s), {total_characters} characters, "
        f"model={args.model_id}, voice={voice_label}, "
        f"format={args.output_format}."
    )
    if not args.execute:
        print("Dry run only: no API requests sent. Add --execute after approval.")
        return 0

    api_key = os.getenv("ELEVENLABS_API_KEY")
    if not api_key:
        print("ELEVENLABS_API_KEY is not set.", file=sys.stderr)
        return 2
    if not args.voice_id:
        print(
            "No ElevenLabs voice specified (no --voice-id, and ELEVENLABS_VOICE_ID is not set).\n"
            "Do not guess -- ask the user which ElevenLabs voice ID to use. If they don't already "
            "have one cloned, clone a sample and generate a short test phrase across candidates "
            "before committing (see 'Voice selection' in skills/build-video-voiceovers/SKILL.md).\n"
            "Pass --voice-id once you have an answer.",
            file=sys.stderr,
        )
        return 2

    args.output_dir.mkdir(parents=True, exist_ok=True)
    extension = extension_for(args.output_format)
    manifest_path = args.output_dir / "tts_manifest.json"
    chunks_digest = hashlib.sha256(args.chunks.read_bytes()).hexdigest()
    manifest = {
        "schema_version": 1,
        "language": "en",
        "chunks_file": str(args.chunks),
        "chunks_sha256": chunks_digest,
        "model_id": args.model_id,
        "voice_id": args.voice_id,
        "output_format": args.output_format,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "parts": [],
    }
    existing_manifest = None
    if manifest_path.exists():
        try:
            existing_manifest = json.loads(
                manifest_path.read_text(encoding="utf-8")
            )
        except (json.JSONDecodeError, OSError) as error:
            print(f"Cannot read existing manifest: {error}", file=sys.stderr)
            return 2
        expected = {
            "language": "en",
            "chunks_sha256": chunks_digest,
            "model_id": args.model_id,
            "voice_id": args.voice_id,
            "output_format": args.output_format,
        }
        mismatches = [
            key for key, value in expected.items() if existing_manifest.get(key) != value
        ]
        if mismatches:
            print(
                "Existing manifest has different production settings "
                f"({', '.join(mismatches)}); use a new output directory.",
                file=sys.stderr,
            )
            return 2
        if isinstance(existing_manifest.get("parts"), list):
            manifest["parts"] = existing_manifest["parts"]
    records = {
        int(item["chunk_index"]): item
        for item in manifest["parts"]
        if "chunk_index" in item
    }

    for position, chunk in enumerate(chunks):
        index = chunk["chunk_index"]
        if selected is not None and index not in selected:
            continue
        output_path = args.output_dir / f"part_{index:03d}.{extension}"
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

        previous_text = (
            chunks[position - 1]["text"][-args.context_chars :]
            if position > 0 and args.context_chars
            else None
        )
        next_text = (
            chunks[position + 1]["text"][: args.context_chars]
            if position + 1 < len(chunks) and args.context_chars
            else None
        )
        payload = {
            "text": chunk["text"],
            "model_id": args.model_id,
            "language_code": "en",
        }
        if previous_text:
            payload["previous_text"] = previous_text
        if next_text:
            payload["next_text"] = next_text
        if args.seed is not None:
            payload["seed"] = args.seed

        print(f"Generating chunk {index} ({len(chunk['text'])} characters)...")
        try:
            audio = request_audio(
                api_key,
                args.voice_id,
                args.output_format,
                payload,
                args.timeout,
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
            pass  # ffprobe not available; skip QA

        records[index] = {
            "chunk_index": index,
            "char_count": len(chunk["text"]),
            "file": str(output_path),
            "bytes": output_path.stat().st_size,
            # Hash the FINAL file on disk, not the in-memory `audio` bytes --
            # normalize_loudness() above rewrites output_path in place, so
            # hashing `audio` records the pre-normalization content and
            # --resume's verification against the actual (post-normalization)
            # file would then always fail. Same bug as generate_tts_fish.py.
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
