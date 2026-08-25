---
description: Improve English in a video script, outline, or plain-text transcript
argument-hint: [file-path]
allowed-tools: Read, Write
---

Read the file at @$1.

This file is a video script, outline, or transcript. Apply the english-improvement skill to improve the English throughout. Address all five improvement types: grammar & punctuation, natural fluency, vocabulary & word choice, clarity & conciseness, and tone.

**Rules:**
- Preserve the speaker's voice and intent — improve, don't rewrite from scratch
- Keep the same structure, headings, and paragraph/line breaks as the original
- Do not add or remove content sections
- Since this will be spoken aloud, keep language natural and conversational, not overly formal
- Use contractions where appropriate (I'll, you'll, we're, etc.)

**What to do:**

1. Improve the full text
2. Save the corrected version to the same directory as the input file, with `-improved` appended before the file extension
   - Example: `my-script.txt` → `my-script-improved.txt`
3. Save a changelog to the same directory with `-changes` appended before the file extension
   - Example: `my-script.txt` → `my-script-changes.txt`
   - Format the changelog like this:

```
# Changes — [original filename]

## Grammar & Punctuation
- "[original]" → "[fixed]"
  Reason: ...

## Natural Fluency
- "[original]" → "[fixed]"
  Reason: ...

## Vocabulary & Word Choice
- "[original]" → "[fixed]"
  Reason: ...

## Clarity & Conciseness
- "[original]" → "[fixed]"
  Reason: ...

## Tone
- "[original]" → "[fixed]"
  Reason: ...
```

Only include sections where changes were made. Limit to the most significant 3–5 examples per section.

4. After saving, tell the user:
   - Where the improved file and changelog were saved
   - A one-paragraph plain-English summary of the main improvements made
