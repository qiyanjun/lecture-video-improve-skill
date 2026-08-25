# Lecture Video Improver

Two capabilities in one plugin: **English text improvement** for scripts, transcripts, and subtitle files, and a complete **batch video production pipeline** — transcript cleanup, TTS via Fish Audio or ElevenLabs, AI thumbnail generation, FFmpeg assembly, all orchestrated for N videos at once.

---

## Plugin structure

```
lecture-video-improver/
├── .claude-plugin/
│   └── plugin.json                       ← manifest (name, version)
├── README.md                             ← this file
├── commands/
│   ├── improve-script.md                 ← /improve-script slash command
│   └── improve-subtitles.md              ← /improve-subtitles slash command
├── skills/
│   ├── english-improvement/              ← Part 1: text-only English polishing
│   │   ├── SKILL.md
│   │   └── references/improvement-guidelines.md
│   └── build-video-voiceovers/           ← Part 2: full video production pipeline
│       ├── SKILL.md                      ← when to use, boundaries, required workflow
│       └── references/
│           ├── transcript-and-tts.md
│           └── assembly-and-batching.md
└── scripts/                              ← every script below is called by its number
    │                                        in "Step flow" below, or noted standalone
    ├── run_stage1_improve.py             ← Stage 1 ORCHESTRATOR -- runs steps 0-6 for N videos
    ├── resolve_youtube_source.py         ← step 0: download video / extract captions
    ├── detect_fillers.py                 ← step 0 (pause only): pre-flag filler candidates
    ├── chunk_transcript_with_timestamps.py  ← step 1: anchor cleaned script to exact timestamps
    ├── measure_transcript_diff.py        ← step 2: lexical + semantic diff review
    ├── generate_tts_fish.py              ← step 3: Fish Audio TTS
    ├── generate_tts.py                   ← step 3: ElevenLabs TTS (same interface)
    ├── generate_images.py                ← step 4: thumbnail + intro card (OpenAI)
    ├── sync_segments.py                  ← step 5: per-chunk trim/freeze-extend + assemble
    ├── qa_check.py                       ← step 6: post-assembly audit
    ├── upload_to_youtube.py              ← Stage 2: single-video YouTube upload
    ├── run_stage2_publish.py             ← Stage 2 ORCHESTRATOR: batch publish
    ├── chunk_transcript.py               ← standalone (B-roll/no-source-recording case only)
    ├── concat_audio.py                   ← standalone (B-roll/no-source-recording case only)
    └── trim_video.py                     ← standalone (B-roll/no-source-recording case only)
```

---


## Part 1 — English Improvement

Fix and polish English in any video-related text file.

### Commands

**`/improve-script [file]`** — Improves grammar, fluency, vocabulary, clarity, and tone in a script, outline, or plain-text transcript. Saves a corrected version and a changelog with before/after examples.

**`/improve-subtitles [file]`** — Improves English in `.srt` or `.vtt` subtitle files. All timing codes and sequence numbers are preserved exactly.

The **english-improvement** skill also loads automatically when you say things like "improve my English", "make this sound more natural", or "fix the grammar in my transcript".

---

## Part 2 — Batch Video Production Pipeline

Full end-to-end pipeline for producing professional narrated videos from raw recordings, split
into two independently-runnable stages with a human review checkpoint in between:

- **Stage 1 — Improve** (`run_stage1_improve.py`): raw video/transcript (local or YouTube) →
  local, English-improved, fully assembled video. Publishes nowhere.
- **Human review**: watch the finished local videos before going further.
- **Stage 2 — Publish** (`run_stage2_publish.py` for a batch, `upload_to_youtube.py` for one
  video): reviewed local videos → YouTube, with "L**" title formatting and a hard,
  exact-match confirmation gate before anything uploads.

Run all of Stage 1 for a whole batch without touching a real channel; only run Stage 2 once
you've actually watched the results.

### Skill trigger

The **build-video-voiceovers** skill loads when you say things like:
- "Build a voiceover for this video using Fish Audio"
- "Batch-produce 70 narrated videos from my transcripts"
- "Clean my transcript and generate TTS audio"
- "Generate thumbnails and intro cards for my videos"
- "Trim filler sections from my talking-head recording"
- "Upload these finished videos to YouTube"

### Setup — API keys, voice ID, and job inputs

**1. API keys (environment variables).** Set whichever your job actually needs:

