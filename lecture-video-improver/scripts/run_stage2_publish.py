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
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

SCRIPTS_DIR = Path(__file__).parent


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("manifest", type=Path, help="Path to the SAME job_manifest.json Stage 1 used")
    parser.add_argument("--jobs", help="Comma-separated job IDs to publish (default: all eligible)")
    parser.add_argument("--privacy", default="private", choices=["private", "unlisted", "public"],
                        help="Default: private, applied to every video in this run")
    parser.add_argument("--client-secrets", type=Path, default=Path("client_secret.json"))
    parser.add_argument("--token-file", type=Path, default=Path.home() / ".config" / "youtube_upload_token.json")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--confirmed-plan", type=Path, default=None,
                        help="Path to the publish_plan.json the user reviewed. Required with --execute; "
                             "its content must hash-match the freshly recomputed plan exactly.")
    return parser.parse_args()


def compute_title(lecture: str, topic: str, module: str | None) -> str:
    title = f"L{lecture}: {topic}"
    if module:
        title += f" - Module {module}"
    return title


def build_plan(manifest: dict, jobs_filter: set[str] | None) -> tuple[list[dict], list[str]]:
    output_base = Path(manifest.get("output_base", "output"))
    plan: list[dict] = []
    skipped: list[str] = []

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
        title = compute_title(str(lecture), str(topic), str(module) if module else None)
        plan.append({
            "job_id": job_id,
            "video": str(final_video),
            "lecture": str(lecture),
            "topic": str(topic),
            "module": str(module) if module else None,
            "title": title,
            "description": job.get("description", ""),
        })

    return plan, skipped


def plan_hash(plan: list[dict], privacy: str) -> str:
    canonical = json.dumps({"privacy": privacy, "plan": plan}, sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def main() -> int:
    args = parse_args()

    if not args.manifest.is_file():
        print(f"Manifest not found: {args.manifest}", file=sys.stderr)
        return 2
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))

    jobs_filter = {j.strip() for j in args.jobs.split(",")} if args.jobs else None
    plan, skipped = build_plan(manifest, jobs_filter)

    output_base = Path(manifest.get("output_base", "output"))
    plan_path = output_base / "publish_plan.json"
    plan_payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "privacy": args.privacy,
        "videos": plan,
        "skipped": skipped,
    }

    print(f"Publish plan ({len(plan)} video(s) eligible, {len(skipped)} skipped):\n")
    for item in plan:
        print(f"  {item['job_id']}: \"{item['title']}\"  <- {item['video']}")
    if skipped:
        print("\nSkipped:")
        for s in skipped:
            print(f"  {s}")
    print(f"\nPrivacy for this run: {args.privacy}")

    output_base.mkdir(parents=True, exist_ok=True)
    plan_path.write_text(json.dumps(plan_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"\nWrote {plan_path}")

    if not args.execute:
        print(
            "\nDry run only: nothing uploaded. Show the plan above (every title, verbatim) to the "
            f"user. Once confirmed, re-run with --execute --confirmed-plan {plan_path}"
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

    confirmed_hash = plan_hash(confirmed_payload.get("videos", []), confirmed_payload.get("privacy", ""))
    fresh_hash = plan_hash(plan, args.privacy)
    if confirmed_hash != fresh_hash:
        print(
            "\nThe confirmed plan does not match the freshly recomputed plan. Something changed "
            "since it was reviewed (a job's output was regenerated, a title field was edited, the "
            "privacy setting differs, or a different set of jobs is eligible now). This is a hard "
            "stop, not a formality -- go back, show the NEW plan to the user, and get it confirmed "
            "again rather than forcing the stale one through.",
            file=sys.stderr,
        )
        return 2

    print(f"\nConfirmed plan matches. Uploading {len(plan)} video(s)...\n")
    log_path = output_base / "publish_log.json"
    log = json.loads(log_path.read_text()) if log_path.is_file() else {"jobs": {}}

    success_count = 0
    fail_count = 0
    for item in plan:
        job_id = item["job_id"]
        print(f"─── {job_id}: \"{item['title']}\" ───")
        # Pass the SAME lecture/topic/module Stage 2 used to compute item["title"],
        # so upload_to_youtube.py's own internal recomputation reproduces the
        # identical string by construction -- its --confirmed-title gate then
        # naturally passes rather than needing to be worked around.
        cmd = [
            sys.executable, str(SCRIPTS_DIR / "upload_to_youtube.py"), item["video"],
            "--lecture", item["lecture"], "--topic", item["topic"],
            "--description", item.get("description", ""),
            "--privacy", args.privacy,
            "--client-secrets", str(args.client_secrets),
            "--token-file", str(args.token_file),
            "--execute", "--confirmed-title", item["title"],
        ]
        if item["module"]:
            cmd += ["--module", item["module"]]
        result = subprocess.run(cmd, capture_output=True, text=True)
        ok = result.returncode == 0
        output = (result.stdout + result.stderr).strip()
        for line in output.splitlines():
            print(f"  {line}")

        log["jobs"][job_id] = {
            "status": "published" if ok else "failed",
            "title": item["title"],
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        if ok:
            success_count += 1
        else:
            fail_count += 1
            print(f"  ✗ FAILED", file=sys.stderr)

    log["updated_at"] = datetime.now(timezone.utc).isoformat()
    log_path.write_text(json.dumps(log, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"\nPublish complete: {success_count} succeeded, {fail_count} failed. Log: {log_path}")
    return 0 if fail_count == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
