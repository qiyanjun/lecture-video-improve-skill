# English transcript cleaning and ElevenLabs TTS

## Contents

1. Transcript artifacts
2. Conservative cleanup
3. Chunking contract
4. English TTS production
5. Duration and pronunciation

This skill accepts English speech and produces English narration. Do not
translate or switch languages inside this workflow.

## Transcript artifacts

Maintain two transcripts when edits depend on the original recording:

- `transcript_verbatim.json`: original English words with timestamps and
  speaker data. Use it to locate fillers, false starts, pauses, and visual cuts.
- `cleaned_transcript.txt`: English narration-ready prose. Use it for TTS and
  content review, not as evidence of the original timing.

ElevenLabs Scribe v2 supports a `no_verbatim` option that removes fillers,
false starts, and disfluencies. Set the transcription language to English when
supported. A clean transcript is useful for narration, but keep the separate
timestamped verbatim artifact when the video timeline must be edited precisely.

Official references:

- https://elevenlabs.io/docs/overview/capabilities/speech-to-text
- https://elevenlabs.io/docs/overview/capabilities/text-to-speech
- https://elevenlabs.io/docs/overview/models
- https://elevenlabs.io/docs/api-reference/text-to-speech/convert

API behavior and service limits can change. Check current official model
metadata or documentation before a large paid batch.

## Conservative cleanup

Apply these rules to one transcript at a time:

1. Remove `um`, `uh`, `erm`, `ah`, stuttered syllables, accidental duplicate
   words, and abandoned false starts.
2. Remove `like`, `you know`, or similar phrases only when they are clearly
   discourse fillers. Preserve literal comparisons, quotations, and meaningful
   uses.
3. Remove tangents only when the user asks for a tighter narrative or target
   runtime. Do not silently delete substantive examples or qualifications.
4. Improve awkward syntax and punctuation while preserving the speaker's
   claims, uncertainty, tone, English dialect, and personal voice.
5. Preserve names, numbers, dates, citations, equations, code, medical or legal
   qualifiers, and technical terms exactly unless the user approves a change.
6. Create a pronunciation review list for names, acronyms, symbols, formulas,
   and domain terminology. Use an English pronunciation dictionary when useful.
7. Use ordinary punctuation and conversational paragraph lengths to guide
   pauses. Do not insert stage directions that the selected model will speak.

For meaning-sensitive material, provide a compact change report with removed
tangents and substantive rewrites. Filler removal alone does not require a
word-by-word diff.

## Chunking contract

Use `scripts/chunk_transcript.py` after semantic cleanup.

- Target: approximately 2,000 characters.
- Preferred range: 1,500–2,500 characters.
- Hard default maximum: 2,500 characters.
- Boundary: end of English sentence only; retain paragraph breaks in chunks.
- Final chunk: may be shorter than 1,500 characters.
- Overlong sentence: stop and ask for a manual rewrite or an explicitly larger
  limit. Do not split it mid-sentence.

The output schema is:

```json
{
  "schema_version": 1,
  "source_file": "cleaned_transcript.txt",
  "source_sha256": "...",
  "limits": {"min_chars": 1500, "target_chars": 2000, "max_chars": 2500},
  "chunks": [
    {"chunk_index": 1, "char_count": 1975, "text": "..."}
  ]
}
```

## English TTS production

Use `eleven_flash_v2` as the default English-only batch model. Current official
documentation identifies it as English-only and lists a 30,000-character
request limit. Keep operational chunks much smaller so selective regeneration
and quality review remain practical.

When expressive performance matters more than batch throughput, offer a short
pilot with `eleven_v3` and let the user choose based on listening quality. Do
not select a multilingual model for this English-only workflow. Preserve any
explicit user model choice that supports English TTS.

Keep the English voice, model, output format, voice settings, seed, and
pronunciation dictionary consistent across a production unless a reviewed
change is intentional. The ElevenLabs documentation indicates phoneme-based
pronunciation dictionaries work with `eleven_flash_v2` and `eleven_v3`.

Use the TTS endpoint's `previous_text` and `next_text` fields to improve
continuity across separately generated chunks. Generate sequentially by default.
Do not assume a failed request is free; avoid unbounded automatic retries.

The bundled generator is a dry run unless `--execute` is present. It reads the
API key only from `ELEVENLABS_API_KEY` and the voice from `--voice-id` or
`ELEVENLABS_VOICE_ID`. Examples:

```bash
# Plan only; sends no API requests.
python3 scripts/generate_tts.py chunks.json --output-dir audio_parts

# Paid English generation after approval.
python3 scripts/generate_tts.py chunks.json --output-dir audio_parts \
  --voice-id "$ELEVENLABS_VOICE_ID" --execute

# Regenerate only chunk 4 after review; overwrites that derived part only.
python3 scripts/generate_tts.py chunks.json --output-dir audio_parts \
  --only 4 --overwrite --execute
```

## Duration and pronunciation

Estimate runtime from English word count and the requested delivery pace, not
from a fixed characters-per-minute assumption. Punctuation, numbers, acronyms,
and voice settings materially affect duration. Measure every generated part and
the master audio before aligning visuals.

Pilot pronunciation with terminology-dense sections first. Correct the text or
pronunciation dictionary and regenerate only affected chunks. Listen across
chunk boundaries; adjacent-text context does not guarantee a seamless join.
