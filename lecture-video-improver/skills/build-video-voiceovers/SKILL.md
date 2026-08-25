---
name: build-video-voiceovers
description: Build one or many English-language long-form narrated videos from raw English transcripts and source media using conservative transcript cleanup, sentence-safe chunking, TTS synthesis via ElevenLabs or Fish Audio, FFmpeg audio assembly and video muxing, and optional thumbnails or intro cards. Use for English-to-English long scripts, batch voiceover production (e.g. 70 videos), filler-word removal before TTS, regenerating individual narration chunks, or replacing/adding English narration to edited video. Do not use for translation, multilingual production, environment installation or repair, generic video-editing advice, or short-form vertical editing without a long-form narration workflow.
---

# Build Video Voiceovers

Create reproducible long-form narration and video outputs. Supports two TTS
providers: **ElevenLabs** and **Fish Audio**. The chunking, audio assembly,
and video muxing workflow is identical for both.

## Two-stage model

The pipeline is deliberately split into two independent stages with a human
review checkpoint between them -- this is what makes batch production (e.g.
70 videos) safe rather than just fast:

- **Stage 1 -- Improve** (`run_stage1_improve.py`): raw video/transcript
  (local files or YouTube links) → English-improved, filler-free, fully
  assembled **local** video. Nothing here touches any publishing platform.
  A job that needs its transcript cleaned up **pauses** (status
  `needs_cleanup`) rather than guessing at the rewrite itself -- that step
  needs an agent or human, not a script (see step 0 in the script's
  docstring for why).
- **Human review**: watch the finished local videos in
  `<output_base>/<job_id>/final/` before anything goes further. This is the
  actual point of splitting the stages -- it's much cheaper to catch a bad
  take, wrong voice, or mistitled video locally than after it's live.
- **Stage 2 -- Publish** (`run_stage2_publish.py` for a batch,
  `upload_to_youtube.py` directly for one video): reviewed local videos →
  YouTube, with "L**" title formatting and a hard, exact-match confirmation
  gate before anything uploads (see **YouTube upload** below).

Run Stage 1 for as many videos as you want, in one batch, without ever
touching a real channel. Only run Stage 2 once you've actually watched the
results.

## Reusable scripts

The bundled scripts live at `${CLAUDE_PLUGIN_ROOT}/scripts/`. Reference them
by absolute path or copy them into your project's `scripts/` directory:

