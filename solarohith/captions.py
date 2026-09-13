from pathlib import Path
import re


def _ass_time(seconds: float) -> str:
    seconds = max(0.0, float(seconds))
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    cs = int(round((seconds - int(seconds)) * 100))
    if cs >= 100:
        cs -= 100
        s += 1
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _escape(text: str) -> str:
    # ASS override tags use braces, so escape literal braces from ASR text.
    return (
        str(text or "")
        .replace("\\", r"\\")
        .replace("{", r"\{")
        .replace("}", r"\}")
        .replace("\n", " ")
        .strip()
    )


def _word_list(transcript, start, end):
    words = []
    for seg in transcript.get("segments", []):
        for word in seg.get("words", []) or []:
            try:
                ws = float(word["start"])
                we = float(word["end"])
            except (KeyError, TypeError, ValueError):
                continue
            if we <= start or ws >= end:
                continue
            text = _escape(word.get("word", ""))
            if not text:
                continue
            words.append({
                "start": max(start, ws),
                "end": min(end, we),
                "text": text,
            })
    return [w for w in words if w["end"] > w["start"]]


def _plain_word(text):
    return re.sub(r"[^A-Za-z0-9']+", "", text).lower()


def _groups(words, max_chars=28, max_words=6, max_lines=2):
    """
    Build short social-caption beats.

    A new beat is started on:
      - a noticeable pause;
      - punctuation ending a sentence/phrase;
      - the word/character limits.

    The returned groups are still timed word-by-word, so the active word
    animation stays synchronized to Whisper.
    """
    groups = []
    current = []
    chars = 0

    def flush():
        nonlocal current, chars
        if current:
            groups.append(current)
        current = []
        chars = 0

    for i, word in enumerate(words):
        prev = words[i - 1] if i else None
        token = word["text"]
        extra = len(token) + (1 if current else 0)

        pause = (
            prev is not None
            and word["start"] - prev["end"] >= 0.32
        )
        punctuation_break = bool(
            re.search(r"[.!?]$", prev["text"]) if prev else False
        )
        hard_limit = (
            current
            and (chars + extra > max_chars or len(current) >= max_words)
        )

        if current and (pause or punctuation_break or hard_limit):
            flush()

        current.append(word)
        chars += len(token) + (1 if len(current) > 1 else 0)

    flush()

    # Avoid tiny one-word groups when they can naturally join the next beat.
    merged = []
    for group in groups:
        if (
            merged
            and len(group) == 1
            and len(merged[-1]) < max_words
            and len(" ".join(w["text"] for w in merged[-1] + group)) <= max_chars
            and group[0]["start"] - merged[-1][-1]["end"] < 0.22
        ):
            merged[-1].extend(group)
        else:
            merged.append(group)

    return merged


def _split_display_lines(group, max_line_chars=16):
    """Split a caption beat into at most two visually balanced lines."""
    if len(group) <= 1:
        return [group]

    total = len(" ".join(w["text"] for w in group))
    if total <= max_line_chars:
        return [group]

    best = None
    for cut in range(1, len(group)):
        left = " ".join(w["text"] for w in group[:cut])
        right = " ".join(w["text"] for w in group[cut:])
        if len(left) <= max_line_chars and len(right) <= max_line_chars:
            score = abs(len(left) - len(right))
            if best is None or score < best[0]:
                best = (score, cut)

    if best:
        cut = best[1]
        return [group[:cut], group[cut:]]

    # If a single token is longer than the visual limit, keep the beat intact.
    mid = len(group) // 2
    return [group[:mid], group[mid:]] if mid else [group]


def _tag_color(color):
    return color if str(color).startswith("&H") else str(color)


def make_ass(
    transcript,
    clip_start,
    clip_end,
    output,
    width=1080,
    height=1920,
    font_name="Arial",
    font_size=76,
    normal="&H00FFFFFF",
    highlight="&H00FF66CC",
    outline="&H00000000",
    outline_width=6,
    shadow=3,
    margin_v=470,
    pop_scale=112,
    max_words=6,
    max_chars=28,
    max_line_chars=18,
    pop_ms=110,
):
    """
    Create polished word-synchronous social captions.

    Each caption beat has a normal base event. During every spoken word, a
    second event overlays the same beat and:
      - changes the active word colour;
      - briefly scales it up;
      - adds a slightly heavier outline.

    All timing comes directly from Whisper's word timestamps.
    """
    words = _word_list(transcript, clip_start, clip_end)
    groups = _groups(
        words,
        max_chars=max_chars,
        max_words=max_words,
    )

    header = f"""[Script Info]
ScriptType: v4.00+
ScriptVersion: 4.00+
PlayResX: {width}
PlayResY: {height}
ScaledBorderAndShadow: yes
WrapStyle: 2
Collisions: Normal

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: SCAG,{font_name},{font_size},{_tag_color(normal)},{_tag_color(normal)},{_tag_color(outline)},&H00000000,-1,0,0,0,100,100,0,0,1,{outline_width},{shadow},2,70,70,{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

    events = []

    for group in groups:
        if not group:
            continue

        line_start = group[0]["start"]
        line_end = group[-1]["end"]

        # Keep the same line layout for the base and active overlays.
        lines = _split_display_lines(group, max_line_chars=max_line_chars)

        # Build a single centered ASS event with an explicit line break.
        line_text = r"\N".join(
            " ".join(w["text"] for w in line)
            for line in lines
        )

        events.append(
            f"Dialogue: 0,{_ass_time(line_start-clip_start)},"
            f"{_ass_time(line_end-clip_start)},SCAG,,0,0,0,,"
            f"{line_text}"
        )

        # Active-word overlays. Each overlay contains the full caption beat,
        # but only the currently spoken token receives the highlight tags.
        for idx, active in enumerate(group):
            ws, we = active["start"], active["end"]
            if we <= ws:
                continue

            active_text = []
            for line in lines:
                line_parts = []
                for w in line:
                    # Match by object identity; group objects are reused in lines.
                    if w is active:
                        line_parts.append(
                            "{"
                            + r"\c" + _tag_color(highlight)
                            + r"\fscx100\fscy100"
                            + r"\bord" + str(int(outline_width + 1))
                            + r"\t(0," + str(int(pop_ms))
                            + r",\fscx" + str(int(pop_scale))
                            + r"\fscy" + str(int(pop_scale)) + ")"
                            + r"\t(" + str(int(pop_ms)) + "," + str(int(pop_ms + 90))
                            + r",\fscx100\fscy100)"
                            + "}"
                            + w["text"]
                            + "{\\rSCAG}"
                        )
                    else:
                        line_parts.append(w["text"])
                active_text.append(" ".join(line_parts))

            animated = r"\N".join(active_text)
            events.append(
                f"Dialogue: 1,{_ass_time(ws-clip_start)},"
                f"{_ass_time(we-clip_start)},SCAG,,0,0,0,,"
                f"{animated}"
            )

    Path(output).parent.mkdir(parents=True, exist_ok=True)
    Path(output).write_text(header + "\n".join(events) + "\n", encoding="utf-8")
    return output
