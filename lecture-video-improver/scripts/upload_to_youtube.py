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

The upload itself is resumable (MediaFileUpload resumable=True) AND retries
transient failures (HTTP 500/502/503/504, dropped connections, incomplete
reads) with exponential backoff, up to MAX_RETRIES -- without this, a single
network blip partway through a large, multi-minute upload would kill the
whole upload and lose all progress on it, rather than resuming from where it
left off. Non-transient errors (bad auth, quota exceeded, malformed request)
are NOT retried -- they fail immediately rather than wasting MAX_RETRIES
worth of backoff on an error that will never succeed.

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
import http.client
import json
import random
import socket
import sys
import time
from pathlib import Path

# Standard resumable-upload retry policy (matches Google's own documented
# sample for the YouTube Data API's resumable upload flow). Only genuinely
# transient failures go here -- a 4xx auth/permission/quota error means
# retrying is pointless, so those propagate immediately instead.
RETRIABLE_STATUS_CODES = (500, 502, 503, 504)
RETRIABLE_EXCEPTIONS = (
    socket.error,
    IOError,
    http.client.NotConnected,
    http.client.IncompleteRead,
    http.client.ImproperConnectionState,
    http.client.CannotSendRequest,
    http.client.CannotSendHeader,
    http.client.ResponseNotReady,
    http.client.BadStatusLine,
)
MAX_RETRIES = 10


MAX_TITLE_LEN = 100  # YouTube's hard limit


