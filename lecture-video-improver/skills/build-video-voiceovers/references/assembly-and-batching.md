# Video assembly, optional assets, and batch production

## Contents

1. Working layout
2. Timeline rules
3. FFmpeg patterns
4. Optional thumbnail and intro
5. Batch controls
6. QA contract

## Working layout

Use a separate directory per source so a resumed batch cannot mix artifacts:

```text
video-name/
├── input/                 source media and raw English transcript
├── work/
│   ├── cleaned_transcript.txt
│   ├── chunks.json
│   └── audio_parts/
├── assets/                optional thumbnail and intro card
├── preview/
├── output/
└── manifests/             job, generation, edit, and QA records
```

Inputs remain immutable. A batch-level job manifest must pair each source,
transcript, English voice configuration, title, and output directory explicitly.

## Timeline rules

Choose one timing authority:

- For narration-first work, the verified master narration is authoritative;
  fit slides, screen recordings, B-roll, and graphics to it.
- For a talking-head edit retaining original speech, the timestamped source
  transcript and approved edit decision list are authoritative.
- For a talking-head video with replacement narration, work in reviewed
  segments. Compare each source keep interval with its generated narration
  duration and use cutaways, holds, modest retiming, or forced alignment.

Do not use a list of visual `select` intervals and independently generated audio
as if they share timing. A global `-shortest` can hide a duration error by
truncating content; check durations first. Simple audio replacement is not lip
sync.

## FFmpeg patterns

Use the bundled audio assembler rather than putting MP3 packets into a `.wav`
container with `-c copy`:

```bash
python3 scripts/concat_audio.py work/audio_parts work/master_narration.wav
```

For an already aligned video and narration whose measured durations agree
within the approved tolerance:

```bash
ffmpeg -i aligned_video.mp4 -i master_narration.wav \
  -map 0:v:0 -map 1:a:0 -c:v copy -c:a aac -b:a 192k \
  -movflags +faststart -shortest output.mp4
```

Before using this template, verify stream-copy compatibility and actual
alignment. Re-encode video when the timeline, frame rate, scale, or color format
changes.

To prepend a static intro, normalize the intro clip and main program to the same
resolution, frame rate, pixel format, sample rate, and channel layout before
concatenating. Generate exactly the requested intro duration, including a
silent stereo stream of the same length. Derive the FFmpeg filter from
`ffprobe` results instead of assuming 1920×1080, 30 fps, stereo, or an existing
audio stream.

## Optional thumbnail and intro

Only create these assets when requested.

- Thumbnail: default to 1280×720 (16:9) unless the publishing target specifies
  another format. Generate or choose a clean background based on the approved
  English title and summary. Avoid AI-rendered text; overlay exact title and
  brand marks programmatically.
- Intro card: default to the requested output dimensions and 3–5 seconds. Use a
  readable English title, optional subtitle, consistent margins, and a visual
  treatment related to the thumbnail.
- Store the generation prompt, title text, dimensions, and source rights in the
  job manifest. Do not add logos, portraits, or copyrighted assets without
  permission.

## Batch controls

For a large collection such as 70 videos:

1. Validate the job manifest and report missing or ambiguous pairs.
2. Confirm that each source and output transcript is English. This skill does
   not translate mixed-language material.
3. Dry-run transcript sizes, chunk counts, selected voice/model, expected paid
   calls, and existing output conflicts for the entire batch.
4. Complete and review two or three representative pilots, including the
   longest and most terminology-heavy source.
5. Obtain approval for the remaining paid execution.
6. Process sequentially by default. Enable bounded concurrency only when the
   user requests it and account limits are known.
7. Record per-job state and hashes so a rerun skips verified work. Never treat a
   zero-byte or unverified file as complete.
8. Stop on authentication/quota errors and after repeated source-specific
   failures; do not continue burning credits across the batch.
9. Regenerate only rejected chunks or assets, then rebuild downstream outputs.

## QA contract

For every final, record:

- source and cleaned transcript hashes;
- chunk count, character counts, English voice, model, and output format;
- audio-part and master durations;
- source, preview, and final media stream metadata;
- intro and thumbnail dimensions when present;
- checks for missing, repeated, clipped, or mispronounced speech;
- inspection of each chunk join and the first/last five seconds;
- A/V duration difference and the reason for any intentional mismatch;
- final path and whether it is preview or approved production output.

Use a complete preview for human review before batch finalization. FFmpeg exit
status and file existence are necessary evidence, not sufficient QA.