| Script | Stage | Purpose |
|--------|-------|---------|
| `detect_fillers.py` | 1 | Scans the raw transcript for candidate um/uh, stutter repeats, and discourse fillers (like/you know/i mean/sort of/kind of) with context — pure pattern matching, runs automatically at the `needs_cleanup` pause to narrow the search space. Doesn't decide what's actually disposable — that's still yours |
| `chunk_transcript_with_timestamps.py` | 1 | **What `run_stage1_improve.py` uses.** Anchors each `{anchor, clean}` pair to an exact timestamp span in a word-level source transcript, so `sync_segments.py` knows exactly which video span each chunk replaces |
| `generate_tts.py` | 1 | ElevenLabs TTS — plan or execute sequential generation, resume, regenerate chunks |
| `generate_tts_fish.py` | 1 | Fish Audio TTS — same interface, uses `api.fish.audio/v1/tts` |
| `sync_segments.py` | 1 | **What `run_stage1_improve.py` uses to assemble.** Trims or freeze-extends each chunk's video segment to match its TTS clip's duration, then concatenates — keeps audio/video sync locked across the whole video instead of drifting |
| `generate_images.py` | 1 | Generate thumbnail + intro card. **`basic` card style (default): flat/gradient background, no API call, no cost, deterministic — same branding on every card, which is what a lecture series wants.** `ai` style (opt-in): OpenAI gpt-image-1.5 background from a `--theme` prompt, real cost, varies per call — better suited to a single one-off video than a series. |
| `measure_transcript_diff.py` | 1 | **Runs automatically as `run_stage1_improve.py`'s Step 2, not optional.** Lexical word-diff % vs. the raw transcript, persisted to `work/diff_report.json`; set `max_change_pct` to hard-fail an over-aggressive rewrite. Also scores meaning-preservation via OpenAI embeddings when `OPENAI_API_KEY` is set (`min_semantic_similarity` to hard-fail on that) — catches the case a lexical diff can't: heavy paraphrase with the same meaning vs. a small edit that flips it |
| `qa_check.py` | 1 | Post-assembly audit: chunk bounds, audio loudness consistency, boundary silences, final video/audio stream sanity |
| `resolve_youtube_source.py` | 1 | Download a video and/or extract+convert a caption track from a YouTube URL, when the video or transcript isn't already a local file |
| `run_stage1_improve.py` | 1 | **Stage 1 orchestrator.** Resolves inputs (incl. YouTube), pauses for agent transcript cleanup when needed, then chunk (timestamp-anchored) → TTS → images → assemble (`sync_segments.py`) → QA. Produces local-only output. Requires a word-level source transcript — see the script's module docstring. |
| `prepend_welcome.py` | 1 | **Optional, run automatically by `run_stage1_improve.py`'s Step 5 when `welcome_message` is set in the manifest.** Prepends a short spoken welcome/branding clip (same TTS voice) over a still of the video's own first frame, ahead of the intro card, and trims the intro card's redundant silent hold so there's no dead air between the spoken welcome and the content. Fish Audio only. Dry-run by default (`--execute` to actually call the TTS API and write output) — also runnable standalone against an already-assembled final video. |
| `upload_to_youtube.py` | 2 | Upload one finished video to YouTube — "L**" title formatting, hard pre-upload confirmation gate |
| `run_stage2_publish.py` | 2 | **Stage 2 orchestrator.** Batch-publishes Stage 1's reviewed local outputs — one combined plan, one confirmation, covering every video in the run |

Two scripts remain in `scripts/` but are **not** called by `run_stage1_improve.py`: `chunk_transcript.py` (plain character-count chunking, no timestamp anchoring) and `concat_audio.py` (concatenate parts into one master track). They're still useful for narration written fresh over B-roll with no source recording to sync against — a case `sync_segments.py` doesn't apply to, since there's nothing to anchor timestamps against. `trim_video.py` (cut filler/dead-air segments from source video via a keep-segments JSON) is likewise a standalone tool for manual production, not part of the orchestrator.

## Choose a TTS provider

| | ElevenLabs | Fish Audio |
|--|------------|------------|
| **Env var** | `ELEVENLABS_API_KEY` | `FISH_API_KEY` |
| **Voice ID env var** | `ELEVENLABS_VOICE_ID` | `FISH_AUDIO_VOICE_ID` |
| **Script** | `generate_tts.py` | `generate_tts_fish.py` |
| **Default model** | `eleven_flash_v2` | platform default |
| **Speed control** | via voice settings | `--speed 0.5–2.0` |
| **Formats** | mp3, wav, pcm, opus | mp3, wav, opus, flac, pcm |

Fish Audio voice IDs (`reference_id`) are found at fish.audio. Do not omit
`--voice-id` to fall through to the platform default as a convenience --
see **Voice selection** below. `generate_tts_fish.py --execute` refuses to
run with no voice specified unless `--allow-default-voice` is passed
explicitly, precisely to prevent that silent fallback.

## Voice selection

Never guess a voice ID, and never silently proceed with a provider's platform
default. If the user hasn't already told you which voice/model ID to use for
this job:

