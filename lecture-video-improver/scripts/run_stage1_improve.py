#!/usr/bin/env python3
"""Stage 1 of the two-stage pipeline: turn raw video + transcript into a
locally-reviewable, English-improved final video. Stage 2 (upload_to_youtube.py
/ run_stage2_publish.py) is a deliberately separate step, run only after a
human has reviewed Stage 1's local output -- nothing in this script publishes
anywhere.

Assembly path -- SYNC, always: chunk_transcript_with_timestamps.py ->
generate_tts -> sync_segments.py. Each chunk's video segment is individually
trimmed or freeze-extended to match its TTS clip's natural duration, so sync
never silently drifts across a long video. This is the path verified against
real hand-built output in this project and confirmed to match its quality
(loudness within 0.15 LU, same freeze-extend behavior on the same content).
It requires a word-level source transcript (to anchor chunks to exact video
timestamps) and a "cleaned_script.json" (anchor + clean text pairs, see
chunk_transcript_with_timestamps.py) rather than plain cleaned text -- the
anchors are what tie each chunk back to an exact video span. A job with no
resolvable source transcript at all (no 'source_transcript', and no
'transcript_youtube_url'/'source_video_url' to pull captions from) fails
outright rather than silently degrading to an un-synced assembly -- provide
one of those fields. (chunk_transcript.py + concat_audio.py remain in
scripts/ as standalone tools for the different case of narration written
fresh over B-roll with no source recording to sync against; this
orchestrator no longer calls them.)

Processes a job manifest of N videos:
  0. Resolve inputs -- download the video from YouTube if source_video isn't
     already local (source_video_url), and/or extract a raw transcript from
     YouTube captions if no cleaned script exists yet (transcript_youtube_url,
     or source_video_url reused for captions when the video and transcript
     are the same recording).
     IMPORTANT: this step can only produce a RAW verbatim transcript, never a
     cleaned one -- turning disfluent raw speech into polished English is
     inherently a judgment call (filler classification in context, rewording
     decisions, a diff-percentage sign-off) that only an agent or human can
     make well, not something this script automates. If no cleaned_script.json
     exists yet after this step, the job PAUSES with status "needs_cleanup"
     and tells you exactly where the raw transcript is and where to save the
     cleaned result. Re-run the same command after that file exists and the
     job picks up exactly where it left off.
  1. Chunk the cleaned script via chunk_transcript_with_timestamps.py
  2. Diff review -- ALWAYS runs, not optional: measure_transcript_diff.py
     quantifies how much the cleaned script changed vs. the raw transcript
     and persists the report to work/diff_report.json. Set "max_change_pct"
     in the manifest or a job to make an over-aggressive rewrite a hard
     failure here instead of a number nobody looked at. If OPENAI_API_KEY
     is set, also scores meaning-preservation via embeddings (lexical %
     change alone can't tell "heavy paraphrase, same meaning" apart from
     "small edit, meaning flipped") -- set "min_semantic_similarity" (0-1)
     to hard-fail on that too.
  3. TTS: dry-run cost estimate for all jobs, then execute once approved
  4. Generate thumbnail and intro card (optional, requires OPENAI_API_KEY)
  5. Assemble via sync_segments.py, then prepend the intro card. If
     "welcome_message" is set (manifest-level default and/or per-job
     override), also prepend a short spoken welcome/branding clip in the
     SAME TTS voice via prepend_welcome.py -- e.g. "Welcome to the course.
     This is Lecture 3." Optional and additive: a failure here only warns (the intro-card
     final video is still valid without it). Fish Audio only for now
     (prepend_welcome.py calls generate_tts_fish.py's request_audio()
     directly) -- ignored with a warning if provider is "elevenlabs".
  6. Log per-job status to batch_log.json (completed / needs_cleanup / failed)

Job Manifest format (job_manifest.json):
  {
    "provider": "fish_audio",        // "fish_audio" or "elevenlabs"
    "voice_id": "abc123",            // optional, or set via env var
    "tts_model": "eleven_flash_v2",  // ElevenLabs only
    "generate_images": true,
    "card_style": "basic",           // "basic" (default, no cost, deterministic --
                                      // what a series wants) or "ai" (OpenAI
                                      // background per video, real cost, varies)
    // "basic" style branding -- set ONCE here, applied to every job, so a
    // whole series shares one visual identity. All optional (fall back to
    // generate_images.py's own defaults); irrelevant if card_style is "ai".
    "card_eyebrow": "CS 101: Introduction to Programming",
    "card_bg_color": "#16213E",
    "card_bg_color2": "#0B1120",
    "card_accent_color": "#2DD4BF",
    "card_text_color": "#FFFFFF",
    // "ai" style only:
    "image_model": "gpt-image-1.5",
    "image_quality": "medium",
    "welcome_message": "Welcome to the course. This is Lecture {lecture}.",
                                      // optional; a short spoken welcome/branding
                                      // clip prepended after the intro card, in
                                      // the job's own TTS voice (fish_audio only).
                                      // Supports {lecture}/{topic}/{title} substitution
                                      // from the job's own fields. Applies to every
                                      // job unless a job sets its own (below), or
                                      // "welcome_message": null to opt a job out.
    "max_change_pct": 40,            // optional; caps step 2's LEXICAL diff --
                                      // exceeding it is a hard failure, not a
                                      // warning. Applies to every job unless
                                      // a job sets its own (below).
    "min_semantic_similarity": 0.75, // optional; caps step 2's SEMANTIC diff
                                      // (needs OPENAI_API_KEY). Separate axis
                                      // from max_change_pct -- catches meaning
                                      // drift a lexical-only cap can miss.
    "output_base": "output/",
    "jobs": [
      {
        "id": "video_001",
        // EITHER a local path, OR a YouTube URL to download:
        "source_video": "raw/video_001.mp4",
        "source_video_url": "https://youtube.com/watch?v=...",
        // Word-level ASR transcript (Scribe-shaped), REQUIRED (directly, or
        // resolved in step 0 from YouTube captions via
        // transcript_youtube_url / source_video_url) -- anchors every chunk
        // to an exact video timestamp so sync_segments.py can keep sync.
        "source_transcript": "transcripts/video_001_words.json",
        "transcript_youtube_url": "https://youtube.com/watch?v=...",
        // Anchor + clean text pairs, produced by an agent/human from the raw
        // transcript. Omit to have the script look for
        // <output_base>/<id>/cleaned_script.json on re-run.
        "cleaned_script": "transcripts/video_001_script.json",
        "max_change_pct": 40,             // optional, overrides the manifest-level default
        "min_semantic_similarity": 0.75,  // optional, overrides the manifest-level default
        "welcome_message": null,          // optional, overrides the manifest-level default
                                           // (e.g. null to opt this one job out)
        "title": "Introduction to Python",
        "subtitle": "Beginner Series – Episode 1",
        "theme": "Python programming, coding, clean white background, modern",  // "ai" card_style only
        // Optional, only needed if you'll run Stage 2 (upload) afterward:
        "lecture": "1",
        "topic": "Introduction to Python",
        "module": null
      },
      ...
    ]
  }

Environment variables:
  FISH_API_KEY or ELEVENLABS_API_KEY   TTS provider key
  FISH_AUDIO_VOICE_ID or ELEVENLABS_VOICE_ID  Voice (overrides manifest voice_id)
  OPENAI_API_KEY                       Required for thumbnail generation

Usage:
  python3 run_stage1_improve.py job_manifest.json [--execute] [--skip-images] [--skip-video]
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

SCRIPTS_DIR = Path(__file__).parent


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("manifest", type=Path, help="Path to job_manifest.json")
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Execute paid TTS and image API calls (dry run by default)",
    )
    parser.add_argument(
        "--skip-images",
        action="store_true",
        help="Skip thumbnail and intro card generation",
    )
    parser.add_argument(
        "--skip-video",
        action="store_true",
        help="Skip FFmpeg video assembly (produce audio only)",
    )
    parser.add_argument(
        "--jobs",
        help="Comma-separated job IDs to process (default: all)",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Skip jobs already marked 'completed' in batch_log.json",
    )
    parser.add_argument(
        "--tts-timeout", type=float, default=180.0,
        help="Per-chunk TTS request timeout in seconds (default: 180)",
    )
    return parser.parse_args()


def load_manifest(path: Path) -> dict:
    if not path.is_file():
        raise FileNotFoundError(f"Manifest not found: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if "jobs" not in data or not isinstance(data["jobs"], list):
        raise ValueError("Manifest must contain a 'jobs' list.")
    return data


def load_log(log_path: Path) -> dict:
    if log_path.is_file():
        try:
            return json.loads(log_path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"jobs": {}}


def save_log(log_path: Path, log: dict) -> None:
    log["updated_at"] = datetime.now(timezone.utc).isoformat()
    # Unique per-process temp name -- a shared fixed name (the old
    # "batch_log.json.part") crashes when two processes race to write it:
    # one's tmp.replace() can find the file already consumed by the other,
    # raising FileNotFoundError. Only matters as defense in depth once
    # update_log_entry()'s lock is in place, but cheap to make robust anyway.
    tmp = log_path.with_suffix(f".json.{os.getpid()}.part")
    tmp.write_text(json.dumps(log, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(log_path)


def update_log_entry(log_path: Path, job_id: str, entry: dict) -> None:
    """Re-read the log fresh, patch in ONE job's entry, write it back --
    all while holding an exclusive lock, so concurrent processes actually
    serialize instead of racing.

    Two real bugs lived here, both found running the 71-video lecture
    series pilot batch with several jobs in parallel:
      1. The original code held one in-memory `log` dict for main()'s
         entire runtime and saved that whole dict on every per-job update.
         Whichever process finished last overwrote the file with its own
         stale snapshot, silently reverting every OTHER concurrently-running
         job's status back to whatever it was when THAT process started.
         Caught directly: three pilot jobs run concurrently, the last one
         to finish reverted the other two from 'completed' back to
         'needs_cleanup' in the log (their actual output files were
         untouched -- only the status tracking was wrong).
      2. Re-reading fresh before each write (without locking) shrinks that
         race window but does not close it -- verified by re-running 3
         concurrent jobs after the "fix": two of three log entries still
         went missing, because two processes' read-modify-write sequences
         still overlapped. A real mutual-exclusion lock is required, not
         just a smaller window.
    fcntl.flock on a dedicated lock file forces every concurrent process's
    read-modify-write to happen one at a time.
    """
    import fcntl
    lock_path = log_path.with_suffix(".json.lock")
    with open(lock_path, "w") as lock_file:
        fcntl.flock(lock_file, fcntl.LOCK_EX)
        try:
            log = load_log(log_path)
            log["jobs"][job_id] = entry
            save_log(log_path, log)
        finally:
            fcntl.flock(lock_file, fcntl.LOCK_UN)


def run_script(script_name: str, args_list: list[str]) -> tuple[bool, str]:
    """Run a bundled script and return (success, error_message)."""
    script = SCRIPTS_DIR / script_name
    cmd = [sys.executable, str(script)] + args_list
    result = subprocess.run(cmd, capture_output=True, text=True)
    output = (result.stdout + result.stderr).strip()
    if result.returncode != 0:
        return False, output
    return True, output


def probe_resolution(path: Path) -> tuple[int, int] | None:
    cmd = ["ffprobe", "-v", "error", "-select_streams", "v:0",
           "-show_entries", "stream=width,height", "-of", "csv=p=0", str(path)]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0 or not result.stdout.strip():
        return None
    try:
        w, h = result.stdout.strip().split(",")[:2]
        return int(w), int(h)
    except ValueError:
        return None


def prepend_intro_card(video: Path, intro_card: Path | None, output_path: Path) -> tuple[bool, str]:
    """Prepend a silent 3s intro card to sync_segments.py's output, or just
    copy through if there's no intro card. video and output_path may be the
    same path only if there IS no intro card (early-return case) -- callers
    should pass distinct paths otherwise.

    sync_segments.py's output is built by concatenating many individually
    trimmed/freeze-extended chunk segments, which leaves it with an
    irregular effective frame rate / time base (confirmed via ffprobe on a
    real job: avg_frame_rate came back as an ugly fraction, not a clean
    N/1). A naive `-f concat -c copy` join against a clean-CFR intro clip
    silently miscomputes duration against a source like that -- verified on
    a real 905s video, it came out as 988s with non-monotonic DTS, no error
    raised. The concat FILTER (decodes and re-times frames, rather than
    trusting container-level timestamps) doesn't have this failure mode,
    but it requires every input to share the same resolution -- the intro
    card is generate_images.py's 1536x864 thumbnail size, essentially never
    the source video's actual resolution, so it has to be scaled first.
    """
    if not intro_card or not intro_card.is_file():
        if video != output_path:
            cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
                   "-i", str(video), "-c", "copy", str(output_path)]
            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode != 0:
                return False, result.stderr.strip()
        return True, ""

    resolution = probe_resolution(video)
    if resolution is None:
        return False, f"Could not determine {video}'s resolution to scale the intro card to match."
    width, height = resolution

    intro_clip = output_path.with_name(output_path.stem + "_intro.mp4")
    intro_cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
        "-loop", "1", "-i", str(intro_card),
        "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
        "-t", "3",
        "-vf", f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
               f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1",
        "-c:v", "libx264", "-tune", "stillimage", "-c:a", "aac", "-b:a", "128k",
        "-pix_fmt", "yuv420p", "-shortest",
        str(intro_clip),
    ]
    result = subprocess.run(intro_cmd, capture_output=True, text=True)
    if result.returncode != 0:
        return False, f"Intro clip failed: {result.stderr.strip()}"

    concat_cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
        "-i", str(intro_clip), "-i", str(video),
        "-filter_complex", "[0:v][0:a][1:v][1:a]concat=n=2:v=1:a=1[vout][aout]",
        "-map", "[vout]", "-map", "[aout]",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart",
        str(output_path),
    ]
    result = subprocess.run(concat_cmd, capture_output=True, text=True)
    intro_clip.unlink(missing_ok=True)
    if result.returncode != 0:
        return False, f"Final concat failed: {result.stderr.strip()}"
    return True, ""


def resolve_video(job: dict, job_dir: Path, work_dir: Path, execute: bool) -> tuple[Path | None, str | None]:
    """Returns (resolved_local_path_or_None, error_or_None)."""
    given = job.get("source_video")
    if given and Path(given).is_file():
        return Path(given), None
    url = job.get("source_video_url")
    if not url:
        return None, "No 'source_video' local path (or it doesn't exist) and no 'source_video_url' given."

    resolved_path = work_dir / "resolved_source.mp4"
    if resolved_path.is_file():
        return resolved_path, None

    ok, msg = run_script("resolve_youtube_source.py", [
        "--download-video", url, "-o", str(resolved_path),
        *(["--execute"] if execute else []),
    ])
    for line in msg.splitlines():
        print(f"  [resolve-video] {line}")
    if not execute:
        return None, "dry-run: video download planned but not executed"
    if not ok or not resolved_path.is_file():
        return None, f"Video download failed: {msg.splitlines()[-1] if msg else 'unknown error'}"
    return resolved_path, None


def resolve_source_transcript(
    job: dict, job_dir: Path, work_dir: Path, resolved_video: Path | None, execute: bool,
) -> tuple[Path | None, str | None]:
    """Resolve the word-level source transcript that anchors every chunk to
    an exact video timestamp -- required for sync_segments.py.

    Returns (path_or_None, error_or_None). If both are None, no source
    transcript is available and none could be resolved -- caller must treat
    this as a hard failure (see process_job). error is set for a genuine
    dry-run-pending or resolution-failure signal.
    """
    given = job.get("source_transcript")
    if given and Path(given).is_file():
        return Path(given), None

    raw_path = work_dir / "raw_transcript.json"
    if raw_path.is_file():
        return raw_path, None

    caption_url = job.get("transcript_youtube_url") or job.get("source_video_url")
    if not caption_url:
        return None, None  # genuinely unavailable -- caller treats as a hard failure

    cmd = ["--download-captions", caption_url, "-o", str(raw_path)]
    if resolved_video:
        cmd += ["--compare-against", str(resolved_video)]
    if execute:
        cmd += ["--execute"]
    ok, msg = run_script("resolve_youtube_source.py", cmd)
    for line in msg.splitlines():
        print(f"  [resolve-source-transcript] {line}")
    if not execute:
        return None, "dry-run: raw transcript extraction planned but not executed"
    if not ok:
        return None, f"Raw transcript extraction failed: {msg.splitlines()[-1] if msg else 'unknown error'}"
    return raw_path, None


def resolve_cleaned_script(job: dict, job_dir: Path) -> Path | None:
    """SYNC path input: anchor + clean text pairs. None if not found yet
    (not an error -- caller decides what to do, e.g. pause for cleanup)."""
    given = job.get("cleaned_script")
    if given and Path(given).is_file():
        return Path(given)
    conventional = job_dir / "cleaned_script.json"
    if conventional.is_file():
        return conventional
    return None


def process_job(
    job: dict,
    manifest: dict,
    output_base: Path,
    args: argparse.Namespace,
) -> tuple[bool, str]:
    """Run the full pipeline for a single job. Returns (success, message).

    A "needs_cleanup" pause (see resolve_cleaned_script()) returns
    (False, msg) with msg starting with "needs_cleanup:" -- callers should
    treat that distinctly from a real failure.
    """
    job_id = job["id"]
    title = job.get("title", job_id)
    subtitle = job.get("subtitle", "")
    theme = job.get("theme", title)

    provider = manifest.get("provider", "fish_audio")
    voice_id = job.get("voice_id") or manifest.get("voice_id") or ""

    job_dir = output_base / job_id
    work_dir = job_dir / "work"
    work_dir.mkdir(parents=True, exist_ok=True)

    chunks_json = work_dir / "chunks.json"
    audio_parts_dir = work_dir / "audio_parts"
    assets_dir = work_dir / "assets"
    final_output = job_dir / "final" / f"{job_id}_final.mp4"
    final_output.parent.mkdir(parents=True, exist_ok=True)

    # ── Step 0: Resolve inputs ────────────────────────────────────────────────
    print(f"\n[{job_id}] Step 0/6: Resolving video and transcript sources...")
    source_video, video_err = resolve_video(job, job_dir, work_dir, args.execute)
    if video_err:
        if video_err.startswith("dry-run:"):
            return True, "dry-run"
        return False, video_err

    source_transcript, st_err = resolve_source_transcript(job, job_dir, work_dir, source_video, args.execute)
    if st_err:
        if st_err.startswith("dry-run:"):
            return True, "dry-run"
        return False, st_err
    if not source_transcript:
        return False, (
            "No word-level source transcript is available for this job (no 'source_transcript' "
            "resolved, and no 'transcript_youtube_url' / 'source_video_url' to pull captions "
            "from). sync_segments.py needs one to anchor chunks to exact video timestamps -- "
            "provide one of those fields in the job manifest."
        )
    print(f"[{job_id}]   video: {source_video}")
    print(f"[{job_id}]   source transcript: {source_transcript}")

    cleaned_script = resolve_cleaned_script(job, job_dir)
    if not cleaned_script:
        conventional_script = job_dir / "cleaned_script.json"
        fillers_report = work_dir / "raw_transcript.fillers.json"
        fillers_summary = ""
        ok, msg = run_script("detect_fillers.py", [str(source_transcript), "-o", str(fillers_report)])
        if ok and fillers_report.is_file():
            try:
                counts = json.loads(fillers_report.read_text(encoding="utf-8"))
                fillers_summary = (
                    f" A candidate filler scan is ready at {fillers_report} "
                    f"({len(counts.get('definite_fillers', []))} definite um/uh, "
                    f"{len(counts.get('repeats', []))} stutter repeats, "
                    f"{len(counts.get('discourse_fillers', []))} discourse fillers needing a "
                    f"context judgment -- start there instead of scanning the raw transcript cold, "
                    f"but it only finds candidates; deciding which are genuinely disposable and how "
                    f"to reword the surrounding speech is still yours to do.)"
                )
            except (json.JSONDecodeError, OSError):
                pass
        return False, (
            f"needs_cleanup: raw source transcript is at {source_transcript}.{fillers_summary} An "
            f"agent/human needs to produce a cleaned SCRIPT from it: a JSON list of {{'anchor': "
            f"'<verbatim short quote from the raw transcript>', 'clean': '<polished replacement "
            f"text>'}} pairs covering the content end to end (see chunk_transcript_with_timestamps.py "
            f"and the english-improvement skill). Save the result to {conventional_script}. Re-run "
            f"this exact command once that file exists -- Step 2 will then run "
            f"measure_transcript_diff.py against it automatically and report the lexical change % "
            f"(plus semantic similarity via embeddings, if OPENAI_API_KEY is set); set "
            f"'max_change_pct' and/or 'min_semantic_similarity' in the manifest if you want either "
            f"one to hard-fail there instead of just being reported."
        )
    print(f"[{job_id}]   cleaned script: {cleaned_script}")

    # ── Step 1: Chunk transcript ──────────────────────────────────────────────
    print(f"\n[{job_id}] Step 1/6: Chunking transcript...")
    chunk_args = [str(source_transcript), str(cleaned_script), str(chunks_json)]
    if chunks_json.exists():
        chunk_args += ["--overwrite"]
    ok, msg = run_script("chunk_transcript_with_timestamps.py", chunk_args)
    if not ok:
        return False, f"Chunking failed: {msg}"
    print(f"[{job_id}]   {msg.splitlines()[-1] if msg else 'OK'}")

    # ── Step 2: Diff review -- ALWAYS runs, not optional ──────────────────────
    # Quantifies how much the cleaned script actually changed vs. the raw
    # transcript, every single time, so nobody has to remember to run this
    # by hand before spending money on TTS. If the manifest sets
    # "max_change_pct", an over-aggressive rewrite hard-fails here instead
    # of silently shipping.
    print(f"[{job_id}] Step 2/6: Diff review (cleaned script vs. raw transcript)...")
    diff_report_path = work_dir / "diff_report.json"
    diff_args = [
        "--source-transcript", str(source_transcript),
        "--chunks", str(chunks_json),
        "-o", str(diff_report_path),
    ]
    max_change_pct = job.get("max_change_pct", manifest.get("max_change_pct"))
    if max_change_pct is not None:
        diff_args += ["--max-change-pct", str(max_change_pct)]
    min_semantic_similarity = job.get("min_semantic_similarity", manifest.get("min_semantic_similarity"))
    if os.environ.get("OPENAI_API_KEY"):
        diff_args += ["--semantic"]
        if min_semantic_similarity is not None:
            diff_args += ["--min-semantic-similarity", str(min_semantic_similarity)]
    else:
        print(f"[{job_id}]   (skipping semantic similarity -- OPENAI_API_KEY not set)")
    ok, msg = run_script("measure_transcript_diff.py", diff_args)
    for line in msg.splitlines():
        print(f"[{job_id}]   {line}")
    if not ok:
        return False, (
            f"Diff review failed: either the cleaned script changed more than the configured "
            f"max_change_pct ({max_change_pct}), or its semantic similarity fell below "
            f"min_semantic_similarity ({min_semantic_similarity}). Report: {diff_report_path}. "
            f"Either the rewrite drifted too far, or a cap needs revisiting -- this is a real "
            f"stop, not a formality; go back and look at what changed before raising a cap."
        )

    # ── Step 3: TTS dry-run (always) then execute if --execute ───────────────
    tts_script = "generate_tts_fish.py" if provider == "fish_audio" else "generate_tts.py"
    tts_args = [str(chunks_json), "--output-dir", str(audio_parts_dir)]
    if voice_id:
        tts_args += ["--voice-id", voice_id]
    if provider == "elevenlabs" and manifest.get("tts_model"):
        tts_args += ["--model-id", manifest["tts_model"]]
    tts_args += ["--timeout", str(args.tts_timeout)]

    print(f"[{job_id}] Step 3/6: TTS dry-run ({provider})...")
    ok, msg = run_script(tts_script, tts_args)
    if not ok:
        return False, f"TTS dry-run failed: {msg}"
    for line in msg.splitlines():
        print(f"[{job_id}]   {line}")

    if args.execute:
        print(f"[{job_id}]          Executing TTS...")
        ok, msg = run_script(tts_script, tts_args + ["--execute", "--resume"])
        if not ok:
            return False, f"TTS execution failed: {msg}"
        print(f"[{job_id}]   Done.")
    else:
        print(f"[{job_id}]   Skipping execution (dry run mode).")
        return True, "dry-run"

    # ── Step 3: Generate images ───────────────────────────────────────────────
    intro_card: Path | None = None
    if not args.skip_images and manifest.get("generate_images", True):
        print(f"[{job_id}] Step 4/6: Generating thumbnail + intro card...")
        card_style = manifest.get("card_style", "basic")
        img_args = [
            "--title", title,
            "--subtitle", subtitle,
            "--card-style", card_style,
            "--output-dir", str(assets_dir),
            "--execute",
        ]
        if card_style == "basic":
            # Every one of these should come from the manifest (fixed for
            # the whole series), not the job -- that's what makes N videos
            # in a series look like one product. Falls back to
            # generate_images.py's own defaults if the manifest sets none.
            if "card_eyebrow" in manifest:
                img_args += ["--eyebrow", manifest["card_eyebrow"]]
            if "card_bg_color" in manifest:
                img_args += ["--bg-color", manifest["card_bg_color"]]
            if "card_bg_color2" in manifest:
                img_args += ["--bg-color2", manifest["card_bg_color2"]]
            if "card_accent_color" in manifest:
                img_args += ["--accent-color", manifest["card_accent_color"]]
            if "card_text_color" in manifest:
                img_args += ["--text-color", manifest["card_text_color"]]
        else:
            img_args += [
                "--theme", theme,
                "--model", manifest.get("image_model", "gpt-image-1.5"),
                "--quality", manifest.get("image_quality", "medium"),
            ]
        video_resolution = probe_resolution(source_video) if source_video.is_file() else None
        if video_resolution:
            img_args += ["--target-size", f"{video_resolution[0]}x{video_resolution[1]}"]
        ok, msg = run_script("generate_images.py", img_args)
        if not ok:
            print(f"[{job_id}]   Warning: image generation failed: {msg}")
        else:
            intro_card = assets_dir / "intro_card.png"
            print(f"[{job_id}]   Assets saved.")
    else:
        print(f"[{job_id}] Step 4/6: Skipping image generation.")

    # ── Step 4: Assemble via sync_segments.py, then prepend intro card ────────
    if args.skip_video:
        print(f"[{job_id}] Step 5/6: Skipping video assembly (--skip-video).")
        return True, f"Audio only: audio_parts dir: {audio_parts_dir}"

    if not source_video.is_file():
        return False, f"Source video not found: {source_video}"

    print(f"[{job_id}] Step 5/6: Assembling via sync_segments.py (per-chunk trim/freeze-extend)...")
    synced_video = work_dir / "synced.mp4"
    sync_args = [str(chunks_json), str(audio_parts_dir), str(source_video), str(synced_video)]
    if synced_video.exists():
        sync_args += ["--overwrite"]
    ok, msg = run_script("sync_segments.py", sync_args)
    if not ok:
        return False, f"Sync assembly failed: {msg}"
    for line in msg.splitlines():
        print(f"[{job_id}]   {line}")

    ok, msg = prepend_intro_card(synced_video, intro_card, final_output)
    if not ok:
        return False, f"Intro card assembly failed: {msg}"
    print(f"[{job_id}]   Final video: {final_output}")

    welcome_message = job.get("welcome_message", manifest.get("welcome_message"))
    if welcome_message:
        if provider != "fish_audio":
            print(f"[{job_id}]   Skipping welcome message: prepend_welcome.py only "
                  f"supports the fish_audio provider currently (job provider: {provider}).")
        else:
            welcome_text = welcome_message.format(
                lecture=job.get("lecture", ""), topic=job.get("topic", ""), title=title,
            )
            print(f"[{job_id}]   Prepending welcome message ({len(welcome_text)} chars)...")
            welcome_args = [str(final_output), "--text", welcome_text]
            if voice_id:
                welcome_args += ["--voice-id", voice_id]
            if args.execute:
                welcome_args += ["--execute"]
            ok, msg = run_script("prepend_welcome.py", welcome_args)
            if not ok:
                print(f"[{job_id}]   Warning: welcome message step failed "
                      f"(final video is still valid without it): {msg}")
            else:
                for line in msg.splitlines():
                    print(f"[{job_id}]   {line}")

    return True, str(final_output)


def main() -> int:
    args = parse_args()

    if shutil.which("ffmpeg") is None:
        print("ffmpeg must be available on PATH.", file=sys.stderr)
        return 2

    try:
        manifest = load_manifest(args.manifest)
    except (FileNotFoundError, ValueError, json.JSONDecodeError) as error:
        print(str(error), file=sys.stderr)
        return 2

    output_base = Path(manifest.get("output_base", "output"))
    log_path = output_base / "batch_log.json"
    output_base.mkdir(parents=True, exist_ok=True)

    all_jobs = manifest["jobs"]

    # Filter to requested job IDs
    if args.jobs:
        requested = {j.strip() for j in args.jobs.split(",")}
        all_jobs = [j for j in all_jobs if j["id"] in requested]
        if not all_jobs:
            print(f"No jobs matched: {args.jobs}", file=sys.stderr)
            return 2

    log = load_log(log_path)

    # Skip completed jobs if --resume
    if args.resume:
        pending = [j for j in all_jobs if log["jobs"].get(j["id"], {}).get("status") != "completed"]
        skipped = len(all_jobs) - len(pending)
        if skipped:
            print(f"Resuming: skipping {skipped} already-completed job(s).")
        all_jobs = pending

    total = len(all_jobs)
    print(f"\n{'='*60}")
    print(f"Batch: {total} job(s) | provider: {manifest.get('provider', 'fish_audio')}")
    print(f"Mode: {'EXECUTE (paid API calls)' if args.execute else 'DRY RUN (no charges)'}")
    print(f"{'='*60}\n")

    if not args.execute:
        print("Running dry-run for all jobs to show cost estimates.\n")

    success_count = 0
    fail_count = 0
    cleanup_count = 0

    for i, job in enumerate(all_jobs, 1):
        job_id = job.get("id", f"job_{i}")
        print(f"─── Job {i}/{total}: {job_id} ───")

        # Validate required fields. source_video/cleaned_transcript are NOT
        # required directly -- they can be resolved from source_video_url /
        # transcript_youtube_url (see resolve_video/resolve_transcript), or a
        # cleaned_transcript.txt dropped in the job dir on a later re-run.
        if not job.get("title"):
            msg = "Missing required field: ['title']"
            print(f"[{job_id}] ERROR: {msg}", file=sys.stderr)
            update_log_entry(log_path, job_id, {"status": "failed", "error": msg,
                                   "timestamp": datetime.now(timezone.utc).isoformat()})
            fail_count += 1
            continue
        if not job.get("source_video") and not job.get("source_video_url"):
            msg = "Need either 'source_video' (local path) or 'source_video_url' (YouTube link)."
            print(f"[{job_id}] ERROR: {msg}", file=sys.stderr)
            update_log_entry(log_path, job_id, {"status": "failed", "error": msg,
                                   "timestamp": datetime.now(timezone.utc).isoformat()})
            fail_count += 1
            continue

        try:
            ok, result_msg = process_job(job, manifest, output_base, args)
        except Exception as exc:
            ok, result_msg = False, str(exc)

        # ── QA check after each completed job ─────────────────────────────
        qa_result: dict = {}
        if ok and result_msg != "dry-run" and not args.skip_video:
            job_dir = output_base / job_id
            final_video_path = job_dir / "final" / f"{job_id}_final.mp4"
            qa_args = [str(job_dir)]
            if final_video_path.is_file():
                qa_args += ["--final-video", str(final_video_path)]
            qa_ok, qa_msg = run_script("qa_check.py", qa_args)
            qa_result = {"qa_passed": qa_ok, "qa_summary": qa_msg.splitlines()[-3:] if qa_msg else []}
            if not qa_ok:
                print(f"[{job_id}] ⚠ QA found issues — review qa_report.json in job directory.")

        needs_cleanup = (not ok) and isinstance(result_msg, str) and result_msg.startswith("needs_cleanup:")
        status = "completed" if ok else ("needs_cleanup" if needs_cleanup else "failed")
        update_log_entry(log_path, job_id, {
            "status": status,
            "result": result_msg,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            **qa_result,
        })

        if ok:
            success_count += 1
            print(f"[{job_id}] ✓ {status}: {result_msg}")
        elif needs_cleanup:
            cleanup_count += 1
            print(f"[{job_id}] ⏸ PAUSED (needs English cleanup): {result_msg}")
        else:
            fail_count += 1
            print(f"[{job_id}] ✗ FAILED: {result_msg}", file=sys.stderr)

    print(f"\n{'='*60}")
    print(f"Batch complete: {success_count} succeeded, {cleanup_count} paused for cleanup, {fail_count} failed.")
    print(f"Log: {log_path}")
    if not args.execute:
        print("\nAdd --execute to run paid TTS and image API calls.")
    print(f"{'='*60}")

    if fail_count:
        return 1
    if cleanup_count:
        return 3  # distinct from 1 (real failure) -- work remains, but it's on a human/agent, not this script
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
