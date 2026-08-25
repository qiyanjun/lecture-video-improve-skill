# lecture-video-improve-skill

A Claude Code plugin (and standalone script toolkit) for turning raw lecture recordings into polished, narrated videos: transcript cleanup, TTS re-narration (Fish Audio or ElevenLabs), FFmpeg assembly, thumbnails/intro cards, an optional spoken welcome message, and YouTube publishing — orchestrated for one video or a whole series at once.

The actual plugin lives in [`lecture-video-improver/`](./lecture-video-improver) — see [its README](./lecture-video-improver/README.md) for full documentation (setup, API keys, voice ID selection, job manifest schema, every bundled script, and the version history). This root README only covers **installing and running it**.

## Install in Claude Code

Verified end-to-end with `claude plugin validate`, a live `marketplace add` → `install` → `details` → `uninstall` cycle, and `claude plugin details` confirming all 4 components (2 skills, 2 commands) load correctly.

**Option A — plugin marketplace (recommended):**

```
/plugin marketplace add qiyanjun/lecture-video-improve-skill
/plugin install lecture-video-improver@lecture-video-improve-skill
```

The marketplace registers under the `name` declared in this repo's `.claude-plugin/marketplace.json` — currently `lecture-video-improve-skill` — so the command above is exact, not a guess. If it's ever renamed, `/plugin marketplace list` after the `add` step shows the current name to use in the `install` command.

This tracks the repo, so `/plugin update lecture-video-improver` picks up future releases automatically.

**Option B — manual install (no marketplace, just the files):**

```bash
git clone https://github.com/qiyanjun/lecture-video-improve-skill.git /tmp/lecture-video-improve-skill
cp -R /tmp/lecture-video-improve-skill/lecture-video-improver ~/.claude/skills/lecture-video-improver
```

Claude Code auto-discovers anything dropped into `~/.claude/skills/` on the next session — no further registration needed. Note the `cp -R .../lecture-video-improver ...` — the plugin lives in that subfolder of the repo, not at the repo root, so a plain top-level clone into `~/.claude/skills/` won't have the right layout.

**Don't use both options for the same install.** They're alternatives, not additive — if a skills-dir copy (Option B) and a marketplace-installed copy (Option A) both exist under the same plugin name, the marketplace-installed one takes precedence and the skills-dir copy is silently not loaded (`claude plugin list` will show it as `✘ Not loaded`, name already taken). Pick one.

## How this works

**Distribution: a plain GitHub repo, not a centralized marketplace.** This repo isn't listed in Anthropic's official plugin marketplace — nobody discovers it by browsing. Sharing it just means giving someone the repo URL and the `marketplace add` command above; there's no submission or approval step, and anyone can add any public git repo as a marketplace source this same way.

**From `marketplace add` to a running skill:**
1. `/plugin marketplace add owner/repo` fetches this repo and reads the root `.claude-plugin/marketplace.json`, registering it as a marketplace under the name it declares (`lecture-video-improve-skill`).
2. `/plugin install lecture-video-improver@lecture-video-improve-skill` reads that marketplace entry's `source` — a `git-subdir` sparse checkout that pulls just the `lecture-video-improver/` subfolder, not the whole repo — and installs it locally.
3. Claude Code scans the installed folder for `skills/*/SKILL.md` and `commands/*.md`, registering each as a component (this plugin: 2 skills, 2 commands).
4. Each session, only a skill's name and one-line description load into context by default (cheap — a few hundred tokens total); the full `SKILL.md` — the actual workflow instructions — only loads when a request matches that skill closely enough to trigger it.
5. Once triggered, the agent follows `SKILL.md`'s instructions and executes the bundled Python scripts directly (TTS API calls, `ffmpeg` assembly, etc.) via its normal shell access.
6. `/plugin update lecture-video-improver` re-fetches from the tracked repo/ref to pick up new commits.

**Everything after step 1 runs locally.** The install step is a plain `git` fetch to the user's own machine; their marketplace/plugin registrations live in their own local Claude Code config — never synced or shared between users. At runtime, the only outbound network calls are whatever the scripts themselves make (Fish Audio, ElevenLabs, OpenAI), using the user's own API keys — nothing about this plugin mechanism routes through Anthropic's infrastructure.

## Using it once installed

Just describe what you want in normal conversation — the skill loads automatically on phrases like:
- "Build a voiceover for this video using Fish Audio"
- "Batch-produce 70 narrated videos from my transcripts"
- "Clean my transcript and generate TTS audio"
- "Trim filler sections from my talking-head recording"
- "Improve the English in this script" / "Improve this subtitle file"

Or invoke the bundled commands directly: `/improve-script [file]`, `/improve-subtitles [file]`.

Before generating anything real, the assistant will walk you through the required setup — an API key for your chosen TTS provider (`FISH_API_KEY` or `ELEVENLABS_API_KEY`) and a voice ID (reuse one you have, search Fish Audio's library by name, or clone one from a sample). Full details in the [plugin README's Setup section](./lecture-video-improver/README.md#setup--api-keys-voice-id-and-job-inputs).

## Using it with other LLM coding agents

The plugin's `/plugin` auto-install and skill-trigger mechanism above is Claude Code-specific. But the plugin itself is just:
- `skills/*/SKILL.md` — plain-text, agent-readable instructions (what to do, in what order, and why)
- `scripts/*.py` — standalone Python scripts, runnable directly (`python3 scripts/run_stage1_improve.py job_manifest.json`), no Claude Code dependency
- `commands/*.md` — slash-command prompts, readable as plain instructions

Any coding agent that can read a markdown instructions file and execute Python/`ffmpeg` on your behalf can use this toolkit — point it at `lecture-video-improver/skills/build-video-voiceovers/SKILL.md` (or `skills/english-improvement/SKILL.md`) and let it follow the workflow described there, calling the scripts in `lecture-video-improver/scripts/` directly.

## License

[MIT](./LICENSE)