1. **Ask them directly** if they already have one in mind or already cloned.
2. If they want an existing library voice by name (Fish Audio only) rather
   than a clone — e.g. "use the voice 'Sarah'" — **search by name instead of
   guessing an ID**, since Fish Audio has many public voices sharing common
   names:
   ```bash
   curl -s "https://api.fish.audio/model?title=Sarah&page_size=10" \
     -H "Authorization: Bearer $FISH_API_KEY"
   ```
   Disambiguate the results yourself before proposing one — sort by
   `like_count` (a canonical/popular voice is usually an order of magnitude
   ahead of same-named alternatives), and cross-check `languages` and `tags`
   against what the job actually needs (e.g. `narration`/`conversational`
   for lecture narration, `language: en`). Tell the user which one you
   picked and why (id, like count, tags) before generating anything real —
   don't just silently take the first hit.
3. If not, and a cloned voice is wanted: **clone from a real, representative
   sample** of the target speaker (not the whole source file — a clean 60–90s
   excerpt free of filler words, background noise, and music works well; ask
   the user's consent before uploading any sample to the provider).
4. **Generate a short test phrase** (a sentence or two of the actual target
   content, not generic filler text) with the chosen/cloned voice, and let
   the user listen before committing to it for the full job. If they're
   unsure about a clone, try 2-3 different source excerpts as separate
   clones and compare — voice clone quality varies noticeably by sample
   choice, more than most people expect.
5. Only after the user confirms a specific voice ID should you pass
   `--voice-id` and proceed to real generation.

This adds a step, but it's cheap (a search call, or cloning + one test
phrase, costs far less than a full job) against the cost of generating an
entire narration in the wrong voice. Skip straight to step 5 only when the
user already has a voice ID they want reused.

## YouTube upload

Uploading is a bigger step than anything else this skill does — it publishes
content to a real channel, visible beyond just this conversation (even at
the "private" default, it's now sitting on the user's actual YouTube
account). Treat it accordingly:

1. **Compute the title from the "L**" convention**: `L{lecture}: {topic}`,
   or `L{lecture}: {topic} - Module {module}` when the lecture is split into
   multiple parts (check the source material itself for hints — a filename
   or existing title like `S0-L1-Introduction-Module1` tells you both the
   lecture number and that a module suffix is expected).
2. **Show the user the exact computed title, description, and privacy
   setting** — not a paraphrase, not "want me to upload this?" — the literal
   strings that would appear on YouTube. Get their explicit confirmation of
   that exact text.
3. Only then run with `--execute --confirmed-title "<the exact confirmed
   title>"`. This isn't just a style preference: `upload_to_youtube.py`
   hard-fails if `--confirmed-title` doesn't match the computed title
   character-for-character, specifically so a title can't be uploaded
   without having actually been shown to and confirmed by the user.
4. **Default privacy is `private`** unless the user explicitly asks for
   `unlisted` or `public`. Never upgrade privacy on their behalf.
5. One-time OAuth setup is per Google account, not per video — see the
   script's docstring. The cached token means only the very first upload
   needs a browser; ask before running that step, since it opens a real
   browser-based login flow.
6. For a multi-module lecture, confirm the module number explicitly rather
   than inferring it from file order — a batch of similarly-named files is
   an easy place to get the numbering wrong.
7. **For a batch, use `run_stage2_publish.py` instead of calling
   `upload_to_youtube.py` per video.** It applies the same principle at
   batch scale: one dry-run shows every computed title across all jobs in
   `publish_plan.json`, show that whole plan to the user once, then
   `--execute --confirmed-plan publish_plan.json` re-verifies the plan
   still matches byte-for-byte before uploading anything. If a job's local
   video changed since the plan was shown, the hash won't match and nothing
   uploads — go back and reconfirm the new plan rather than forcing it
   through.

## Boundaries and authorization

1. Preserve every source file. Create new artifacts in a per-video working
   directory; never overwrite source media, transcripts, generated chunks, or
   approved finals without explicit authorization.
2. Check `ffmpeg`, `ffprobe`, the chosen TTS credential, and the voice
   configuration before production.
3. Before the first paid TTS or image-generation call, identify the files and
   service, estimate the number of requests or characters, note that quota or
   charges may apply, and obtain explicit approval. A dry run is not approval
   to execute paid calls. Never print or commit API keys.
