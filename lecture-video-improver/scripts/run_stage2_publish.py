#!/usr/bin/env python3
"""Stage 2 of the two-stage pipeline: publish Stage 1's reviewed local
outputs to YouTube. Deliberately separate from Stage 1 -- run this only
after a human has actually watched the finished videos in
<output_base>/<job_id>/final/.

Batch-scale confirmation, same exact-match principle as upload_to_youtube.py's
single-video --confirmed-title, just scaled up:
  1. Dry-run (default): scans <output_base> for completed Stage 1 jobs,
     computes each one's "L**" title from the SAME job manifest Stage 1 used
     (lecture/topic/module fields), and writes the full plan to
     publish_plan.json. Nothing is uploaded.
  2. Show that plan to the user -- every title, exactly as computed -- and
     get their explicit confirmation of the whole batch.
  3. Re-run with --execute --confirmed-plan publish_plan.json. This script
     recomputes the plan fresh and requires it to match the confirmed file
     BYTE FOR BYTE (via content hash). If anything changed since the plan was
     shown (a job's local video was regenerated, a title field edited) the
     hash won't match and nothing uploads -- go back and reconfirm the new
     plan instead of forcing the old one through.

Which jobs are eligible: any job directory under <output_base> with a
completed Stage 1 output (final/<job_id>_final.mp4 exists) AND lecture +
topic fields in the SAME job_manifest.json Stage 1 used. Jobs missing either
are skipped with a clear reason, not silently dropped.

YouTube's daily upload limit, for large batches (70+ videos): YouTube caps
how many videos an account can upload per day -- separate from the API's own
per-request quota (videos.insert costs 1 unit, its own 100/day bucket, not
the shared 10,000-unit pool most other endpoints share) and unrelated to
OAuth scope. This is an account-standing limit, and phone-verifying the
channel (youtube.com/verify) raises it substantially but doesn't remove it
outright -- verified on a real batch: 7 uploads/day before verification,
~32/day right after. If this hits (HttpError 400, reason
"uploadLimitExceeded"), this script stops itself immediately rather than
attempting every remaining job on a guaranteed-identical failure, and saves
the remaining job IDs to <output_base>/remaining_upload_jobs.json. The limit
resets on its own (observed ~24h) -- resume with --jobs pointed at that
file's contents once it has. Budget multiple days for very large batches.

Usage:
  # Dry-run -- always safe, writes/overwrites publish_plan.json for review
  python3 run_stage2_publish.py job_manifest.json

  # After the user has reviewed publish_plan.json in full
  python3 run_stage2_publish.py job_manifest.json \\
      --execute --confirmed-plan output/publish_plan.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

SCRIPTS_DIR = Path(__file__).parent


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("manifest", type=Path, help="Path to the SAME job_manifest.json Stage 1 used")
    parser.add_argument("--jobs", help="Comma-separated job IDs to publish (default: all eligible)")
    parser.add_argument("--privacy", default="private", choices=["private", "unlisted", "public"],
                        help="Default: private, applied to every video in this run")
    parser.add_argument("--made-for-kids", action="store_true",
                         help="Declare every video as directed at children under 13 (disables comments/"
                              "notifications/personalized ads). Default: NOT made for kids.")
    parser.add_argument("--title-prefix", default="",
                         help="Fixed text prepended to every video's title, e.g. a course/series name. "
                              "Truncates the lecture-specific part (never the prefix) to stay within "
                              "YouTube's 100-char title limit if needed -- flagged per-video in the plan.")
    parser.add_argument("--title-style", default="verbose", choices=["verbose", "compact"],
                         help="verbose (default): 'L1: Topic - Module 2'. compact: 'L01-M2-Topic'.")
    parser.add_argument("--output-base", type=Path, default=None,
                         help="Override the manifest's own 'output_base' -- e.g. to publish from a "
                              "different generated-output directory (a different voice run) without "
                              "editing the manifest file itself")
    parser.add_argument("--client-secrets", type=Path, default=Path("client_secret.json"))
    parser.add_argument("--token-file", type=Path, default=Path.home() / ".config" / "youtube_upload_token.json")
    parser.add_argument("--category-id", default="27", help="YouTube category ID for every video (default: 27 = Education)")
    parser.add_argument("--tags", default=None, help="Comma-separated tags applied to every video")
    parser.add_argument("--default-language", default="en")
    parser.add_argument("--no-thumbnail", action="store_true",
                         help="Skip setting each video's custom thumbnail (default: use work/assets/thumbnail.jpg if present)")
    parser.add_argument("--playlist-title", default=None,
                         help="Create a new playlist with this title and add every uploaded video to it, in "
                              "manifest order. Mutually exclusive with --playlist-id.")
    parser.add_argument("--playlist-id", default=None,
                         help="Add every uploaded video to this EXISTING playlist instead of creating a new one.")
    parser.add_argument("--no-playlist", action="store_true", help="Skip playlist creation/assignment entirely")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--confirmed-plan", type=Path, default=None,
                        help="Path to the publish_plan.json the user reviewed. Required with --execute; "
                             "its content must hash-match the freshly recomputed plan exactly.")
    return parser.parse_args()


_VOICE_LABEL_CACHE: dict[str, str] = {}


def describe_voice(voice_id: str | None) -> str | None:
    """Fetch a Fish Audio voice's real title/source from its API and turn
    it into a human-readable description -- e.g. "Fish Audio's \"Sarah\"
    voice" for a public library voice (source=voice_design) vs. "a cloned
    voice (\"...\")" for a user-uploaded clone (source=api). Never
    hardcodes specific voice IDs -- works for any Fish Audio voice_id.
    Returns None if it can't be determined (missing key, network error,
    non-Fish-Audio voice) -- callers should omit the line rather than
    guess."""
    if not voice_id:
        return None
    if voice_id in _VOICE_LABEL_CACHE:
        return _VOICE_LABEL_CACHE[voice_id]

    api_key = os.environ.get("FISH_API_KEY")
    if not api_key:
        return None

    try:
        req = urllib.request.Request(
            f"https://api.fish.audio/model/{voice_id}",
            headers={"Authorization": f"Bearer {api_key}"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
        return None

    title = data.get("title") or voice_id
    source = data.get("source")
    if source == "voice_design":
        label = f'Fish Audio\'s "{title}" voice'
    else:
        label = f'a cloned voice ("{title}")'
    _VOICE_LABEL_CACHE[voice_id] = label
    return label


def extract_summary(text: str, max_chars: int = 350) -> str:
    """Sentence-boundary-aware truncation of narration text into a short
    summary -- never cuts mid-sentence, so it reads naturally."""
    sentences = re.split(r"(?<=[.!?])\s+", text.strip())
    out = ""
    for sentence in sentences:
        candidate = f"{out} {sentence}".strip() if out else sentence
        if len(candidate) > max_chars and out:
            break
        out = candidate
        if len(out) >= max_chars:
            break
    return out


def generate_description(
    job_id: str, lecture: str, module: str | None, module_count: int,
    output_base: Path, card_eyebrow: str | None, voice_id: str | None,
) -> str:
    """Build a description genuinely derived from this job's own cleaned
    narration script -- not fabricated -- plus explicit lecture/module
    numbering and which TTS voice actually generated this video's audio
    (read from the job's own tts_manifest.json, not assumed)."""
    lines = [f"Lecture {lecture}" + (f", Module {module} of {module_count}" if module else "")]

    cleaned_path = output_base / job_id / "cleaned_script.json"
    if cleaned_path.is_file():
        try:
            data = json.loads(cleaned_path.read_text(encoding="utf-8"))
            chunks = data.get("chunks", data if isinstance(data, list) else [])
            text = " ".join(c.get("clean", "") for c in chunks if isinstance(c, dict))
            summary = extract_summary(text)
            if summary:
                lines += ["", summary]
        except (json.JSONDecodeError, OSError):
            pass

    voice_label = describe_voice(voice_id)
    if voice_label:
        lines += ["", f"Narration voice: {voice_label}."]

    if card_eyebrow:
        lines += ["", f"Part of {card_eyebrow}."]

    return "\n".join(lines)


MAX_TITLE_LEN = 100  # YouTube's hard limit


def compute_title(lecture: str, topic: str, module: str | None, prefix: str = "", style: str = "verbose") -> str:
    if style == "compact":
        base = f"L{int(lecture):02d}" + (f"-M{module}" if module else "") + f"-{topic}"
    else:
        base = f"L{lecture}: {topic}"
        if module:
            base += f" - Module {module}"
    title = prefix + base
    if len(title) > MAX_TITLE_LEN:
        available = MAX_TITLE_LEN - len(prefix) - 1
        title = prefix + base[:available].rstrip() + "…"
    return title


def read_voice_id(output_base: Path, job_id: str) -> str | None:
    """Ground truth for which voice actually generated this job's audio --
    read from its own tts_manifest.json, never assumed from the manifest's
    (possibly since-changed) top-level voice_id or from folder naming."""
    manifest_path = output_base / job_id / "work" / "audio_parts" / "tts_manifest.json"
    if not manifest_path.is_file():
        return None
    try:
        return json.loads(manifest_path.read_text(encoding="utf-8")).get("voice_id")
    except (json.JSONDecodeError, OSError):
        return None


def build_plan(manifest: dict, jobs_filter: set[str] | None, output_base_override: Path | None, title_prefix: str = "", title_style: str = "verbose") -> tuple[list[dict], list[str]]:
    output_base = output_base_override or Path(manifest.get("output_base", "output"))
    card_eyebrow = manifest.get("card_eyebrow")
    plan: list[dict] = []
    skipped: list[str] = []

    module_counts: dict[str, int] = {}
    for job in manifest.get("jobs", []):
        lecture = job.get("lecture")
        if lecture and job.get("module"):
            module_counts[str(lecture)] = module_counts.get(str(lecture), 0) + 1

    for job in manifest.get("jobs", []):
        job_id = job["id"]
        if jobs_filter is not None and job_id not in jobs_filter:
            continue

        final_video = output_base / job_id / "final" / f"{job_id}_final.mp4"
        if not final_video.is_file():
            skipped.append(f"{job_id}: no final video at {final_video} -- Stage 1 not completed yet")
            continue

        lecture = job.get("lecture")
        topic = job.get("topic")
        if not lecture or not topic:
            skipped.append(f"{job_id}: missing 'lecture' and/or 'topic' in job manifest -- can't compute a title")
            continue

        module = job.get("module")
        title = compute_title(str(lecture), str(topic), str(module) if module else None, title_prefix, title_style)
        voice_id = read_voice_id(output_base, job_id)
        description = job.get("description") or generate_description(
            job_id, str(lecture), str(module) if module else None,
            module_counts.get(str(lecture), 1), output_base, card_eyebrow, voice_id,
        )
        thumbnail = output_base / job_id / "work" / "assets" / "thumbnail.jpg"
        plan.append({
            "job_id": job_id,
            "video": str(final_video),
            "lecture": str(lecture),
            "topic": str(topic),
            "module": str(module) if module else None,
            "title": title,
            "description": description,
            "voice_id": voice_id,
            "thumbnail": str(thumbnail) if thumbnail.is_file() else None,
        })

    return plan, skipped


def plan_hash(plan: list[dict], settings: dict) -> str:
    canonical = json.dumps({"settings": settings, "plan": plan}, sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def create_playlist(title: str, privacy: str, client_secrets: Path, token_file: Path) -> str:
    from upload_to_youtube import get_authenticated_service
    youtube = get_authenticated_service(client_secrets, token_file)
    response = youtube.playlists().insert(
        part="snippet,status",
        body={"snippet": {"title": title}, "status": {"privacyStatus": privacy}},
    ).execute()
    return response["id"]


def main() -> int:
    args = parse_args()

    if args.playlist_title and args.playlist_id:
        print("--playlist-title and --playlist-id are mutually exclusive -- pick one.", file=sys.stderr)
        return 2

    if not args.manifest.is_file():
        print(f"Manifest not found: {args.manifest}", file=sys.stderr)
        return 2
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))

    jobs_filter = {j.strip() for j in args.jobs.split(",")} if args.jobs else None
    output_base = args.output_base or Path(manifest.get("output_base", "output"))
    plan, skipped = build_plan(manifest, jobs_filter, args.output_base, args.title_prefix, args.title_style)

    tags = [t.strip() for t in args.tags.split(",")] if args.tags else None
    settings = {
        "privacy": args.privacy, "category_id": args.category_id, "tags": tags,
        "default_language": args.default_language, "no_thumbnail": args.no_thumbnail,
        "playlist_title": args.playlist_title, "playlist_id": args.playlist_id,
        "no_playlist": args.no_playlist, "title_prefix": args.title_prefix, "title_style": args.title_style,
        "made_for_kids": args.made_for_kids,
    }

    plan_path = output_base / "publish_plan.json"
    plan_payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "settings": settings,
        "videos": plan,
        "skipped": skipped,
    }

    print(f"Publish plan ({len(plan)} video(s) eligible, {len(skipped)} skipped):\n")
    truncated = []
    for item in plan:
        thumb_note = "" if item["thumbnail"] else "  [no thumbnail found]"
        trunc_note = ""
        if item["title"].endswith("…"):
            trunc_note = "  [TRUNCATED to fit 100-char limit]"
            truncated.append(item["job_id"])
        print(f"  {item['job_id']}: \"{item['title']}\"  <- {item['video']}{thumb_note}{trunc_note}")
        for line in item["description"].splitlines():
            print(f"      | {line}")
    if truncated:
        print(f"\n{len(truncated)} title(s) truncated to fit YouTube's 100-char limit: {', '.join(truncated)}")
    if skipped:
        print("\nSkipped:")
        for s in skipped:
            print(f"  {s}")
    print(f"\nPrivacy: {args.privacy}  |  Made for kids: {args.made_for_kids}  |  Category: {args.category_id}  |  Language: {args.default_language}")
    print(f"Tags: {', '.join(tags) if tags else '(none)'}")
    if args.no_playlist:
        print("Playlist: none (--no-playlist)")
    elif args.playlist_id:
        print(f"Playlist: add all videos to existing playlist {args.playlist_id}")
    else:
        print(f"Playlist: create new playlist \"{args.playlist_title or '(derived from card_eyebrow/course name)'}\" "
              f"and add all {len(plan)} videos to it, in the order shown above")

    output_base.mkdir(parents=True, exist_ok=True)
    plan_path.write_text(json.dumps(plan_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"\nWrote {plan_path}")

    if not args.execute:
        print(
            "\nDry run only: nothing uploaded, no playlist created. Show the plan above (every title "
            f"and description, verbatim) to the user. Once confirmed, re-run with --execute "
            f"--confirmed-plan {plan_path}"
        )
        return 0

    if not plan:
        print("\nNothing to publish -- no eligible videos.", file=sys.stderr)
        return 1

    if not args.confirmed_plan or not args.confirmed_plan.is_file():
        print(
            f"\n--confirmed-plan is required with --execute and must point at a real file "
            f"(the {plan_path} the user reviewed).",
            file=sys.stderr,
        )
        return 2

    try:
        confirmed_payload = json.loads(args.confirmed_plan.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        print(f"--confirmed-plan is not valid JSON: {error}", file=sys.stderr)
        return 2

    confirmed_hash = plan_hash(confirmed_payload.get("videos", []), confirmed_payload.get("settings", {}))
    fresh_hash = plan_hash(plan, settings)
    if confirmed_hash != fresh_hash:
        print(
            "\nThe confirmed plan does not match the freshly recomputed plan. Something changed "
            "since it was reviewed (a job's output was regenerated, a title/description field "
            "changed, a run setting like privacy/tags/playlist differs, or a different set of jobs "
            "is eligible now). This is a hard stop, not a formality -- go back, show the NEW plan "
            "to the user, and get it confirmed again rather than forcing the stale one through.",
            file=sys.stderr,
        )
        return 2

    playlist_id = args.playlist_id
    if not args.no_playlist and not playlist_id:
        playlist_title = args.playlist_title or manifest.get("card_eyebrow") or "Lecture Series"
        print(f"\nCreating playlist \"{playlist_title}\"...")
        playlist_id = create_playlist(playlist_title, args.privacy, args.client_secrets, args.token_file)
        print(f"Created playlist: https://youtube.com/playlist?list={playlist_id}")

    print(f"\nConfirmed plan matches. Uploading {len(plan)} video(s)...\n")
    log_path = output_base / "publish_log.json"
    log = json.loads(log_path.read_text()) if log_path.is_file() else {"jobs": {}}
    if playlist_id:
        log["playlist_id"] = playlist_id

    success_count = 0
    fail_count = 0
    for index, item in enumerate(plan):
        job_id = item["job_id"]
        print(f"─── {job_id}: \"{item['title']}\" ───")
        # Pass the SAME lecture/topic/module Stage 2 used to compute item["title"],
        # so upload_to_youtube.py's own internal recomputation reproduces the
        # identical string by construction -- its --confirmed-title gate then
        # naturally passes rather than needing to be worked around.
        cmd = [
            sys.executable, str(SCRIPTS_DIR / "upload_to_youtube.py"), item["video"],
            "--lecture", item["lecture"], "--topic", item["topic"],
            "--title-prefix", args.title_prefix,
            "--title-style", args.title_style,
            "--description", item.get("description", ""),
            "--privacy", args.privacy,
            "--category-id", args.category_id,
            "--default-language", args.default_language,
            "--client-secrets", str(args.client_secrets),
            "--token-file", str(args.token_file),
            "--execute", "--confirmed-title", item["title"],
        ]
        if item["module"]:
            cmd += ["--module", item["module"]]
        if tags:
            cmd += ["--tags", ",".join(tags)]
        if playlist_id:
            cmd += ["--playlist-id", playlist_id]
        if not args.no_thumbnail and item["thumbnail"]:
            cmd += ["--thumbnail", item["thumbnail"]]
        if args.made_for_kids:
            cmd += ["--made-for-kids"]
        result = subprocess.run(cmd, capture_output=True, text=True)
        ok = result.returncode == 0
        output = (result.stdout + result.stderr).strip()
        for line in output.splitlines():
            print(f"  {line}")

        video_id_match = re.search(r"watch\?v=([\w-]+)", output)
        log["jobs"][job_id] = {
            "status": "published" if ok else "failed",
            "title": item["title"],
            "video_id": video_id_match.group(1) if video_id_match else None,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        # Write after EVERY job, not just once at the end -- a killed/crashed
        # process (including the daily-cap self-stop below, or a plain Ctrl-C)
        # would otherwise lose the log entirely for a run that had real,
        # already-live successes in it. Confirmed this bug for real: an
        # earlier version only wrote once at the end, and two manually-killed
        # runs (before this fix existed) lost all 32 of their successful
        # uploads from the log -- the videos were genuinely live on YouTube,
        # just untracked, until reconstructed by hand from raw output logs.
        log["updated_at"] = datetime.now(timezone.utc).isoformat()
        log_path.write_text(json.dumps(log, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        if ok:
            success_count += 1
        else:
            fail_count += 1
            print(f"  ✗ FAILED", file=sys.stderr)
            if "uploadLimitExceeded" in output:
                # YouTube's account-level daily upload count cap -- separate from API
                # quota (videos.insert costs 1 unit with its own 100/day bucket) and
                # unrelated to OAuth scope. Verified directly on a real 71-video batch:
                # hit after 7 uploads on an unverified channel, then again after 25
                # more on the SAME channel right after phone verification (~32/day
                # observed total) -- verification raises the cap, doesn't remove it.
                # It resets on its own (observed ~24h); retrying immediately just
                # reproduces the same failure for every remaining job. Stop here
                # instead of burning through the rest of the batch on a guaranteed
                # failure -- every job after this one would fail identically.
                remaining = [p["job_id"] for p in plan[index:]]
                remaining_path = output_base / "remaining_upload_jobs.json"
                remaining_path.write_text(json.dumps(remaining, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                playlist_note = f" and --playlist-id {playlist_id}" if playlist_id else ""
                print(
                    f"\nDetected YouTube's daily upload limit (uploadLimitExceeded) -- an "
                    f"account-level cap, not something retrying right now will fix. Stopping "
                    f"here rather than attempting the remaining {len(remaining)} job(s), which "
                    f"would all fail the same way.\n"
                    f"Saved the remaining job IDs to {remaining_path}. Once the cap has reset "
                    f"(observed ~24h), resume with --jobs set to those IDs{playlist_note} to "
                    f"keep adding to the same playlist.",
                    file=sys.stderr,
                )
                break

    print(f"\nPublish complete: {success_count} succeeded, {fail_count} failed. Log: {log_path}")
    return 0 if fail_count == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
