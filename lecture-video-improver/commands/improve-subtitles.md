---
description: Improve English in a subtitle file (.srt or .vtt) while preserving all timing codes
argument-hint: [subtitle-file-path]
allowed-tools: Read, Write
---

Read the subtitle file at @$1.

This is a subtitle file in SRT or VTT format. Apply the english-improvement skill to the subtitle text — but you must leave all timing codes, sequence numbers, and formatting markers completely unchanged.

**File format reference:**

SRT example — only edit lines that are plain text (not numbers or arrows):
```
1
00:00:01,000 --> 00:00:04,000
This is the subtitle text to improve here.

2
00:00:05,000 --> 00:00:08,500
Another subtitle block goes here.
```

VTT example — only edit plain text lines, never the header or timing lines:
```
WEBVTT

00:00:01.000 --> 00:00:04.000
This is the subtitle text to improve here.

00:00:05.000 --> 00:00:08.500
Another subtitle block goes here.
```

**Strict rules:**
- ONLY edit the subtitle text lines — never touch timing lines, sequence numbers, or the WEBVTT header
- Keep improved text roughly the same length as the original — subtitle space is limited
- Prefer natural spoken English; avoid formal or academic phrasing
- Do not merge, split, or reorder subtitle blocks
- Do not change blank lines between blocks

**What to do:**

1. Go through every subtitle block and improve the English text
2. Save the corrected subtitle file to the same directory with `-improved` appended before the file extension
   - Example: `video.srt` → `video-improved.srt`
   - Example: `captions.vtt` → `captions-improved.vtt`
3. After saving, tell the user:
   - Where the improved file was saved
   - How many subtitle blocks were edited out of the total
   - The main types of improvements made (e.g. "Fixed grammar in 12 blocks, improved fluency in 8 blocks")
