#!/usr/bin/env python3
"""Retry setting custom thumbnails for already-published videos.

Standalone companion to run_stage2_publish.py. YouTube's custom-thumbnail
upload has its OWN separate rate limit -- `uploadRateLimitExceeded` (HTTP
429) -- independent of, and slower to clear than, the daily video-upload
cap run_stage2_publish.py already handles. It's an undocumented, rolling
~24h-per-channel window (no exact published number; confirmed directly on
a real batch: a little over a dozen thumbnails set back-to-back during a
video-upload run was enough to trip it, and it stayed tripped a full ~24h
from when it was first hit, not a calendar-day reset like the video cap
seems to follow). A large batch will almost always need thumbnails set in
a separate pass like this one, sometimes spread across more than one day.

Reads <output_base>/publish_log.json (written by run_stage2_publish.py) for
each already-published job's video_id, and that job's own
work/assets/thumbnail.jpg for the image to set. Tracks per-job success in
<output_base>/thumbnail_log.json so re-runs skip already-set thumbnails
instead of re-spending rate-limit budget confirming something that already
worked.

Stops itself immediately on the first uploadRateLimitExceeded -- same
self-stopping principle as run_stage2_publish.py's daily-cap handling:
every remaining job would fail identically right now, so there's no point
burning through the rest of the list. Re-run later once the window clears.

Usage:
  # Dry run -- shows which jobs still need a thumbnail set, nothing changes
  python3 retry_thumbnails.py job_manifest.json

  # Execute for real
  python3 retry_thumbnails.py job_manifest.json --execute

  # Just specific jobs
  python3 retry_thumbnails.py job_manifest.json --jobs L14-M1,L14-M2 --execute
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

SCRIPTS_DIR = Path(__file__).parent
sys.path.insert(0, str(SCRIPTS_DIR))

from upload_to_youtube import get_authenticated_service  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("manifest", type=Path, help="Path to the SAME job_manifest.json Stage 1/2 used")
    parser.add_argument("--output-base", type=Path, default=None,
                         help="Override the manifest's own 'output_base' (matches run_stage2_publish.py)")
    parser.add_argument("--jobs", help="Comma-separated job IDs to retry (default: all pending)")
    parser.add_argument("--client-secrets", type=Path, default=Path("client_secret.json"))
    parser.add_argument("--token-file", type=Path, default=Path.home() / ".config" / "youtube_upload_token.json")
    parser.add_argument("--execute", action="store_true", help="Actually set thumbnails (dry run by default)")
    return parser.parse_args()


def load_json(path: Path, default: dict) -> dict:
    if path.is_file():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    return default


def main() -> int:
    args = parse_args()

    if not args.manifest.is_file():
        print(f"Manifest not found: {args.manifest}", file=sys.stderr)
        return 2
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    output_base = args.output_base or Path(manifest.get("output_base", "output"))

    publish_log_path = output_base / "publish_log.json"
    if not publish_log_path.is_file():
        print(f"No publish_log.json at {publish_log_path} -- run run_stage2_publish.py first.", file=sys.stderr)
        return 2
    publish_log = load_json(publish_log_path, {"jobs": {}})

    thumb_log_path = output_base / "thumbnail_log.json"
    thumb_log = load_json(thumb_log_path, {"jobs": {}})

    jobs_filter = {j.strip() for j in args.jobs.split(",")} if args.jobs else None

    candidates: list[tuple[str, str, Path]] = []
    skipped: list[str] = []
    for job_id, entry in publish_log.get("jobs", {}).items():
        if jobs_filter is not None and job_id not in jobs_filter:
            continue
        if entry.get("status") != "published" or not entry.get("video_id"):
            skipped.append(f"{job_id}: not published (or no recorded video_id) -- skipping")
            continue
        if thumb_log["jobs"].get(job_id, {}).get("status") == "set":
            continue  # already confirmed set on a previous run -- don't re-spend rate-limit budget
        thumb_path = output_base / job_id / "work" / "assets" / "thumbnail.jpg"
        if not thumb_path.is_file():
            skipped.append(f"{job_id}: no thumbnail.jpg at {thumb_path} -- skipping")
            continue
        candidates.append((job_id, entry["video_id"], thumb_path))

    print(f"Plan: {len(candidates)} thumbnail(s) pending ({len(skipped)} skipped, already-set jobs excluded automatically):\n")
    for job_id, video_id, thumb_path in candidates:
        print(f"  {job_id} -> https://youtube.com/watch?v={video_id}  <- {thumb_path}")
    if skipped:
        print("\nSkipped:")
        for s in skipped:
            print(f"  {s}")

    if not candidates:
        print("\nNothing to do.")
        return 0

    if not args.execute:
        print("\nDry run only: no thumbnails changed. Add --execute to actually set them.")
        return 0

    from googleapiclient.errors import HttpError
    from googleapiclient.http import MediaFileUpload

    youtube = get_authenticated_service(args.client_secrets, args.token_file)

    set_count = 0
    for job_id, video_id, thumb_path in candidates:
        try:
            youtube.thumbnails().set(videoId=video_id, media_body=MediaFileUpload(str(thumb_path))).execute()
            print(f"{job_id}: thumbnail set")
            thumb_log["jobs"][job_id] = {
                "status": "set", "video_id": video_id,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            # Write after EVERY attempt, success or failure, not just at the
            # end -- a killed process partway through would otherwise lose
            # track of thumbnails that genuinely succeeded (same bug fixed
            # in run_stage2_publish.py's publish_log.json for the same
            # reason).
            thumb_log_path.write_text(json.dumps(thumb_log, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            set_count += 1
        except HttpError as error:
            error_str = str(error)
            # Two DISTINCT self-stop conditions, both meaning "every remaining
            # thumbnail will fail identically right now, stop instead of
            # burning through the list": the thumbnail-specific rate limit,
            # and the general YouTube Data API quota (10,000 units/day,
            # shared pool, resets at midnight Pacific). Missed the quota case
            # on the first pass -- confirmed for real: it kept attempting
            # (and failing) every remaining thumbnail instead of stopping,
            # exactly the bug already fixed once for uploadLimitExceeded in
            # run_stage2_publish.py. Both must be handled the same way.
            stop_reasons = {
                "uploadRateLimitExceeded": (
                    "thumbnail rate limit hit again (HTTP 429 uploadRateLimitExceeded) -- an "
                    "undocumented rolling ~24h-per-channel window, not something retrying right "
                    "now will fix"
                ),
                "quotaExceeded": (
                    "YouTube Data API quota exhausted (quotaExceeded) -- the shared 10,000-unit/day "
                    "pool, resets at midnight Pacific"
                ),
            }
            matched_reason = next((r for r in stop_reasons if r in error_str), None)
            thumb_log["jobs"][job_id] = {
                "status": "rate_limited" if matched_reason else "failed",
                "video_id": video_id, "error": error_str[:500],
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            thumb_log_path.write_text(json.dumps(thumb_log, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            if matched_reason:
                remaining = len(candidates) - set_count - 1
                print(
                    f"\n{job_id}: {stop_reasons[matched_reason]}. Stopping here rather than "
                    f"attempting the remaining {remaining} thumbnail(s), which would fail the "
                    f"same way. Re-run this script later once it clears.",
                    file=sys.stderr,
                )
                print(f"\n{set_count}/{len(candidates)} thumbnails set this run.")
                return 0
            print(f"{job_id}: FAILED - {error}", file=sys.stderr)

    thumb_log_path.write_text(json.dumps(thumb_log, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"\n{set_count}/{len(candidates)} thumbnails set this run.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
