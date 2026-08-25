#!/usr/bin/env python3
"""Generate thumbnail and intro card for a video.

Two card styles:
  basic (default) -- a flat or two-color gradient background, drawn locally
    with Pillow, no API call, no cost, fully deterministic (same title in ->
    pixel-identical card out, every time). This is what a lecture SERIES
    wants: 70 videos with the exact same visual identity, differing only in
    the title text -- not 70 independently AI-generated backgrounds that
    each look different. --bg-color/--bg-color2/--accent-color/--text-color/
    --eyebrow define the brand once; pass the same values for every job in
    the series (the manifest-level fields on run_stage1_improve.py do this
    for you -- see that script's docstring).
  ai -- a background image via OpenAI gpt-image-1.5 from a --theme prompt,
    with the title overlaid on top. Real cost (~$0.034/image at medium
    quality), and -- because it's a fresh generation per video -- no two
    cards look alike unless --theme is held identical across every job.
    Better suited to a single video than a series that needs to look like
    one product.

Both styles: 2. Overlay the title (and optional subtitle) programmatically
using Pillow. 3. Save thumbnail.jpg and intro_card.png at the same size.

Environment variables:
  OPENAI_API_KEY   Required for --execute with --card-style ai. Not used by basic.

Requirements:
  pip install pillow requests
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

OPENAI_IMAGE_URL = "https://api.openai.com/v1/images/generations"
DEFAULT_MODEL = "gpt-image-1.5"
THUMBNAIL_SIZE = (1536, 864)   # 16:9 landscape

# Basic-style defaults -- a professional, low-key academic look. Deep navy
# fading to near-black, with a neutral teal accent. Not tied to any specific
# institution's brand -- override all of these via CLI flags (or the
# manifest's card_bg_color/card_accent_color/etc.) to fit your own series.
DEFAULT_BG_COLOR = "#16213E"
DEFAULT_BG_COLOR2 = "#0B1120"
DEFAULT_ACCENT_COLOR = "#2DD4BF"
DEFAULT_TEXT_COLOR = "#FFFFFF"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--title", required=True, help="Video title text to overlay")
    parser.add_argument("--subtitle", default="", help="Optional subtitle text")
    parser.add_argument(
        "--card-style", default="basic", choices=["basic", "ai"],
        help="'basic' (default): flat/gradient background, no API call, consistent across a "
             "series. 'ai': OpenAI-generated background from --theme, real cost, varies per call.",
    )
    parser.add_argument(
        "--eyebrow", default="",
        help="[basic style] Small label above the title, e.g. a course code -- set once, reused "
             "for every video in a series to keep the brand consistent.",
    )
    parser.add_argument("--bg-color", default=DEFAULT_BG_COLOR, help="[basic style] Background color (hex)")
    parser.add_argument(
        "--bg-color2", default=DEFAULT_BG_COLOR2,
        help="[basic style] Second color for a top-to-bottom gradient. Pass the same value as "
             "--bg-color for a flat fill instead.",
    )
    parser.add_argument("--accent-color", default=DEFAULT_ACCENT_COLOR, help="[basic style] Accent rule/eyebrow color (hex)")
    parser.add_argument("--text-color", default=DEFAULT_TEXT_COLOR, help="[basic style] Title/subtitle color (hex)")
    parser.add_argument(
        "--theme",
        default="",
        help="[ai style, required for it] Visual theme / subject description for background prompt",
    )
    parser.add_argument(
        "--output-dir", type=Path, required=True, help="Directory to save assets/"
    )
    parser.add_argument(
        "--model", default=DEFAULT_MODEL, help=f"[ai style] OpenAI image model (default: {DEFAULT_MODEL})"
    )
    parser.add_argument(
        "--quality",
        default="medium",
        choices=["low", "medium", "high"],
        help="[ai style] Image quality tier (default: medium ~$0.034/image)",
    )
    parser.add_argument(
        "--style",
        default="",
        help="[ai style] Additional style instructions appended to the prompt",
    )
    parser.add_argument(
        "--target-size", default=None,
        help=f"Final WIDTHxHEIGHT for both saved images, e.g. '1920x1080' to match the source "
             f"video exactly -- prevents a resolution mismatch when the intro card gets "
             f"concatenated onto the video later. Default: {THUMBNAIL_SIZE[0]}x{THUMBNAIL_SIZE[1]} "
             f"(only really matters if nothing downstream will scale this to fit).",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="[ai style] Send paid API requests (dry run by default). No-op for basic style, "
             "which always executes -- there's nothing to pay for or approve.",
    )
    parser.add_argument("--timeout", type=float, default=120.0)
    return parser.parse_args()


def build_prompt(theme: str, style: str, asset_type: str) -> str:
    base = (
        f"Professional video {asset_type} background image. "
        f"Theme: {theme}. "
        "Clean, modern, visually compelling composition. "
        "No text, no letters, no words, no watermarks anywhere in the image. "
        "High contrast areas suitable for overlaid title text. "
    )
    if style:
        base += style
    return base.strip()


def generate_image_bytes(
    api_key: str,
    prompt: str,
    model: str,
    quality: str,
    timeout: float,
) -> bytes:
    payload = {
        "model": model,
        "prompt": prompt,
        "n": 1,
        "size": "1536x1024",
        "quality": quality,
        "output_format": "jpeg",
    }
    request = urllib.request.Request(
        OPENAI_IMAGE_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "User-Agent": "lecture-video-improver-plugin/1",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            result = json.loads(response.read())
    except urllib.error.HTTPError as error:
        detail = error.read(2000).decode("utf-8", errors="replace")
        raise RuntimeError(f"OpenAI image HTTP {error.code}: {detail}") from error
    except urllib.error.URLError as error:
        raise RuntimeError(f"OpenAI image request failed: {error.reason}") from error

    b64 = result["data"][0].get("b64_json")
    if not b64:
        raise RuntimeError("OpenAI returned no image data.")
    return base64.b64decode(b64)


def hex_to_rgb(value: str) -> tuple[int, int, int]:
    value = value.lstrip("#")
    if len(value) != 6:
        raise ValueError(f"Expected a 6-digit hex color like '#16213E', got: {value!r}")
    return tuple(int(value[i:i + 2], 16) for i in (0, 2, 4))


def load_font(size_pt: int, bold: bool = False) -> "ImageFont.FreeTypeFont":
    from PIL import ImageFont
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
        "C:/Windows/Fonts/arialbd.ttf",
    ] if bold else [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
        "C:/Windows/Fonts/arial.ttf",
    ]
    for path in candidates:
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size_pt)
            except Exception:
                continue
    return ImageFont.load_default()


def wrap_text(draw: "ImageDraw.ImageDraw", text: str, font, max_width: int) -> list[str]:
    """Greedy word-wrap to fit max_width, using actual rendered text width
    (not a character-count guess -- title text is proportional, so that
    would wrap wrong for anything with a lot of narrow or wide letters)."""
    words = text.split()
    if not words:
        return [""]
    lines: list[str] = []
    current = words[0]
    for word in words[1:]:
        candidate = f"{current} {word}"
        if draw.textlength(candidate, font=font) <= max_width:
            current = candidate
        else:
            lines.append(current)
            current = word
    lines.append(current)
    return lines


def render_basic_card(
    title: str,
    subtitle: str,
    eyebrow: str,
    bg_color: str,
    bg_color2: str,
    accent_color: str,
    text_color: str,
    output_path: Path,
    size: tuple[int, int],
) -> None:
    """The fixed-design-per-series card: a two-color vertical gradient (pass
    bg_color == bg_color2 for a flat fill), one accent-color rule, an
    optional small eyebrow label, then title + subtitle. Every argument
    except title/subtitle/output_path should be IDENTICAL across every job
    in a series -- that sameness is the entire point, it's what makes 70
    videos read as one product instead of 70 independent ones."""
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        raise RuntimeError(
            "Pillow is required for card rendering. Install with: pip install pillow"
        )

    w, h = size
    bg1 = hex_to_rgb(bg_color)
    bg2 = hex_to_rgb(bg_color2)
    accent = hex_to_rgb(accent_color)
    text = hex_to_rgb(text_color)

    img = Image.new("RGB", (w, h), bg1)
    if bg1 != bg2:
        for y in range(h):
            t = y / max(h - 1, 1)
            row = tuple(int(bg1[i] + (bg2[i] - bg1[i]) * t) for i in range(3))
            ImageDraw.Draw(img).line([(0, y), (w, y)], fill=row)
    draw = ImageDraw.Draw(img)

    margin = int(w * 0.07)

    # One accent rule above the text block -- the single consistent visual
    # anchor every card in the series shares, regardless of title length.
    rule_y = int(h * 0.58)
    rule_w = int(w * 0.10)
    draw.rectangle([margin, rule_y, margin + rule_w, rule_y + max(int(h * 0.008), 3)], fill=accent)

    text_top = rule_y + int(h * 0.05)

    if eyebrow:
        eyebrow_font = load_font(int(h * 0.032), bold=True)
        draw.text((margin, text_top), eyebrow.upper(), font=eyebrow_font, fill=accent)
        text_top += int(h * 0.06)

    # Titles vary a lot across a 70-video series -- shrink to fit up to 2
    # lines before accepting more, rather than clipping off the right edge.
    max_text_width = w - 2 * margin
    title_size = int(h * 0.075)
    min_title_size = int(h * 0.04)
    while True:
        title_font = load_font(title_size, bold=True)
        title_lines = wrap_text(draw, title, title_font, max_text_width)
        if len(title_lines) <= 2 or title_size <= min_title_size:
            break
        title_size = max(int(title_size * 0.88), min_title_size)

    line_height = int(title_size * 1.22)
    for i, line in enumerate(title_lines):
        draw.text((margin, text_top + i * line_height), line, font=title_font, fill=text)
    title_block_bottom = text_top + len(title_lines) * line_height

    if subtitle:
        subtitle_y = title_block_bottom + int(h * 0.025)
        subtitle_font = load_font(int(h * 0.04))
        muted = tuple(int(c * 0.75) for c in text)
        draw.text((margin, subtitle_y), subtitle, font=subtitle_font, fill=muted)

    ext = output_path.suffix.lower()
    fmt = "JPEG" if ext in {".jpg", ".jpeg"} else "PNG"
    img.save(output_path, fmt, quality=92)


def overlay_text(
    image_bytes: bytes,
    title: str,
    subtitle: str,
    output_path: Path,
    size: tuple[int, int],
) -> None:
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        raise RuntimeError(
            "Pillow is required for text overlay. Install with: pip install pillow"
        )

    import io
    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    img = img.resize(size, Image.LANCZOS)
    draw = ImageDraw.Draw(img)
    w, h = size

    # Semi-transparent dark bar at the bottom for text legibility
    overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    bar_h = int(h * 0.38)
    bar = Image.new("RGBA", (w, bar_h), (0, 0, 0, 160))
    overlay.paste(bar, (0, h - bar_h))
    img = Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")
    draw = ImageDraw.Draw(img)

    margin = int(w * 0.06)
    title_font = load_font(int(h * 0.072), bold=True)
    subtitle_font = load_font(int(h * 0.042))

    # Title
    title_y = h - bar_h + int(bar_h * 0.18)
    draw.text((margin, title_y), title, font=title_font, fill=(255, 255, 255))

    # Subtitle
    if subtitle:
        subtitle_y = title_y + int(h * 0.09)
        draw.text((margin, subtitle_y), subtitle, font=subtitle_font, fill=(220, 220, 220))

    ext = output_path.suffix.lower()
    fmt = "JPEG" if ext in {".jpg", ".jpeg"} else "PNG"
    img.save(output_path, fmt, quality=92)


def main() -> int:
    args = parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    target_size = THUMBNAIL_SIZE
    if args.target_size:
        try:
            w, h = args.target_size.lower().split("x")
            target_size = (int(w), int(h))
        except ValueError:
            print(f"--target-size must be WIDTHxHEIGHT, got: {args.target_size!r}", file=sys.stderr)
            return 2

    thumbnail_path = args.output_dir / "thumbnail.jpg"
    intro_path = args.output_dir / "intro_card.png"
    assets: list[tuple[str, Path]] = [
        ("thumbnail", thumbnail_path),
        ("intro card", intro_path),
    ]

    if args.card_style == "basic":
        # No API call, no cost, no dry-run gate needed -- there's nothing to
        # approve. Deterministic: identical arguments produce a pixel-identical
        # card, which is the point for a series that should look like one thing.
        print(
            f"Rendering 2 images at {target_size[0]}x{target_size[1]} (basic style, no cost): "
            f"bg={args.bg_color}->{args.bg_color2}, accent={args.accent_color}"
        )
        print(f"  thumbnail → {thumbnail_path}")
        print(f"  intro card → {intro_path}")

        manifest = {
            "schema_version": 1,
            "card_style": "basic",
            "title": args.title,
            "subtitle": args.subtitle,
            "eyebrow": args.eyebrow,
            "bg_color": args.bg_color,
            "bg_color2": args.bg_color2,
            "accent_color": args.accent_color,
            "text_color": args.text_color,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "assets": [],
        }
        for asset_type, out_path in assets:
            try:
                render_basic_card(
                    args.title, args.subtitle, args.eyebrow,
                    args.bg_color, args.bg_color2, args.accent_color, args.text_color,
                    out_path, target_size,
                )
            except (RuntimeError, ValueError) as error:
                print(str(error), file=sys.stderr)
                return 1
            print(f"  Saved: {out_path}")
            manifest["assets"].append({"type": asset_type, "path": str(out_path)})

    else:  # card_style == "ai"
        if not args.theme:
            print("--theme is required with --card-style ai.", file=sys.stderr)
            return 2

        quality_cost = {"low": 0.009, "medium": 0.034, "high": 0.133}
        cost_per = quality_cost.get(args.quality, 0.034)
        print(
            f"Plan: 2 images at {target_size[0]}x{target_size[1]} ({args.quality} quality, {args.model}), "
            f"estimated cost ~${cost_per * 2:.3f}."
        )
        print(f"  thumbnail → {thumbnail_path}")
        print(f"  intro card → {intro_path}")

        if not args.execute:
            print("Dry run only: no API requests sent. Add --execute after approval.")
            return 0

        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            print("OPENAI_API_KEY is not set.", file=sys.stderr)
            return 2

        manifest = {
            "schema_version": 1,
            "card_style": "ai",
            "model": args.model,
            "quality": args.quality,
            "title": args.title,
            "subtitle": args.subtitle,
            "theme": args.theme,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "assets": [],
        }

        for asset_type, out_path in assets:
            prompt = build_prompt(args.theme, args.style, asset_type)
            print(f"Generating {asset_type}...")
            try:
                image_bytes = generate_image_bytes(
                    api_key, prompt, args.model, args.quality, args.timeout
                )
            except RuntimeError as error:
                print(str(error), file=sys.stderr)
                return 1

            try:
                overlay_text(image_bytes, args.title, args.subtitle, out_path, target_size)
            except RuntimeError as error:
                print(str(error), file=sys.stderr)
                # Save raw image even if overlay fails
                out_path.write_bytes(image_bytes)
                print(f"  Saved raw image (overlay failed): {out_path}")
            else:
                print(f"  Saved: {out_path}")

            manifest["assets"].append(
                {"type": asset_type, "path": str(out_path), "prompt": prompt}
            )

    manifest_path = args.output_dir / "image_manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"Image manifest: {manifest_path}")

    # ── Inline QA: verify output files ───────────────────────────────────────
    qa_issues: list[str] = []
    for out_path in [thumbnail_path, intro_path]:
        if not out_path.exists():
            qa_issues.append(f"  [QA FAIL] Missing output: {out_path.name}")
            continue
        size = out_path.stat().st_size
        if size < 10_000:
            qa_issues.append(
                f"  [QA WARN] {out_path.name}: suspiciously small ({size} bytes) — may be corrupt."
            )
        try:
            from PIL import Image
            with Image.open(out_path) as img:
                w, h = img.size
                if w < 640 or h < 360:
                    qa_issues.append(
                        f"  [QA WARN] {out_path.name}: low resolution {w}x{h}."
                    )
                else:
                    print(f"QA: {out_path.name} — {w}x{h} px, {size:,} bytes. OK.")
        except ImportError:
            print(f"QA: {out_path.name} — {size:,} bytes (Pillow not installed; skipping dimension check).")
        except Exception as e:
            qa_issues.append(f"  [QA WARN] {out_path.name}: could not read image ({e}).")

    if qa_issues:
        print("\nQA Warnings:")
        for issue in qa_issues:
            print(issue)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
