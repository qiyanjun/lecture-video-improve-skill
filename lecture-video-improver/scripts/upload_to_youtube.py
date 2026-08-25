#!/usr/bin/env python3
"""Upload a finished video to YouTube, with title formatted in the "L**"
lecture-numbering convention, and a hard confirmation gate before anything
actually publishes.

Title format (confirmed convention):
  Single-module lecture:  "L{lecture}: {topic}"
  Multi-module lecture:   "L{lecture}: {topic} - Module {module}"
  (matches the source material's own existing naming, e.g. the file
  "S0-L1-Introduction-Module1.mp4" -> "L1: Introduction - Module 1")

Two layers of protection against an accidental/unconfirmed upload, on top
of the dry-run-by-default pattern used everywhere else in this plugin:
  1. --execute is required to do anything beyond show the plan (as usual).
  2. --confirmed-title must be passed and must match the COMPUTED title
     EXACTLY, character for character. This forces whoever drives this
     script (the agent) to have actually shown the literal title string to
     the user and gotten it confirmed verbatim -- not just a generic
     "proceed?" yes. There is no flag to skip this.

Default privacy is "private" (confirmed default) -- only visible to the
uploading account. Never defaults to "public".

One-time setup (per Google account, not per video):
  1. Create a Google Cloud project, enable the "YouTube Data API v3".
  2. Create OAuth 2.0 credentials of type "Desktop app", download as JSON.
  3. First run of this script with --client-secrets pointing at that file
     opens a browser for one-time login + consent (scope:
     youtube.upload). The resulting token is cached at --token-file for
     all future uploads -- no browser needed after that.

Requires: pip install google-api-python-client google-auth-httplib2 google-auth-oauthlib

Usage:
  # Show the exact plan, no upload -- always safe to run
  python3 upload_to_youtube.py video.mp4 --lecture 1 --topic "Introduction"

  # Multi-module lecture
  python3 upload_to_youtube.py video.mp4 --lecture 1 --topic "Introduction" --module 2

  # After the user has confirmed the exact title shown above, upload for real
  python3 upload_to_youtube.py video.mp4 --lecture 1 --topic "Introduction" --module 2 \\
      --execute --confirmed-title "L1: Introduction - Module 2"
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def compute_title(lecture: str, topic: str, module: str | None) -> str:
    title = f"L{lecture}: {topic}"
    if module:
        title += f" - Module {module}"
    return title


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("video", type=Path, help="Video file to upload")
    parser.add_argument("--lecture", required=True, help="Lecture number, e.g. 1 (produces 'L1')")
    parser.add_argument("--topic", required=True, help="Lecture topic/title, e.g. 'Introduction'")
    parser.add_argument("--module", default=None, help="Module/part number within the lecture, if the lecture is split (e.g. 2)")
    parser.add_argument("--description", default="", help="Video description")
    parser.add_argument("--privacy", default="private", choices=["private", "unlisted", "public"],
                        help="Default: private (confirmed safe default -- never defaults to public)")
    parser.add_argument("--playlist-id", default=None, help="Optional YouTube playlist ID to add the video to after upload")
    parser.add_argument("--client-secrets", type=Path, default=Path("client_secret.json"),
                        help="OAuth2 client secrets JSON from Google Cloud Console (Desktop app type)")
    parser.add_argument("--token-file", type=Path, default=Path.home() / ".config" / "youtube_upload_token.json",
                        help="Where the cached OAuth token is stored/read (default: ~/.config/youtube_upload_token.json)")
    parser.add_argument("--execute", action="store_true", help="Actually upload (dry run by default)")
    parser.add_argument("--confirmed-title", default=None,
                        help="Must match the computed title EXACTLY. Required with --execute -- proves the "
                             "exact title was shown to and confirmed by the user before uploading, not just "
                             "a generic go-ahead.")
    return parser.parse_args()


def get_authenticated_service(client_secrets: Path, token_file: Path):
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build
    except ImportError:
        sys.exit(
            "Missing dependencies. Install with:\n"
            "  pip install google-api-python-client google-auth-httplib2 google-auth-oauthlib"
        )

    scopes = ["https://www.googleapis.com/auth/youtube.upload"]
    creds = None

    if token_file.is_file():
        creds = Credentials.from_authorized_user_file(str(token_file), scopes)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not client_secrets.is_file():
                sys.exit(
                    f"No cached token at {token_file} and no client secrets file at {client_secrets}. "
                    "One-time setup needed -- see this script's module docstring for the Google Cloud "
                    "Console steps, then re-run with --client-secrets pointing at the downloaded JSON. "
                    "This opens a browser for one-time login + consent."
                )
            flow = InstalledAppFlow.from_client_secrets_file(str(client_secrets), scopes)
            creds = flow.run_local_server(port=0)
        token_file.parent.mkdir(parents=True, exist_ok=True)
        token_file.write_text(creds.to_json())
        try:
            token_file.chmod(0o600)
        except OSError:
            pass

    return build("youtube", "v3", credentials=creds)


def upload_video(
    youtube, video_path: Path, title: str, description: str, privacy: str,
    playlist_id: str | None,
) -> str:
    from googleapiclient.http import MediaFileUpload

    body = {
        "snippet": {"title": title, "description": description},
        "status": {"privacyStatus": privacy},
    }
    media = MediaFileUpload(str(video_path), chunksize=1024 * 1024 * 8, resumable=True)
    request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)

    response = None
    while response is None:
        status, response = request.next_chunk()
        if status:
            print(f"  Uploaded {int(status.progress() * 100)}%...")

    video_id = response["id"]
    print(f"Uploaded: https://youtube.com/watch?v={video_id}  (privacy: {privacy})")

    if playlist_id:
        youtube.playlistItems().insert(
            part="snippet",
            body={"snippet": {"playlistId": playlist_id, "resourceId": {"kind": "youtube#video", "videoId": video_id}}},
        ).execute()
        print(f"Added to playlist: {playlist_id}")

    return video_id


def main() -> int:
    args = parse_args()

    if not args.video.is_file():
        print(f"Video not found: {args.video}", file=sys.stderr)
        return 2

    title = compute_title(args.lecture, args.topic, args.module)
    size_mb = args.video.stat().st_size / (1024 * 1024)

    print("Plan:")
    print(f"  File:        {args.video}  ({size_mb:.1f} MB)")
    print(f"  Title:       {title}")
    print(f"  Description: {args.description or '(none)'}")
    print(f"  Privacy:     {args.privacy}")
    if args.playlist_id:
        print(f"  Playlist:    {args.playlist_id}")

    if not args.execute:
        print("\nDry run only: nothing uploaded. Show this exact title to the user for confirmation, "
              "then re-run with --execute --confirmed-title \"<title above, verbatim>\".")
        return 0

    if args.confirmed_title != title:
        print(
            f"\n--confirmed-title does not match the computed title exactly.\n"
            f"  computed: {title!r}\n"
            f"  provided: {args.confirmed_title!r}\n"
            f"This is a hard gate, not a formality -- it must be the literal title the user confirmed. "
            f"Do not paper over a mismatch by just re-passing the computed value; if it doesn't match "
            f"what the user actually agreed to, go back and confirm the real one.",
            file=sys.stderr,
        )
        return 2

    youtube = get_authenticated_service(args.client_secrets, args.token_file)
    upload_video(youtube, args.video, title, args.description, args.privacy, args.playlist_id)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