| Variable | Required for | Notes |
|----------|---------------|-------|
| `FISH_API_KEY` | Fish Audio TTS (`generate_tts_fish.py`, `prepend_welcome.py`) | Get one at [fish.audio/app/api-keys](https://fish.audio/app/api-keys) |
| `ELEVENLABS_API_KEY` | ElevenLabs TTS (`generate_tts.py`) | |
| `OPENAI_API_KEY` | `ai`-style thumbnail/intro cards, and semantic-similarity scoring in the diff-review step | Optional — the default `basic` card style and lexical-only diff review both work without it |
| `FISH_AUDIO_VOICE_ID` / `ELEVENLABS_VOICE_ID` | — | Optional shortcut: sets a default voice without putting it in the manifest. A manifest/job `voice_id` still takes precedence when set — see below |

None of the video-assembly steps (chunking, `sync_segments.py`, `ffmpeg`) need a key at all — only TTS, `ai`-style images, and semantic diff scoring call out to a paid API.

**2. Voice ID.** Never left to guess or default — `generate_tts_fish.py --execute` hard-refuses to run with no voice specified (pass `--allow-default-voice` to explicitly opt into the platform default instead). Three ways to land on one, in order of how you'd typically reach for them:

- **You already have one** (from a prior job, or the user names it) — set it directly, no lookup needed:
  ```json
  { "voice_id": "a43acc3187284bc5a4fef352a79bbcd8" }
  ```
  in the manifest (applies to every job), or per-job to override just one job, or via the `FISH_AUDIO_VOICE_ID`/`ELEVENLABS_VOICE_ID` env var.
- **You want an existing Fish Audio library voice by name** (e.g. "use the voice Sarah") — search rather than guess, since common names have many entries:
  ```bash
  curl -s "https://api.fish.audio/model?title=Sarah&page_size=10" \
    -H "Authorization: Bearer $FISH_API_KEY"
  ```
  Pick by `like_count` (the canonical voice is usually far ahead of same-named alternatives) and check `languages`/`tags` match the job. See **Voice selection** in `SKILL.md` for the full disambiguation procedure.
- **You want a clone of a specific speaker** — clone from a clean 60–90s representative sample (with the source's consent before uploading), generate a short test phrase, and confirm before committing to a full job. Full procedure in `SKILL.md`'s **Voice selection** section.

**3. Job inputs.** Each job in the manifest needs a source video and a source transcript — either can be a local file or resolved automatically from a YouTube URL:

| Field | Purpose |
|-------|---------|
| `source_video` *(local path)* **or** `source_video_url` *(YouTube)* | The recording to narrate over. A YouTube URL is downloaded once and cached at `work/resolved_source.mp4` — re-runs reuse it instead of re-downloading |
| `source_transcript` *(local word-level JSON)* **or** `transcript_youtube_url` *(YouTube captions)* — falls back to `source_video_url` if the video and transcript are the same recording | The word-level, timestamped ASR transcript every chunk anchors against. Required — a job with neither fails outright rather than silently assembling un-synced |
| `cleaned_script` *(path, optional)* | An `{"anchor": "...", "clean": "..."}` pair list — the polished narration text, anchored back to exact spans in the raw transcript. If omitted, the script looks for `<output_base>/<job_id>/cleaned_script.json`; if that's missing too, the job **pauses** with `needs_cleanup` and tells you exactly where to save it. This is the one step that stays a human/agent judgment call — never auto-generated |

Once a job resolves cleanly, re-running the same command picks up from whatever already exists on disk (downloaded video, extracted transcript, cleaned script) rather than redoing expensive/manual work — see the full manifest schema in `run_stage1_improve.py`'s module docstring for every optional field (`max_change_pct`, `min_semantic_similarity`, `welcome_message`, card branding, etc.).

### Pipeline overview

Two stages, run independently, with a human review checkpoint between them.
Stage 1 never publishes anywhere; Stage 2 never runs unless you invoke it.

### Step flow (Stage 1 — `run_stage1_improve.py`, one job)

Each numbered step below is exactly what prints as `Step N/6` when the
orchestrator runs. Steps 0–2 are gates: any of them can stop the job before
a cent gets spent on TTS or images.

| # | Step | Script(s) | What it does | Can it stop the job? |
|---|------|-----------|---------------|----------------------|
| 0 | Resolve inputs | `resolve_youtube_source.py` | Download video from YouTube if not local; extract the word-level source transcript from captions if not already provided | **Yes, twice.** Fails outright with no source transcript at all (nothing to sync against). Pauses with `needs_cleanup` if a source transcript exists but no `cleaned_script.json` does — also auto-runs `detect_fillers.py` here and folds candidate counts into the pause message |
| 1 | Chunk | `chunk_transcript_with_timestamps.py` | Anchor-matches each `{anchor, clean}` pair in the cleaned script to its exact `[start, end]` span in the source transcript | **Yes.** Fails if any anchor doesn't literally appear in the transcript (lists every failure) |
| 2 | Diff review | `measure_transcript_diff.py` | Lexical word-diff % vs. the raw transcript (always). Semantic similarity via OpenAI embeddings, if `OPENAI_API_KEY` is set (always, when available) | **Optionally.** `max_change_pct` / `min_semantic_similarity` in the manifest turn either into a hard failure; without them, both are reported but non-blocking |
| 3 | TTS | `generate_tts_fish.py` or `generate_tts.py` | Dry-run cost estimate for every job first; real generation only with `--execute`. Each clip loudness-normalized to −14 LUFS | **Yes.** Hard-requires a voice ID unless `--allow-default-voice` is passed explicitly |
| 4 | Images (optional) | `generate_images.py` | Thumbnail + 3-second intro card. **`basic` style (default): flat/gradient background, no cost, deterministic — set the brand once at the manifest level (`card_bg_color`, `card_accent_color`, `card_eyebrow`, ...) and every job in the series shares it.** `ai` style (opt-in): OpenAI `gpt-image-1.5` background from `--theme`, real cost, varies per call. Both generated at the source video's actual resolution | No — failure here only warns, the video still assembles without a card |
| 5 | Assemble | `sync_segments.py`, `prepend_welcome.py` | Per chunk: trim the video span if the TTS clip ran short, freeze-extend the last frame if it ran long, mux, concatenate. Output resolution matches the source video's own native resolution (no forced up/downscale). Then prepend the intro card (or copy through if none). If `welcome_message` is set in the manifest, also prepend a short spoken welcome/branding clip (same TTS voice, over a still of the video's own first frame) ahead of the intro card, trimming the card's fixed silent hold so there's no dead air afterward | **Yes**, on the core assembly (`ffmpeg`/`ffprobe` error on any chunk). The optional welcome-message pass only warns on failure — the intro-card final video is still valid without it |
| 6 | QA + log | `qa_check.py` | Audits the finished job (chunk bounds, loudness consistency, boundary silences, stream sanity), writes status to `batch_log.json` | No — QA failures are logged (`qa_passed: false`) but don't change the job's completed/failed status |

Batch entry point: `run_stage1_improve.py job_manifest.json [--execute]` runs
steps 0–6 for every job in the manifest, tracking `completed` /
`needs_cleanup` / `failed` per job with distinct process exit codes (`0` /
`3` / `1`) so a wrapper script can tell "some jobs need a human" apart from
"something actually broke."

```
── Human review: watch final/<id>_final.mp4 before proceeding ──
```

### Step flow (Stage 2 — publish, only after review)

| Script | Scope | What it does |
|--------|-------|---------------|
| `upload_to_youtube.py` | one video | Computes the "L**" title, requires `--confirmed-title` to match it character-for-character before `--execute` uploads anything. Defaults to `private`. |
| `run_stage2_publish.py` | whole batch | Scans `output_base` for completed Stage 1 jobs, writes one `publish_plan.json` covering all of them, requires `--confirmed-plan` to content-hash-match the freshly recomputed plan before uploading any of them. |

### Bundled scripts

| Script | Stage | Purpose | Key env var |
|--------|-------|---------|-------------|
| `chunk_transcript.py` | 1 | Sentence-safe chunking (1,500–2,500 chars), no source-timestamp mapping. Not used by `run_stage1_improve.py` — standalone tool for the B-roll/no-source-recording case. | — |
| `detect_fillers.py` | 1 | **New in 0.10.0.** Pattern-matches candidate um/uh, stutter repeats, and discourse fillers (like/you know/i mean/sort of/kind of) with context. Runs automatically at the `needs_cleanup` pause. Finds candidates only — classifying which discourse fillers are genuinely disposable vs. legitimate usage requires understanding meaning, so that stays a judgment call. | — |
| `chunk_transcript_with_timestamps.py` | 1 | **New in 0.2.0; what `run_stage1_improve.py` uses by default since 0.9.0.** Anchor-based chunking that ties each chunk to an exact span in the ORIGINAL source video. Required input for `sync_segments.py`. | — |
| `measure_transcript_diff.py` | 1 | **New in 0.2.0; runs automatically as `run_stage1_improve.py`'s Step 2 since 0.11.0.** Lexical word-diff % vs. the raw transcript (`--max-change-pct` to hard-fail on it), persisted to `work/diff_report.json`. **0.12.0: also scores semantic similarity via OpenAI embeddings** when `OPENAI_API_KEY` is set (`--min-semantic-similarity` / `--semantic`) — catches meaning drift a lexical diff alone can miss. | `OPENAI_API_KEY` (only for `--semantic`) |
| `generate_tts_fish.py` | 1 | Fish Audio TTS (dry run by default). **0.2.0: now loudness-normalizes every part to -14 LUFS before saving** (`--no-normalize` to opt out). | `FISH_API_KEY` |
| `generate_tts.py` | 1 | ElevenLabs TTS (dry run by default). Same loudness-normalization fix as above. | `ELEVENLABS_API_KEY` |
| `concat_audio.py` | 1 | Concatenate parts into master WAV/M4A/MP3. Not used by `run_stage1_improve.py` — standalone tool for the B-roll/no-source-recording case. | — |
| `trim_video.py` | 1 | Cut filler segments from source video (FFmpeg) — for narration-over-B-roll jobs where video and audio aren't chunk-synced | — |
| `sync_segments.py` | 1 | **New in 0.2.0; what `run_stage1_improve.py` uses to assemble by default since 0.9.0.** For narration REPLACING an existing recording's audio: trims or freeze-extends each chunk's video segment to exactly match that chunk's TTS duration, so sync never silently drifts mid-video. **0.14.0: output width now matches the source video's own native width by default** (`--target-width` to force a specific width instead) — previously hardcoded to always scale to 1920px, which silently upscaled any lower-resolution source. | — |
| `prepend_welcome.py` | 1 | **New in 0.15.0.** Optional, standalone or run automatically as part of `run_stage1_improve.py`'s Step 5 when `welcome_message` is set in the manifest. Prepends a short spoken welcome/branding clip in the job's own TTS voice, over a still of the video's own first frame, ahead of the intro card — then trims the intro card's fixed 3s silent hold so the spoken welcome flows straight into the content with no dead air. Uses the concat FILTER (not the demuxer) for the same reason `prepend_intro_card()` does — see below. Fish Audio only. Dry-run by default. | `FISH_API_KEY` |
| `generate_images.py` | 1 | Thumbnail + intro card. **0.2.0: fixed `response_format`** (deprecated/removed param, current gpt-image models always return base64). **0.13.0: added `--target-size`**, so `run_stage1_improve.py` can generate both images at the source video's actual resolution instead of a fixed 1536×864 that then needed scaling. **0.14.0: added `basic` card style (now the default)** — flat/gradient background rendered locally with Pillow, no API call, no cost, deterministic; `--eyebrow`/`--bg-color`/`--bg-color2`/`--accent-color`/`--text-color` define a fixed brand for a whole series. Long titles wrap across up to 2 lines and shrink to fit rather than clipping. The old AI-background behavior is still available as `--card-style ai`. | `OPENAI_API_KEY` (only for `ai` style) |
| `qa_check.py` | 1 | Post-assembly audit, runs automatically after every job in a batch: chunk bounds/sentence endings, audio-part duration plausibility, boundary-join silences, loudness consistency across parts, final video/audio stream sanity. Recognizes both the SYNC path's `work/synced.mp4` and the older `work/master_narration.wav` as valid audio references. Writes `qa_report.json`; failures are logged, not blocking. | — |
| `resolve_youtube_source.py` | 1 | **New in 0.5.0.** Download a video from a YouTube URL, and/or extract+convert a YouTube caption track into the same word-level JSON schema used elsewhere, when the video or transcript isn't already a local file. Flags auto vs. manual caption tracks (manual ones are often pre-cleaned) and sanity-checks duration against a local video when the two come from different sources. | — (yt-dlp only, no API key) |
| `run_stage1_improve.py` | 1 | **Stage 1 orchestrator, renamed from `run_batch.py` in 0.8.0.** Runs steps 0–6 for every job: resolve inputs → chunk (timestamp-anchored) → diff review (lexical + semantic) → TTS → images → assemble via `sync_segments.py` → QA. Pauses with status `needs_cleanup` when a transcript needs agent/human English-improvement (never fakes that step; auto-runs `detect_fillers.py` there since 0.10.0). **Since 0.9.0, requires a word-level source transcript** (given, or resolved from YouTube captions) — fails outright rather than silently degrading to an un-synced assembly if none is available. See "Step flow" above for the full per-step breakdown. Local output only — never uploads. | all of Stage 1 |
| `upload_to_youtube.py` | 2 | **New in 0.7.0.** Uploads one finished video to YouTube with "L**" lecture-numbering title formatting (`L1: Topic` or `L1: Topic - Module 2`, or `L01-M2-Topic` with `--title-style compact`). Hard-fails unless `--confirmed-title` matches the computed title exactly. Defaults to `private` visibility. **0.16.0: added retry/backoff on transient upload failures** (resumable upload was already in place but had no retry on dropped connections/5xx -- see below), **category/tags/language/thumbnail/`--title-prefix`**, and **the OAuth scope now requests full `youtube` manage** (was `youtube.upload`-only, which doesn't cover playlists -- see below). | OAuth2 (Google Cloud project + one-time browser consent) |
| `run_stage2_publish.py` | 2 | **New in 0.8.0.** Batch version of `upload_to_youtube.py`'s confirmation gate: one dry-run computes every video's title (+ auto-generated description, voice, thumbnail) across the whole batch into `publish_plan.json`, one user confirmation covers all of them, then `--execute --confirmed-plan` re-verifies the plan is still byte-identical (via content hash over both the per-video plan AND run-level settings) before uploading anything. **0.16.0: added playlist creation/assignment** (`--playlist-title`/`--playlist-id`), **auto-generated per-video descriptions** derived from each job's own `cleaned_script.json` plus the actual TTS voice used (read from `tts_manifest.json`, not assumed), and **`--output-base`** to publish from a different generated-output directory without editing the manifest. | same as `upload_to_youtube.py` |

### Why the 0.16.1 changes exist

- **`run_stage2_publish.py` now detects YouTube's daily upload cap and stops itself** instead of burning through the rest of a batch on guaranteed-identical failures. Discovered on a real 71-video batch run: an unverified channel hit `HttpError 400 uploadLimitExceeded` after only 7 uploads; phone-verifying the channel raised that to ~32/day, then the SAME error recurred -- confirming it's a real account-standing cap that verification raises but doesn't remove, distinct from both the per-request API quota and OAuth scope (see the module docstring for the full writeup). Previously every remaining job in the batch would attempt and fail identically -- had to be killed by hand, twice, on the real run. Now the first `uploadLimitExceeded` failure stops the loop immediately, saves the untried job IDs to `<output_base>/remaining_upload_jobs.json`, and tells you to resume with `--jobs` once the cap resets (observed ~24h).
- **Documented in the module docstring** so this is known going in, not discovered mid-batch: budget multiple days for very large (70+) batches even on a verified channel.

### Why the 0.16.0 changes exist

- **New: rich metadata + playlist support in `run_stage2_publish.py`.** Previously each video published with just a title and an empty description. Now:
  - **Description auto-generated from the job's own `cleaned_script.json`** -- explicit "Lecture N, Module M of K" line (module count computed from the manifest, not hand-maintained), then a genuine 1-3 sentence summary extracted from the actual polished narration (sentence-boundary-aware, never fabricated or LLM-paraphrased), then which TTS voice narrated it, then the series branding line. A job's own `description` field in the manifest still overrides this if set.
  - **Narration voice identified from ground truth, not assumption.** `read_voice_id()` reads each job's own `work/audio_parts/tts_manifest.json` (the file `generate_tts_fish.py` actually wrote), not the manifest's top-level `voice_id` (which only reflects future runs) or folder naming. `describe_voice()` then calls Fish Audio's `/model/{id}` endpoint and distinguishes a cloned voice (`source: "api"`) from a public library voice (`source: "voice_design"`) -- e.g. `a cloned voice ("my-clone-v4")` vs. `Fish Audio's "Sarah" voice"`. Never hardcodes specific voice IDs, so it works for any user's voices.
  - **Playlist creation and auto-assignment** (`--playlist-title` to create new, `--playlist-id` to reuse an existing one, `--no-playlist` to skip). Videos are added in manifest order, so the playlist plays back as a coherent series.
  - **Category, tags, language, and thumbnail** (`--category-id`, default `27`=Education; `--tags`; `--default-language`; auto-uses each job's `work/assets/thumbnail.jpg` unless `--no-thumbnail`) -- previously none of these were set at all, so videos published into whatever generic default YouTube picked.
  - **`--title-prefix`** prepends fixed text (e.g. a course name) to every title, truncating only the lecture-specific part (never the prefix) to stay within YouTube's hard 100-character title limit -- flagged per-video in the dry-run plan so a truncation is never a surprise.
  - **`--title-style compact`** is a shorter alternative title format (`L01-M2-Topic`, matching the manifest's own job-id convention) alongside the existing default `verbose` style (`L1: Topic - Module 2`, the originally documented convention) -- opt-in, so nothing changes for existing users relying on the documented default.
  - **`--output-base`** lets you publish from a different generated-output directory than the one in the manifest (e.g. comparing two voice runs) without editing the manifest file.
- **Two real bugs found and fixed while building this, both non-obvious:**
  1. **A successful upload reported as failed.** `youtube.thumbnails().set()` isn't covered by the `youtube.upload` OAuth scope at all (fixed separately, see below) -- but even after fixing that, a channel that isn't phone-verified gets HTTP 403 "doesn't have permissions to upload and set custom video thumbnails" from YouTube, a channel-level restriction unrelated to OAuth scope. The thumbnail call wasn't wrapped in its own error handling, so this exception propagated up and crashed the whole `upload_to_youtube.py` process -- even though the video itself had already uploaded successfully and had already been added to the playlist. Verified directly on a real run: the traceback showed `Uploaded: https://youtube.com/watch?v=...` and `Added to playlist: ...` printed successfully immediately before the crash. Fixed by making the thumbnail step non-fatal (warn and continue), matching the same "additive, optional" pattern already used for `prepend_welcome.py` in Stage 1 -- a cosmetic extra shouldn't turn a real success into a reported failure.
  2. **`youtube.upload` scope doesn't cover playlist management.** `playlists().insert()` returns HTTP 403 "insufficient authentication scopes" under the narrower scope this plugin previously requested. There's no scope that covers both upload and playlists except the broader `https://www.googleapis.com/auth/youtube` (full manage of the account's own YouTube data). Changed the requested scope; existing cached tokens need one re-consent (delete the cached token file, next run re-triggers the browser flow) since a token's scope doesn't retroactively upgrade.
- **The confirmed-plan hash now covers run-level settings, not just per-video titles** (`plan_hash()` takes a `settings` dict -- privacy, category, tags, language, playlist config, title prefix/style -- instead of just `privacy`) -- changing any of these after a plan was shown correctly invalidates the old confirmation, consistent with the existing "anything changed since review means stop and reconfirm" design.

### Why the 0.15.3 changes exist

- **Retry/backoff on transient upload failures in `upload_to_youtube.py`.** The upload was already resumable (`MediaFileUpload(resumable=True)`), but the chunk loop had no exception handling at all -- a single dropped connection or HTTP 500/502/503/504 partway through a large, multi-minute upload killed the whole upload and lost all its progress, rather than resuming from where it left off (the entire point of a resumable upload). Added retry with exponential backoff (`min(2**retry + jitter, 60s)`, up to `MAX_RETRIES=10`) on the standard set of transient failures -- matching Google's own documented pattern for this API. The retry counter resets after each successful chunk, so isolated blips spread across a long upload don't exhaust the budget for one bad patch of network. Non-transient errors (bad auth, quota exceeded, malformed request -- any HTTP status outside 500/502/503/504) are NOT retried; they fail immediately, since retrying a permanent error just wastes up to several minutes of backoff for nothing. Verified with 4 isolated tests against mocked API objects: retries-then-succeeds, fails-fast on a non-retriable 403, raises clearly after exceeding `MAX_RETRIES`, and confirms the per-chunk counter reset (many isolated single blips across a long upload don't accumulate toward the cap the way consecutive failures do).
- **Corrected a real quota misconception surfaced by this investigation**: it's commonly believed `videos.insert` costs 1600 quota units against the shared 10,000-unit daily pool (implying ~6 uploads/day without a quota increase). Checked Google's current official docs directly (two independent pages) -- that's outdated. `videos.insert` currently costs 1 unit and has its own separate 100-calls/day bucket, entirely independent of the shared pool. A 70-video batch fits in a single day via the API with no quota increase needed; the real constraint at that scale is bandwidth/transfer time, not quota, which is what this retry fix actually addresses.

### Why the 0.15.2 changes exist

- **Renamed the plugin from `english-video-improver` to `lecture-video-improver`** ahead of open-sourcing it, and moved it into a `lecture-video-improver/` subfolder of its own public repo (root keeps just `README.md`/`LICENSE`/`.gitignore`).
- **Removed institution-specific branding from the code defaults.** `generate_images.py`'s `basic`-style card defaulted its accent color to `#E57200` — UVA's actual institutional orange — unlabeled, for anyone using the plugin without overriding it. Changed the default to a neutral teal (`#2DD4BF`) not tied to any specific institution; still fully overridable via `card_accent_color`/`--accent-color`. Found during a pre-publish review, not reported as a bug — the plugin had only ever been run against one real course before this.
- **Genericized illustrative examples** in `run_stage1_improve.py`, `prepend_welcome.py`, and `SKILL.md` that used real UVA/CS-4774 wording (e.g. `"card_eyebrow": "UVA CS 4774: Machine Learning"`, `"Welcome to 4774, the Machine Learning course at the University of Virginia..."`) — swapped for course-agnostic placeholders so the docs don't read as tied to one specific school or course.
- **Pre-publish review found no secrets, credentials, or personal paths** in any script, doc, or config — `upload_to_youtube.py`'s `--client-secrets`/`--token-file` are just CLI defaults pointing at filenames the user supplies themselves, never anything embedded.

### Why the 0.15.1 changes exist

- **Docs-only: documented voice ID setup, including a real gap found in practice.** Testing a Fish Audio library voice by name ("use the voice Sarah") this session exposed that `SKILL.md`'s **Voice selection** only covered "ask the user directly" and "clone from a sample" — nothing for picking an *existing* library voice, even though Fish Audio has many public voices sharing common names (searching "Sarah" returns 10+ results, the top one with 5871 likes vs. the next at 20). Added library search (`GET /model?title=...`, disambiguate by `like_count`/`languages`/`tags`) as a third path in `SKILL.md`.
- **New "Setup" section in the README** consolidating API keys, the three voice-ID paths (reuse/search/clone), and job input requirements (`source_video`/`source_video_url`, `source_transcript`/`transcript_youtube_url`, `cleaned_script`) in one place — previously scattered across table footnotes with no single "how do I configure a job" entry point.
- **Fixed the `job_manifest.json` example in "Batch workflow"**, which had drifted from the real schema: it showed a `cleaned_transcript` plain-text field that `run_stage1_improve.py` doesn't read at all (the actual field is `cleaned_script`, an `{anchor, clean}` pair list) and was missing `welcome_message`, `lecture`/`topic`, and `source_video_url`. Same class of doc-drift as 0.12.1's fix, caught the same way — noticed while writing docs for an unrelated feature, not from a bug report.

### Why the 0.15.0 changes exist

- **New: optional spoken welcome/branding clip (`prepend_welcome.py`), formalized after proving it out on a real 71-video batch.** Set `welcome_message` in the manifest (a template string, e.g. `"Welcome to the Machine Learning course at the University of Virginia. This is Lecture {lecture}."`, with `{lecture}`/`{topic}`/`{title}` substitution and a per-job override/opt-out like `max_change_pct`) and `run_stage1_improve.py`'s Step 5 generates it in the job's own TTS voice, prepends it ahead of the intro card, and trims the card's redundant silent hold. Additive and optional — a failure only warns, never fails the job.
- **Two real bugs found and fixed while building it, both non-obvious enough to record:**
  1. **Non-monotonic DTS from stream-copy concat.** A naive `ffmpeg -f concat -c copy` to prepend a fresh clip onto an already-assembled final video is fast but silently wrong when the target's own internal audio timestamps aren't clean (which real Stage-1 output isn't guaranteed to be — it's itself built from many individually trimmed/freeze-extended chunk segments). Verified directly on a real batch: stream-copy concat produced a reported duration of 44730s for a 572s video, with only a buried, easy-to-miss "Non-monotonic DTS" warning in stderr — not a hard failure. Fixed the same way `prepend_intro_card()` already does: the concat FILTER (decodes and re-times both inputs) instead of the demuxer. `prepend_welcome.py` follows that established pattern rather than reintroducing the bug.
  2. **Wrong trim-offset math, caught by not trusting a clean exit code.** An early draft computed the redundant-silence cut point as `welcome_dur + trim_seconds`, copied from a separate batch script (`batch_trim_silence.py`) that trims silence out of an ALREADY-combined video, where the redundant hold does sit at that offset in the combined timeline. `prepend_welcome.py` instead trims the RAW target's own leading hold, which sits at offset `trim_seconds` from the start of ITS OWN timeline — not offset by the welcome clip's duration, since the welcome clip is a separate input concatenated in front, not part of the same timeline yet. The bug produced no ffmpeg error and no decode error — just a silently wrong duration, off by exactly the welcome clip's length (309.8s reported vs. 313.0s expected). Caught by independently re-verifying the output duration with a fresh `ffprobe` call rather than trusting the script's own "success" print statement, then isolating each filter branch to confirm ffmpeg had executed the graph exactly as written (the bug was in the offset arithmetic, not ffmpeg's behavior). Fixed by changing the second input's trim-start to just `trim_seconds`. Re-validated end-to-end against a genuinely reconstructed "virgin" video (real, untouched 3.08s silent hold, confirmed via `ffmpeg silencedetect` before AND after) — after the fix, only natural sub-0.3s speech pauses remain at the transition, no residual dead air, 0 decode errors, duration matching exactly.

### Why the 0.14.4 changes exist

- **Unusual provenance, documented honestly**: this fix (fused multi-word tokens in source transcripts -- see below) was found already applied in the working copy partway through the 71-video lecture series batch, made by something other than my own normal edit-and-verify process -- most likely an unintended side effect of a fork dispatched for an unrelated, narrowly-scoped task (writing one video's `cleaned_script.json`) reaching beyond that scope, since forks inherit full conversation context and can act on patterns they observe in it. The manifest's identity fields (`description`, plus fabricated `author`/`keywords` I never wrote) were also overwritten with generic, incomplete content and have been restored. I did not author the code change, but I did independently verify it before deciding to keep it rather than reverting: read the full diff, confirmed the logic is correct, and ran it against an already-verified-good job's transcript (`L01-M1`) to confirm byte-identical output (108 chunks, same spans) -- no regression.
- **The fix itself, once verified**: several source transcripts in this series contain word entries with an embedded space -- one ASR "word" whose text is literally `"i mean"` or `"really really"`, not two separate entries. An anchor written the natural way splits into separate tokens that can never match a transcript where they're fused into one, and because matching is sequential-forward-from-the-previous-anchor, one such mismatch derails every anchor after it. Several fork-written cleanup passes had already hit this independently and manually shortened their anchors to dodge it -- a per-occurrence workaround, not a fix. This change splits every source word on whitespace before matching, so fused tokens transparently become multiple match-tokens, with a `token_to_word_idx` mapping tracking each match-token back to its real word entry for timestamp lookup.
- All fork-dispatch prompts going forward now explicitly state they must not modify any file outside their assigned job's output directory, to prevent this class of scope violation recurring.

### Why the 0.14.3 changes exist

- **Renumbered from 0.14.2**: the version bumped and reinstalled for the log-locking fix before the locking gap itself was actually caught (0.14.2's `update_log_entry()` re-read-before-write without any lock, which the very next concurrency test proved insufficient -- see below). 0.14.3 is the real fix (`fcntl.flock` + per-PID temp filenames), verified with two more concurrent 3-job runs after adding the lock, both clean.

### Why the 0.14.2 changes exist

- **Fixed a real lost-update race condition in `batch_log.json` when running multiple jobs concurrently -- took two attempts to actually close it.** Found running the 71-video lecture series pilot batch: `main()` loaded the log once at start into an in-memory dict, and every per-job status write saved that WHOLE dict back to disk. When several `run_stage1_improve.py` invocations run concurrently against the same `output_base` (exactly what a real batch needs for throughput), each held its own stale snapshot -- whichever process finished last overwrote the file with its snapshot, silently reverting every OTHER job's logged status back to whatever it was when THAT process started. Caught directly: three pilot jobs run concurrently, the one that finished last (`L12-M1`) reverted the other two (`L01-M2`, `L09-M2`) from `completed` back to `needs_cleanup` -- their actual output files were untouched, only the status tracking was wrong. First attempt (`update_log_entry()` re-reading the log fresh before each write, no locking) shrank the race window but didn't close it: re-testing with 3 fresh concurrent jobs, 2 of 3 log entries still went missing, and a second issue surfaced -- the "atomic write" temp file used a fixed name (`batch_log.json.part`) shared across processes, so one process's `os.replace()` could find the file already consumed by another and crash with `FileNotFoundError`. Real fix: `fcntl.flock()` on a dedicated lock file around the whole read-modify-write-rename sequence, plus a per-PID temp filename so concurrent writes can never collide even outside the lock.
- Repaired the two corrupted log entries from the real incident (`L01-M2`, `L09-M2` set back to `completed`, matching their actual verified output), then re-ran the exact same 3-concurrent-job scenario twice more against the properly-locked version: 0 lost entries, 0 crashes, previously-completed jobs' status stayed untouched both times.

### Why the 0.14.1 changes exists

- **Docs-only: added explicit naming-consistency guidance to `SKILL.md`'s "Required workflow"** (new step 11). Requested directly, after building the real 71-video lecture series manifest with ad-hoc test job IDs (`linreg_module1`, `url_only_lecture`) that had no relationship to the card text shown in the video -- fine for a single throwaway test, a real mess at series scale. Now documents the actual scheme used: `id = f"L{lecture:02d}"` (+`-M{module}`), card `title = f"L{lecture}: {topic}"` (matches `upload_to_youtube.py`'s own convention), `subtitle = f"Module {module} of {module_count}: {detail}"`.

### Why the 0.14.0 changes exist

- **New `basic` card style for `generate_images.py`, now the default.** Requested directly for a 70-video lecture series: the old `ai` path generated a fresh OpenAI background per video, so no two cards looked alike unless `--theme` was held byte-identical across every job -- the opposite of what a series wants. `basic` renders a flat/gradient background locally with Pillow (no API call, no cost, deterministic -- same title in, pixel-identical layout out), with `--eyebrow`/`--bg-color`/`--bg-color2`/`--accent-color`/`--text-color` as the brand, set once at the manifest level (`card_eyebrow`, `card_bg_color`, etc.) and applied to every job. Also added real title wrapping (previously overlay_text() just drew the title on one line -- a long lecture title silently ran off the right edge; now wraps up to 2 lines and shrinks the font before accepting more, verified against both a normal-length and a deliberately extreme title). `ai` style remains available for a genuine one-off video.
- **Fixed a second real bug found while wiring up `basic` style's resolution-matching**: `sync_segments.py` had a hardcoded `scale=1920:-2` applied to every chunk regardless of the source video's actual resolution. Invisible for the Introduction lecture (already 1920×1080), but the Linear Regression lecture's source was natively 1280×720 -- every chunk was silently upscaled, producing a blurrier, 33% larger file for zero real quality gain. Now probes the source's native width and uses that by default (`--target-width` to force a specific width instead, for a series that wants one resolution despite differing source recordings).
- Tested for real: rendered the `basic` card at both a normal title length and a deliberately extreme one (confirmed both 2-line wrap and font-shrink paths), reran `sync_segments.py` on the actual Linear Regression job and confirmed output stayed at native 1280×720 (was wrongly 1920×1080 before) with a clean decode and a 33% smaller file, then regenerated images at the now-matching 1280×720 and reran the full intro-card assembly end to end -- correct duration (908.414535s), correct resolution, clean decode, 0 QA failures.

### Why the 0.13.0 changes exist

- **Fixed a real, previously-undiscovered bug in `prepend_intro_card()`: prepending a real intro card onto a SYNC-path video silently produced a corrupt final video.** Found because every single test of the SYNC-default rewrite (0.9.0 through 0.12.x) had used `--skip-images`, so this code path had genuinely never run end-to-end with a real intro card until the Linear Regression lecture test. Two compounding bugs, both real:
  1. `sync_segments.py`'s output (built by concatenating many individually trimmed/freeze-extended chunk segments) has an irregular effective frame rate and time base -- confirmed via `ffprobe` on a real 905.4s video: `avg_frame_rate` came back as an ugly fraction, not a clean `N/1`. The old code joined it to the intro clip via `ffmpeg -f concat -c copy`, which trusts container-level timestamps -- against a source like this, it silently miscomputed duration (905.4s + a 3s intro became 988.8s, not 908.4s) with non-monotonic DTS, and raised no error. Fixed by switching to the concat **filter** (`concat=n=2:v=1:a=1`), which decodes and re-times frames rather than trusting container metadata.
  2. The concat filter requires matching resolution across inputs, which surfaced a second real bug: `generate_images.py` always generated at a fixed 1536×864 regardless of the source video's actual resolution (e.g. 1920×1080), so the two inputs never matched. Fixed two ways -- `prepend_intro_card()` now probes the video's real resolution and scales the intro clip to match before concatenating (defensive, always correct), and `generate_images.py` gained a `--target-size` flag so `run_stage1_improve.py` can generate the images at the *correct* resolution from the start rather than relying on a scale-down/up fix afterward (suggested directly, and the better fix -- no upscaling quality loss on the intro card, and the YouTube thumbnail comes out at a real resolution instead of an arbitrary fixed one).
- Tested for real against the actual failure: reproduced the corrupt 988.8s/non-monotonic-DTS output on the real Linear Regression lecture video, verified the concat-filter-only fix got the duration right but didn't fix a resolution-mismatch failure that showed up next, then verified the complete fix (resolution-matched images + concat filter) end-to-end -- correct duration (908.414535s, exactly 905.414521 + 3.0), correct resolution (1920×1080, matching the source), clean decode, frame spot-checks confirming both the intro card and lecture content render correctly.

### Why the 0.12.1 changes exists

- **Docs-only: rewrote "Plugin structure" and "Pipeline overview" to match reality.** Requested directly, and overdue -- both had gone stale across several versions of real changes. "Plugin structure" was missing `.claude-plugin/`, `detect_fillers.py`, `qa_check.py`, `resolve_youtube_source.py`, `upload_to_youtube.py`, and `run_stage2_publish.py` entirely (last accurate around 0.2.0-0.3.0). "Pipeline overview" didn't show the diff-review step or `detect_fillers.py` at all. Replaced with a "Step flow" table keyed to the exact `Step N/6` labels the orchestrator actually prints, including which steps can hard-stop a job and why -- and fixed the "Bundled scripts" table's `measure_transcript_diff.py` and `run_stage1_improve.py` rows, which had been updated in `SKILL.md` for 0.11.0/0.12.0 but never mirrored here, a real drift between the two docs.

### Why the 0.12.0 changes exist

- **`measure_transcript_diff.py` now also scores meaning-preservation, not just lexical change.** Requested directly: the lexical word-diff alone can't distinguish a heavy paraphrase that's still faithful from a small edit that flips meaning -- both can show similar change %. New `--semantic` flag embeds each chunk's original span and its cleaned text via OpenAI's `text-embedding-3-small` (one batched request per side, a fraction of a cent for a full lecture) and reports cosine similarity, both per-chunk and as a mean. `run_stage1_improve.py`'s Step 2 now passes `--semantic` automatically whenever `OPENAI_API_KEY` is set (skipped with a printed note otherwise -- not a hard requirement, since the lexical check works standalone). New `min_semantic_similarity` manifest/job field hard-fails Step 2 the same way `max_change_pct` already does, as a separate axis.
- Tested for real against the actual full-lecture cleanup from the URL-only replication test: mean similarity 0.831, correctly passed a 0.80 floor and correctly hard-failed (exit 1) a 0.85 floor. The per-chunk breakdown caught something genuinely useful: it flagged the chunk where I'd corrected the instructor's ASR-garbled name (similarity 0.32 -- expected, since "Indrin T" and "Yanjun Qi" are just different proper-noun strings with no semantic relationship to an embedding model, not a meaning drift) but also flagged a chunk where the cleaned text ("here's the roadmap") described content that belonged to what the original was about to say next rather than what was literally in that chunk's span -- a real chunk-boundary content mismatch the lexical diff alone didn't surface.

### Why the 0.11.0 changes exist

- **`measure_transcript_diff.py` now runs automatically as `run_stage1_improve.py`'s Step 2, and can hard-fail a job.** Found while cross-checking the plugin's cleanup step against this project's actual manual workflow, stage by stage: the manual process had a real diff-review artifact (`original_vs_revoice_comparison.md`, a full side-by-side listing) but genuinely no tooling anywhere that computed a percentage -- `measure_transcript_diff.py` was a new capability, not a port of something that existed. It previously only ran if an agent remembered to invoke it by hand. It's now unconditional: Step 2 always runs it against the just-produced chunks, prints the stats, and persists them to `work/diff_report.json` (added `-o`/`--output` to the script to make that possible). A new `max_change_pct` manifest/job field turns "the rewrite changed too much" into an actual hard failure (exit 1, real stop) instead of a number nobody looked at -- same principle as the voice-ID gate and the confirmed-title gate elsewhere in this plugin: don't just document a requirement, enforce it.
- **Added `detect_fillers.py`, wired into the `needs_cleanup` pause.** Also found during the same stage-by-stage comparison: the manual project used a script exactly like this (same pattern-matching approach: um/uh, stutter repeats, discourse fillers with context) to pre-flag candidates before a human classified them, but the plugin's pause handed over the raw transcript cold. `run_stage1_improve.py` now runs it the moment a job pauses and folds the candidate counts into the pause message. Deliberately did not try to automate the classification/rewording steps themselves (the manual project's `discourse_filler_classification.json` and `wording_corrections.json`) -- both require understanding what the speaker meant, not pattern matching, so that stays a judgment call.
- Tested for real, twice: (1) ran the new Step 2 against a freshly agent-authored cleanup of the real full lecture with no cap (always runs, persists the report, doesn't block: 48.5% changed, reported and saved), then with `max_change_pct: 20` (correctly hard-fails, exit 1, real stop before any TTS spend) and `max_change_pct: 60` (correctly passes through). (2) Confirmed `detect_fillers.py`'s wiring produces a real report at the pause (125 definite fillers, 24 stutter repeats, 50 discourse fillers) whose discourse count exactly matches the manual project's own classification file (also 50 items) -- same deterministic logic, not an approximation.

### Why the 0.10.0 changes exist

- **Added `detect_fillers.py` to the bundle, wired into the `needs_cleanup` pause.** Found while comparing this project's manual workflow against the plugin step by step: the manual process used a script exactly like this (`detect_fillers.py`, same pattern-matching approach) to pre-flag filler candidates before a human classified them, but the plugin's `needs_cleanup` message handed over the raw transcript cold with no such assist. `run_stage1_improve.py` now runs it automatically the moment a job pauses, and folds the candidate counts + report path into the pause message. Deliberately did NOT try to automate `discourse_filler_classification.json` or `wording_corrections.json` (the manual project's own filler-classification and rewording decisions) — those require understanding what the speaker meant (is "like" here a verbal tic or the literal word "similar to"?), which is exactly the kind of judgment call this plugin has consistently refused to fake elsewhere (voice-ID gate, confirmed-title gate, the pause itself). `detect_fillers.py` finds candidates by pattern matching; deciding which ones are disposable stays an agent/human call.
- Tested for real: ran the wiring against this project's actual transcript and confirmed the report is generated and its counts land in the pause message (125 definite um/uh, 24 stutter repeats, 50 discourse fillers) — the discourse count exactly matches the manual project's own `discourse_filler_classification.json` (also 50 items), confirming it's the same deterministic logic, not a rough approximation.

### Why the 0.9.0 changes exist

- **`sync_segments.py` is now Stage 1's only assembly path, not an optional manual alternative.** A direct quality comparison against this project's manually-produced reference video (`final_revoice_v4voice.mp4`) showed the *old* default path -- `chunk_transcript.py` → `concat_audio.py` → a single whole-video mux with `-shortest` -- has no per-chunk sync guarantee: it replaces the entire audio track at once, so small per-chunk TTS/original-duration mismatches accumulate across a long video instead of getting corrected as they occur. `sync_segments.py` (added back in 0.2.0, previously something you had to invoke by hand) fixes this per chunk. Making it the default meant `resolve_transcript()` had to split into separate resolvers for the source transcript (`resolve_source_transcript`) and the cleaned script (`resolve_cleaned_script`, now `{anchor, clean}` pairs instead of flat text), and the `needs_cleanup` pause message now asks for `cleaned_script.json` specifically.
- **Removed the SIMPLE fallback path from the orchestrator entirely** (chunk_transcript.py + concat_audio.py + single mux) rather than keeping it as a silent degrade-path when no source transcript is available. A job with no resolvable source transcript now fails outright with a clear message telling you which manifest field to add, instead of quietly producing a lower-quality, un-synced video. `chunk_transcript.py`, `concat_audio.py`, and `trim_video.py` remain in `scripts/` as standalone tools for the genuinely different case of narration written fresh over B-roll with no source recording to sync against -- `sync_segments.py` doesn't apply there since there's nothing to anchor timestamps to.
- **`prepend_intro_card()` extracted as its own function**, reused by the (now single) assembly path -- `sync_segments.py` produces a fully audio-replaced video directly and doesn't know about intro cards, so intro-card prepending had to become a separate step rather than living inside one combined mux+intro function as before.
- **Fixed a real `qa_check.py` bug found while testing this**: it hard-FAILed on a missing `work/master_narration.wav`, an artifact the SYNC path never produces (audio_parts feed `sync_segments.py` directly, no concatenation step). Fixed to recognize `work/synced.mp4` as the SYNC path's audio reference instead of failing on a file that path was never going to create.
- Tested for real, not just reviewed: ran `run_stage1_improve.py --execute` end-to-end against this project's actual source video and transcript (same 3-chunk excerpt and the same cloned voice used for `final_revoice_v4voice.mp4`), confirmed correct step branching in dry-run first, then a real TTS + assembly run producing a playable final video at -15.73 LUFS (matching the hand-built reference within noise) with `qa_check.py` passing clean (0 failures) after the fix above. Also tested the new hard-failure path (no source transcript resolvable) and confirmed it exits 1 with a clear message, not a silent fallback.

### Why the 0.8.0 changes exist

- **Split into an explicit two-stage model**: `run_batch.py` renamed to `run_stage1_improve.py` and extended with a real "Step 0" that resolves video/transcript inputs from YouTube (wasn't possible before -- the old script required both already local), and pauses with status `needs_cleanup` rather than either failing outright or pretending to auto-clean a transcript itself when one doesn't exist yet -- that step is inherently a judgment call only an agent/human can make well (as this whole session demonstrated). New `run_stage2_publish.py` batch-publishes Stage 1's reviewed local outputs, applying `upload_to_youtube.py`'s exact-match confirmation principle at batch scale: one combined `publish_plan.json`, one user confirmation, then a content-hash check that the plan hasn't drifted since review before anything uploads.
- Tested the full resolve → pause → resume flow for real (not mocked): downloaded a real video and captions via the extended Stage 1 script, confirmed it correctly paused with `needs_cleanup` and the exact right file paths, then confirmed resuming with a cleaned transcript present picks up correctly at chunking. Found and fixed a real bug in the process -- dry-run mode was reporting jobs as "FAILED" when the resolve step legitimately had nothing to do yet (expected dry-run behavior treated as a real error).
- Also tested `run_stage2_publish.py`'s dry-run plan generation (correct titles, correct skip reasons for ineligible jobs) and all three confirmation-gate paths (missing plan, tampered/stale plan, and the correct plan reaching the actual upload call) without attempting any real OAuth or upload.

### Why the 0.7.0 changes exist

- **New: `upload_to_youtube.py`**. This is a meaningfully bigger step than anything else the plugin does -- it publishes to a real channel, not just local files or API-generated assets. Two protections beyond the usual dry-run-by-default pattern: (1) titles follow the "L**" lecture-numbering convention confirmed against the source material's own existing naming (`S0-L1-Introduction-Module1` -> `L1: Introduction - Module 1`), and (2) `--execute` hard-fails unless `--confirmed-title` matches the computed title character-for-character -- a technical gate, not just a documentation suggestion, forcing the literal title to have actually been shown to and confirmed by the user before anything uploads. Privacy defaults to `private`. Uses Google's official OAuth/upload client libraries rather than hand-rolled HTTP, unlike every other script in this plugin -- OAuth2 and resumable-upload protocols are enough of a correctness/security surface that reimplementing them for this one consequential capability isn't the right tradeoff, even though it breaks from the plugin's usual minimal-dependency approach.
- Tested everything that's safely testable without real Google credentials: title computation (single- and multi-module), the dry-run plan output, and the confirmation-mismatch gate (missing title, wrong title, and the correct-title-but-missing-dependency path all verified to fail/succeed as intended). Did not attempt a real OAuth flow or upload -- that requires the user's own credentials and is a genuinely public-facing action, not something to test speculatively.

### Why the 0.6.2 changes exist

- **Duration-mismatch check now actually fails, not just warns**: tested `--compare-against` with a genuinely different video (a 60s clip vs. a 1067s caption track, ~1678% off) and found the script still exited 0 -- a stderr warning is easy for an automated caller (e.g. `run_stage1_improve.py`, if this is ever wired into it) to miss entirely. Now anything beyond a 15% duration difference is a hard failure (exit 1); the output file is still written for manual inspection, but the exit code reflects that it shouldn't be trusted automatically. Small mismatches (2-15%, e.g. differently-trimmed intros between uploads) remain a WARN, not a failure. Also re-tested the whole tool against the genuinely local pre-existing video file (not a fresh YouTube download standing in for "local") to confirm it behaves identically either way.

### Why the 0.6.1 changes exist

- **Real-world tested `resolve_youtube_source.py`**: ran both `--download-video` and `--download-captions` against a live public video (not just dry-run/mocked). The video download hit a genuine HTTP 403 caused by a stale yt-dlp version (~6 weeks old) -- initially looked credential-related, wasn't; `brew upgrade yt-dlp` fixed it outright. Along the way, diagnosed that yt-dlp's browser-impersonation feature (needed for some extraction paths) requires a specific `curl_cffi` version range and silently reports "unavailable" with an incompatible one. Baked the fix sequence into the script's own error message instead of leaving a raw traceback. Verified the downloaded video's duration to the millisecond against known-correct ground truth (1065.400s vs. 1065.401179s) and confirmed captions still extract identically against the freshly-downloaded copy.
- Fixed two bugs caught only by actually running the failure path: `sys.exit()` doesn't take a `file=` kwarg (that's `print()`'s), and a multi-line error string had stray mid-sentence whitespace from indentation that looked fine as source but printed wrong.

### Why the 0.6.0 changes exist

- **Renamed `skills/elevenlabs-voiceover/` to `skills/build-video-voiceovers/`**: the folder name implied ElevenLabs-only, but the skill has always covered both ElevenLabs and Fish Audio equally, plus video assembly, trimming, and thumbnails/intro cards. Renamed to match the skill's own frontmatter `name:` field, which was already provider-neutral — one consistent name instead of three different ones (folder, frontmatter, description).

### Why the 0.5.0 changes exist

- **Input resolution wasn't a step at all**: the pipeline assumed both the video and the transcript were already local files, with no path for "the video is a YouTube link" or "there's no local transcript, but this recording was also uploaded to YouTube — use its captions." `resolve_youtube_source.py` adds both, ported and verified against real data from the session that motivated it (reproduced an exact prior result: 2,365 word tokens, 125 "um"/"uh" fillers preserved, 1.8s duration difference between a local file and its YouTube upload). Manual vs. automatic caption tracks are flagged explicitly, since manual/creator-uploaded captions are commonly pre-cleaned and would silently defeat filler-removal work if trusted blindly.

### Why the 0.4.0 changes exist

- **No silent voice defaults**: `generate_tts_fish.py --execute` used to fall through to Fish Audio's platform default voice when no `--voice-id`/`FISH_AUDIO_VOICE_ID` was set (the ElevenLabs script already required one). Now both refuse to run without a voice ID unless the platform default is explicitly requested (`--allow-default-voice`, Fish Audio only). The new **Voice selection** section in `SKILL.md` tells the agent to ask the user, clone from a real sample, and confirm with a short test-phrase comparison before committing — the same process that surfaced real, sample-dependent quality differences in practice.

### Why the 0.3.0 changes exist

- **Reorganized per-job output**: every job now gets one consistent `work/` (intermediates) + `final/` (deliverable) shape instead of a mix of loose files and inconsistently-nested subdirectories — see "Per-job output layout" above for the full rationale.

### Why the 0.2.0 changes exist

Built from direct experience producing a real lecture video end-to-end (filler removal, full English rewrite, Fish Audio + ElevenLabs voice cloning, title cards):

- **Loudness bug**: raw TTS output measured ~19 LU quieter than the source video's natural speech in one real run. A single loudnorm pass on the final concatenated master does NOT fix this — it corrects overall programme loudness, dominated by whichever audio is longest, and can't locally re-level short quiet segments. `qa_check.py`'s `loudness_consistency` check would have caught this as a WARN, but only after the fact. Normalizing every part before concat closes the gap at the source.
- **Sync drift**: the original `trim_video.py` + `concat_audio.py` path treats video trimming and narration as independent steps, checked only in aggregate (final duration within ~6s of master audio). In practice, cleaned/de-filled text is reliably faster to say than the original disfluent delivery, so per-chunk drift is the norm, not the exception. `sync_segments.py` guarantees sync at the chunk level: trim video if TTS ran short, freeze-extend the last frame if it ran long.
- **`response_format` error**: current `gpt-image-*` models reject this parameter (HTTP 400) — it's a holdover from the DALL-E 2/3 API, where it was needed to request base64 instead of a URL. Current models always return base64.
- **Diff quantification**: for any transcript rewrite beyond mechanical filler-stripping, know the actual percentage of words changed before spending API budget on TTS — not just a read-through "this sounds cleaner" judgment call.

### TTS provider comparison

| | Fish Audio | ElevenLabs |
|--|------------|------------|
| API key | `FISH_API_KEY` | `ELEVENLABS_API_KEY` |
| Voice env | `FISH_AUDIO_VOICE_ID` | `ELEVENLABS_VOICE_ID` |
| Script | `generate_tts_fish.py` | `generate_tts.py` |
| Speed control | `--speed 0.5–2.0` | voice settings |
| Docs | [docs.fish.audio](https://docs.fish.audio) | [elevenlabs.io/docs](https://elevenlabs.io/docs) |

### Image generation pricing (OpenAI gpt-image-1.5)

| Quality | Price/image | 70 videos (2 images each) |
|---------|------------|---------------------------|
| low | ~$0.009 | ~$1.26 |
| **medium (default)** | **~$0.034** | **~$4.76** |
| high | ~$0.133 | ~$18.62 |

### Batch workflow

**1. Create `job_manifest.json`** — see **Setup** above for how to land on a `voice_id`; a job with neither a `source_transcript` nor a `cleaned_script` pauses with `needs_cleanup` rather than guessing at cleanup itself:
```json
{
  "provider": "fish_audio",
  "voice_id": "your-fish-audio-voice-id",
  "card_style": "basic",
  "generate_images": true,
  "output_base": "output/",
  "welcome_message": "Welcome to the course. This is Lecture {lecture}.",
  "jobs": [
    {
      "id": "video_001",
      "source_video_url": "https://www.youtube.com/watch?v=...",
      "cleaned_script": "transcripts/video_001_script.json",
      "title": "Introduction to Python",
      "subtitle": "Beginner Series – Episode 1",
      "lecture": "1",
      "topic": "Introduction to Python"
    }
  ]
}
```
`cleaned_script` is optional — omit it to have the job pause at `needs_cleanup` and produce a raw transcript for review first. Full schema (all optional fields — `max_change_pct`, `min_semantic_similarity`, per-job voice/welcome-message overrides, card branding, `ai`-style `theme`, etc.) is in `run_stage1_improve.py`'s module docstring.

**2. Dry-run (no charges):**
```bash
python3 scripts/run_stage1_improve.py job_manifest.json
```

**3. Execute after reviewing cost estimates:**
```bash
python3 scripts/run_stage1_improve.py job_manifest.json --execute
```

**4. Resume if interrupted:**
```bash
python3 scripts/run_stage1_improve.py job_manifest.json --execute --resume
```

### Per-job output layout

**0.2.0 reorganized this.** Previously `chunks.json` and `master_narration.wav`
sat loose at the job root while `audio_parts/`/`assets/`/`output/` were
subdirectories -- an inconsistent mix, and `qa_check.py` had already been
written expecting a `work/` subdirectory that `run_stage1_improve.py` never actually
created. Now every job gets one consistent shape:

```
<output_base>/
├── batch_manifest.json (your job_manifest.json, if you keep a copy here)
├── batch_log.json                    ← per-job status, updated after each job
└── <job_id>/
    ├── work/                         ← everything intermediate, in one place
    │   ├── chunks.json
    │   ├── audio_parts/
    │   │   ├── part_001.<ext>
    │   │   ├── part_002.<ext>
    │   │   └── tts_manifest.json
    │   ├── master_narration.wav
    │   └── assets/
    │       ├── thumbnail.jpg
    │       ├── intro_card.png
    │       └── image_manifest.json
    ├── qa_report.json                ← job-level, check this first
    └── final/                        ← the actual deliverable
        └── <job_id>_final.mp4
```

Two deliberate choices:
- **Everything intermediate lives under `work/`** — nothing ambiguous sitting loose at the job root. At 70-video scale, this also makes it trivial to `rm -rf */work` to reclaim disk space once you've shipped the finals and don't need the parts anymore.
- **The deliverable folder is `final/`, not `output/`** — the old name collided with `output_base` (the *batch's* own top-level directory), so `output_base/video_001/output/` read as "output" nested inside "output" with no way to tell them apart at a glance.

If you're using `sync_segments.py` directly (outside `run_stage1_improve.py`, for the
narration-replacement path) you choose your own output paths -- it's a
standalone tool, not tied to this per-job layout. Follow the same `work/` +
`final/` convention if you want `qa_check.py` to be able to audit it too.

### System requirements

- Python 3.8+
- `ffmpeg` and `ffprobe` on PATH
- `yt-dlp` on PATH (`brew install yt-dlp`) — only needed if using `resolve_youtube_source.py`. Keep it
  up to date (`brew upgrade yt-dlp`) — YouTube changes its anti-bot requirements often enough that
  `--download-video` specifically (not `--download-captions`, which is far less sensitive to this)
  can start failing with HTTP 403 on a version just a few weeks old. The script's error message
  walks through the fix if this happens.
- `pip install google-api-python-client google-auth-httplib2 google-auth-oauthlib` — only needed for
  `upload_to_youtube.py`. Plus a Google Cloud project with the YouTube Data API v3 enabled and OAuth2
  "Desktop app" credentials (see the script's docstring for the one-time setup).
- `pip install pillow` (for thumbnail text overlay)
- API keys set as environment variables

### Alternative path — narration over B-roll, no source recording

`run_stage1_improve.py` does **not** support this case — there's no source
recording to anchor timestamps against, so `sync_segments.py` doesn't apply.
Three standalone scripts remain for it, run by hand:

```
cleaned_transcript.txt  →  chunk_transcript.py   →  chunks.json (character-count only,
                                                      no timestamp mapping)
chunks.json     →  generate_tts_fish.py (or generate_tts.py)  →  audio_parts/
audio_parts/    →  concat_audio.py                    →  master_narration.wav
source_video    →  trim_video.py                      →  trimmed.mp4  (optional cut pass)
trimmed.mp4
+ master_narration.wav  →  FFmpeg (manual mux)         →  final_video.mp4
```