4. Use a cloned or private voice only when the user confirms they own it or
   have the speaker's permission.
5. Treat thumbnails and intro cards as optional deliverables; generate only
   when the user asks.
6. Never upload to YouTube without following **YouTube upload** above in
   full — the exact-title confirmation gate is not optional, and privacy
   defaults to `private` unless the user says otherwise.

## Choose the production mode

- **Narration-first voiceover:** Generate the master narration first, then fit
  slides, screen recordings, B-roll, or graphics to it.
- **Talking-head audio replacement:** Preserve a timestamped verbatim
  transcript for edit decisions and create a separate cleaned TTS script.
  Synthesized narration will not match lip movement automatically; use cutaway
  visuals, segment-level duration matching, or forced alignment.
- **Audio-only narration:** Stop after verified master audio and the generation
  manifest when no video assembly is requested.
- **Batch production (e.g. 70 videos):** Run two or three representative pilot
  videos through full preview and QA before authorizing the remaining batch.

Read [transcript and TTS rules](references/transcript-and-tts.md) whenever
cleaning, segmenting, or generating narration. Read
[assembly and batch rules](references/assembly-and-batching.md) before editing
the video timeline, adding assets, or processing multiple videos.

## Required workflow

1. **Resolve where the video and transcript actually live.** Don't assume
   both are already local files — ask if either is unclear:
   - **Video**: a local file path, or a YouTube URL to download
     (`resolve_youtube_source.py --download-video`)?
   - **Transcript**: an existing local transcript, ASR run separately
     (outside this plugin's scope), or pulled from a YouTube video's
     captions (`resolve_youtube_source.py --download-captions`)? The video
     and the transcript source do not have to be the same thing — a local
     video whose recording was *also* separately uploaded to YouTube is a
     common case where you want the local file for quality but YouTube's
     free captions for the transcript.
   - If pulling captions from YouTube: run `--list-captions` first and
     prefer an **automatic** track over a manual/creator-uploaded one —
     manual captions are frequently cleaned up by a human and may have
     already lost the filler words you're trying to find. State which kind
     you're using.
   - Get the user's explicit consent before any YouTube download — same
     "identify what, note it, get approval" standard used for paid API
     calls (Boundaries and authorization, above), even though yt-dlp itself
     is free.
   - When the transcript comes from a *different* source than the video
     being edited (YouTube captions paired with a local video file), always
     run the duration sanity check (`--compare-against <local_video>`) and
     surface the result before trusting the transcript — a mismatch beyond
     ~2% likely means it's not actually the same recording.
2. Establish the remaining inputs and desired outputs: TTS provider
   (ElevenLabs or Fish Audio), voice ID, target duration or speaking pace,
   aspect ratio, and whether thumbnails or intro cards are needed. Stop if
   the source or output is not English. If there's no clear voice ID yet,
   follow **Voice selection** above before generating any real audio.
3. Inspect media with `ffprobe`. For a batch, create and review an explicit
   job manifest pairing each source with its transcript and output directory.
4. Keep a word-level timestamped verbatim transcript (from ASR, or YouTube
   captions via `resolve_youtube_source.py`) -- this is what
   `chunk_transcript_with_timestamps.py` anchors against so sync never drifts.
   `run_stage1_improve.py` runs `detect_fillers.py` on it automatically at the
   `needs_cleanup` pause, giving you a pre-flagged candidate list (definite
   um/uh, stutter repeats, discourse fillers with context) instead of scanning
   cold -- start there, but it only finds candidates by pattern matching; which
   ones are genuinely disposable in context ("like" as a verbal tic vs. "like"
   meaning "similar to") is still your call. From the raw transcript, produce
   a cleaned **script**: an ordered list of `{"anchor": "...", "clean": "..."}`
   pairs, where `anchor` is a short verbatim quote marking where that chunk
   starts in the raw transcript and `clean` is the polished replacement text.
   Remove disfluencies conservatively, preserve facts and technical terms, and
   flag meaning-sensitive rewrites for review. `run_stage1_improve.py` runs
   `measure_transcript_diff.py` automatically right after chunking (Step 2),
   persisting both the lexical change % and (if `OPENAI_API_KEY` is set) a
   semantic-similarity score to `work/diff_report.json` -- set `max_change_pct`
   and/or `min_semantic_similarity` in the manifest if either should hard-fail
   there rather than just get reported. The two axes catch different things:
   high lexical change with high semantic similarity is a heavy paraphrase
   that's still faithful; high lexical change with LOW semantic similarity is
   the signature of a rewrite that drifted from what the speaker meant.
5. Segment with timestamp anchoring:

   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/chunk_transcript_with_timestamps.py" \
     source_transcript.json cleaned_script.json chunks.json
   ```

6. Review `chunks.json` and dry-run the chosen provider. No API calls are made
   without `--execute`:

   ```bash
   # ElevenLabs
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/generate_tts.py" \
     chunks.json --output-dir audio_parts

   # Fish Audio
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/generate_tts_fish.py" \
     chunks.json --output-dir audio_parts
   ```

7. After approval, generate sequentially with `--execute`. Resume with
   `--resume`; regenerate a single defective chunk with `--only <n> --overwrite`:

   ```bash
   # ElevenLabs
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/generate_tts.py" \
     chunks.json --output-dir audio_parts \
     --voice-id "$ELEVENLABS_VOICE_ID" --execute

   # Fish Audio
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/generate_tts_fish.py" \
     chunks.json --output-dir audio_parts \
     --voice-id "$FISH_AUDIO_VOICE_ID" --execute
   ```

8. Assemble directly with `sync_segments.py` -- no separate master-audio-concat
   step. It trims or freeze-extends each chunk's video span to match that
   chunk's TTS duration, then muxes and concatenates automatically, keeping
   sync locked across the whole video instead of drifting. Output resolution
   matches the source video's own native resolution by default (pass
   `--target-width` only if several videos in a series need to share one
   fixed resolution despite differing source recordings):

   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/sync_segments.py" \
     chunks.json audio_parts source_video.mp4 synced.mp4
   ```

   For batch runs, verify two or three pilots first, then process the rest
   sequentially.

9. This whole flow (steps 4-8) only applies when there's a source recording
   to sync narration against. For the different case of narration written
   fresh over B-roll with no such recording, use `chunk_transcript.py`
   (plain character-count chunking, no timestamp anchoring) +
   `concat_audio.py` (concatenate parts into one master track) +
   `trim_video.py` (cut filler/dead-air segments via a keep-segments JSON)
   instead -- `sync_segments.py` doesn't apply there since there's nothing
   to anchor timestamps against.

10. If requested, create a 16:9 thumbnail and 3-second intro card. Default to
   `basic` card style -- a flat/gradient background with the title overlaid,
   no API call, no cost, and (this matters for a series) deterministic: pass
   the same `--bg-color`/`--accent-color`/`--eyebrow` for every video and
   every card in the series looks like one product:

   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/generate_images.py" \
     --title "My Video Title" \
     --eyebrow "Course Name" \
     --output-dir assets/ \
     --execute
   ```

   Only reach for `ai` style (`--card-style ai --theme "..."`) for a single
   one-off video where a unique AI-generated background is actually wanted --
   it costs real money (~$0.034/image × 2 = ~$0.07/video at medium quality,
   requires `OPENAI_API_KEY`) and produces a different-looking card every
   call, which defeats a series' visual consistency.

10a. If requested, add a short spoken welcome/branding message ahead of the
   intro card -- e.g. "Welcome to the course. This is Lecture {lecture}." -- by setting
   `welcome_message` in the manifest (a template string; supports
   `{lecture}`/`{topic}`/`{title}` substitution from each job's own fields,
   applies to every job unless a job sets its own override, or
   `"welcome_message": null` to opt one job out). `run_stage1_improve.py`
   generates it in the job's own TTS voice, prepends it over a still of the
   video's own first frame, and trims the intro card's fixed silent hold
   afterward so the spoken welcome flows straight into the content with no
   dead air. Additive and optional: a failure here only warns, it doesn't
   fail the job -- the intro-card final video is still valid without it.
   Fish Audio only for now. If several jobs in a series would get identical
   wording (e.g. multiple modules of the same lecture), that's expected and
   fine -- no need to vary it artificially. Reuse `prepend_welcome.py`
   directly (dry-run first) to add a welcome message to an already-assembled
   video without rerunning the whole pipeline.

11. For a batch/series, derive every job's `id`, card `title`/`subtitle`, and
   the `lecture`/`topic`/`module` fields from ONE consistent scheme -- not
   independently chosen per job. Use `id = f"L{lecture:02d}"` (+ `-M{module}`
   if the lecture has more than one module), card `title =
   f"L{lecture}: {topic}"` (matches `upload_to_youtube.py`'s own title
   convention exactly), and card `subtitle = f"Module {module} of
   {module_count}: {detail}"` when there's a module. This makes the output
   folder name, the final video's filename, the on-screen card, and the
   eventual YouTube upload title all visibly the same identifier for a given
   video -- an ad-hoc job id like `linreg_module1` next to a card that reads
   "Linear Regression: Basic Task and Code Run" is exactly what NOT to do;
   it's fine for a single throwaway test but becomes a real mess across a
   real series.

12. For batch production, use the orchestrator (dry-run first, then --execute):

    ```bash
    # Dry-run all 70 jobs — shows TTS + image cost estimates, no charges
    python3 "${CLAUDE_PLUGIN_ROOT}/scripts/run_stage1_improve.py" job_manifest.json

    # Execute after approval
    python3 "${CLAUDE_PLUGIN_ROOT}/scripts/run_stage1_improve.py" job_manifest.json --execute

    # Resume after a failure
    python3 "${CLAUDE_PLUGIN_ROOT}/scripts/run_stage1_improve.py" job_manifest.json --execute --resume
    ```

13. Render a full preview and verify with `ffprobe`. Check chunk joins, pacing,
    pronunciation, audio levels, A/V duration, intro duration, and output
    dimensions. Save a QA report and generation manifest.
14. **This is Stage 2, a deliberately separate step.** Wait until the user
    has actually watched the Stage 1 output in `final/` before running
    anything here. Follow **YouTube upload** above in full.

    Single video:

    ```bash
    # Dry-run -- always safe, shows the exact title that would be used
    python3 "${CLAUDE_PLUGIN_ROOT}/scripts/upload_to_youtube.py" \
      final_video.mp4 --lecture 1 --topic "Introduction" --module 2

    # After the user confirms that exact title, verbatim
    python3 "${CLAUDE_PLUGIN_ROOT}/scripts/upload_to_youtube.py" \
      final_video.mp4 --lecture 1 --topic "Introduction" --module 2 \
      --execute --confirmed-title "L1: Introduction - Module 2"
    ```

    Batch (uses the SAME job_manifest.json Stage 1 used, reading each job's
    `lecture`/`topic`/`module` fields):

    ```bash
    # Dry-run -- writes publish_plan.json, shows every computed title, uploads nothing
    python3 "${CLAUDE_PLUGIN_ROOT}/scripts/run_stage2_publish.py" job_manifest.json

    # After the user confirms the WHOLE plan
    python3 "${CLAUDE_PLUGIN_ROOT}/scripts/run_stage2_publish.py" job_manifest.json \
      --execute --confirmed-plan output/publish_plan.json
    ```

    Requires `pip install google-api-python-client google-auth-httplib2
    google-auth-oauthlib` and a one-time OAuth setup (see
    `upload_to_youtube.py`'s docstring).