def compute_title(lecture: str, topic: str, module: str | None, prefix: str = "", style: str = "verbose") -> str:
    """style="verbose" (default, the documented convention): "L1: Topic" or
    "L1: Topic - Module 2". style="compact": "L01-M1-Topic" or "L01-Topic"
    -- shorter, and matches the job id convention used elsewhere in the
    manifest (e.g. "L01-M1"). Pick compact for a series where the verbose
    form runs long once a --title-prefix is added too."""
    if style == "compact":
        base = f"L{int(lecture):02d}" + (f"-M{module}" if module else "") + f"-{topic}"
    else:
        base = f"L{lecture}: {topic}"
        if module:
            base += f" - Module {module}"
    title = prefix + base
    if len(title) > MAX_TITLE_LEN:
        # Truncate the base (lecture-specific) part, not the prefix -- the
        # prefix is fixed series branding the caller explicitly asked for
        # on every video; leave room for a single ellipsis character.
        available = MAX_TITLE_LEN - len(prefix) - 1
        title = prefix + base[:available].rstrip() + "…"
    return title


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("video", type=Path, help="Video file to upload")
    parser.add_argument("--lecture", required=True, help="Lecture number, e.g. 1 (produces 'L1')")
    parser.add_argument("--topic", required=True, help="Lecture topic/title, e.g. 'Introduction'")
    parser.add_argument("--module", default=None, help="Module/part number within the lecture, if the lecture is split (e.g. 2)")
    parser.add_argument("--title-prefix", default="",
                         help="Fixed text prepended to every computed title, e.g. a course/series name. "
                              "Truncates the lecture-specific part (never the prefix) to stay within "
                              "YouTube's 100-char title limit if needed.")
    parser.add_argument("--title-style", default="verbose", choices=["verbose", "compact"],
                         help="verbose (default): 'L1: Topic - Module 2'. compact: 'L01-M2-Topic' "
                              "(shorter -- matches the manifest's own job id convention).")
    parser.add_argument("--description", default="", help="Video description")
    parser.add_argument("--privacy", default="private", choices=["private", "unlisted", "public"],
                        help="Default: private (confirmed safe default -- never defaults to public)")
    parser.add_argument("--made-for-kids", action="store_true",
                         help="Declare as directed at children under 13 (YouTube requires every video to "
                              "declare this one way or the other -- disables comments, notifications, and "
                              "personalized ads if set). Default: NOT made for kids -- the correct default "
                              "for this plugin's typical use case (course/lecture content), and the safer "
                              "default in general since accidentally defaulting to 'made for kids' silently "
                              "strips features rather than the reverse.")
    parser.add_argument("--playlist-id", default=None, help="Optional YouTube playlist ID to add the video to after upload")
    parser.add_argument("--category-id", default="27", help="YouTube video category ID (default: 27 = Education)")
    parser.add_argument("--tags", default=None, help="Comma-separated tags for discoverability, e.g. 'machine learning,UVA CS 4774'")
    parser.add_argument("--default-language", default="en", help="Default: en. ISO 639-1 language code for title/description/audio")
    parser.add_argument("--thumbnail", type=Path, default=None,
                        help="Optional path to a thumbnail image (e.g. Stage 1's work/assets/thumbnail.jpg) to set after upload")
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

    # youtube.upload alone doesn't cover playlist management (playlists().insert()
    # returns 403 insufficientPermissions under that narrower scope) -- needed for
    # run_stage2_publish.py's playlist creation/assignment. Still scoped to only
    # the authenticated account's own YouTube data, not broader Google account access.
    scopes = ["https://www.googleapis.com/auth/youtube"]
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
    playlist_id: str | None, category_id: str = "27", tags: list[str] | None = None,
    default_language: str = "en", thumbnail: Path | None = None, made_for_kids: bool = False,
) -> str:
    from googleapiclient.errors import HttpError
    from googleapiclient.http import MediaFileUpload

    snippet = {
        "title": title, "description": description, "categoryId": category_id,
        "defaultLanguage": default_language, "defaultAudioLanguage": default_language,
    }
    if tags:
        snippet["tags"] = tags
    body = {
        "snippet": snippet,
        "status": {"privacyStatus": privacy, "selfDeclaredMadeForKids": made_for_kids},
    }
    media = MediaFileUpload(str(video_path), chunksize=1024 * 1024 * 8, resumable=True)
    request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)

    response = None
    retry = 0
    while response is None:
        try:
            status, response = request.next_chunk()
            if status:
                print(f"  Uploaded {int(status.progress() * 100)}%...")
            retry = 0  # reset per successful chunk -- don't let blips early in a
                       # long upload eat into the retry budget for blips much later
        except HttpError as error:
            if error.resp.status not in RETRIABLE_STATUS_CODES:
                raise  # not transient (auth/quota/malformed request) -- fail fast
            retry += 1
            if retry > MAX_RETRIES:
                raise RuntimeError(
                    f"Upload of {video_path} failed after {MAX_RETRIES} retries "
                    f"(last error: HTTP {error.resp.status})"
                ) from error
            sleep_s = min(2 ** retry + random.random(), 60)
            print(f"  Transient error (HTTP {error.resp.status}), retrying in "
                  f"{sleep_s:.0f}s (attempt {retry}/{MAX_RETRIES})...", file=sys.stderr)
            time.sleep(sleep_s)
        except RETRIABLE_EXCEPTIONS as error:
            retry += 1
            if retry > MAX_RETRIES:
                raise RuntimeError(
                    f"Upload of {video_path} failed after {MAX_RETRIES} retries "
                    f"(last error: {error!r})"
                ) from error
            sleep_s = min(2 ** retry + random.random(), 60)
            print(f"  Transient network error ({error!r}), retrying in "
                  f"{sleep_s:.0f}s (attempt {retry}/{MAX_RETRIES})...", file=sys.stderr)
            time.sleep(sleep_s)

    video_id = response["id"]
    print(f"Uploaded: https://youtube.com/watch?v={video_id}  (privacy: {privacy})")

    if playlist_id:
        youtube.playlistItems().insert(
            part="snippet",
            body={"snippet": {"playlistId": playlist_id, "resourceId": {"kind": "youtube#video", "videoId": video_id}}},
        ).execute()
        print(f"Added to playlist: {playlist_id}")

    if thumbnail and thumbnail.is_file():
        # Non-fatal: custom thumbnails require a phone-verified YouTube channel
        # (a separate one-time manual step at youtube.com/verify, unrelated to
        # OAuth scope) -- HTTP 403 "doesn't have permissions to upload and set
        # custom video thumbnails" is the exact signature of an unverified
        # channel. The video itself (and playlist assignment) already
        # succeeded by this point; don't let an optional cosmetic step turn a
        # real success into a reported failure.
        try:
            youtube.thumbnails().set(videoId=video_id, media_body=MediaFileUpload(str(thumbnail))).execute()
            print(f"Thumbnail set: {thumbnail}")
        except HttpError as error:
            print(f"  Warning: thumbnail upload failed (video itself still uploaded fine): "
                  f"HTTP {error.resp.status}. If this says 'doesn't have permissions to upload "
                  f"and set custom video thumbnails', verify your channel's phone number at "
                  f"youtube.com/verify -- that's a YouTube channel-level restriction, not an "
                  f"OAuth scope issue.", file=sys.stderr)

    return video_id


def main() -> int:
    args = parse_args()

    if not args.video.is_file():
        print(f"Video not found: {args.video}", file=sys.stderr)
        return 2

    title = compute_title(args.lecture, args.topic, args.module, args.title_prefix, args.title_style)
    size_mb = args.video.stat().st_size / (1024 * 1024)

    tags = [t.strip() for t in args.tags.split(",")] if args.tags else None

    print("Plan:")
    print(f"  File:        {args.video}  ({size_mb:.1f} MB)")
    print(f"  Title:       {title}{'  (truncated to fit 100-char limit)' if title.endswith('…') else ''}")
    print(f"  Description: {args.description or '(none)'}")
    print(f"  Privacy:     {args.privacy}")
    print(f"  Made for kids: {args.made_for_kids}")
    print(f"  Category:    {args.category_id}")
    print(f"  Tags:        {', '.join(tags) if tags else '(none)'}")
    print(f"  Language:    {args.default_language}")
    if args.thumbnail:
        print(f"  Thumbnail:   {args.thumbnail}{'' if args.thumbnail.is_file() else '  (WARNING: file not found)'}")
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
    upload_video(
        youtube, args.video, title, args.description, args.privacy, args.playlist_id,
        category_id=args.category_id, tags=tags, default_language=args.default_language,
        thumbnail=args.thumbnail, made_for_kids=args.made_for_kids,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
