import base64,json,mimetypes
from pathlib import Path
import requests

SYSTEM="""You are the senior editor for gaming creator Solarohith.
You are NOT a keyword detector. You are given the COMPLETE timestamped stream
transcript plus candidate leads and, in the visual pass, representative video
frames. You are running locally through Ollama using a vision-language model. Use context, setup/payoff, humor, reactions, skill, tension, novelty
and whether a new viewer can understand the moment. Candidates are leads, not
truth. Return ONLY valid JSON."""

def data_url(p):
    p=Path(p); mime=mimetypes.guess_type(p.name)[0] or "image/jpeg"
    return f"data:{mime};base64,{base64.b64encode(p.read_bytes()).decode()}"

def call(cfg,prompt,images=None,timeout=None):
    a=cfg["ai"]
    content=[{"type":"text","text":prompt}]
    for p in images or []:
        content.append({"type":"image_url","image_url":{"url":data_url(p)}})
    # Ollama OpenAI-compatible endpoint accepts image_url data URLs for vision models.
    r=requests.post(
        a["base_url"].rstrip("/")+"/chat/completions",
        headers={"Authorization":f"Bearer {a.get('api_key','ollama')}"},
        json={
            "model":a["model"],
            "temperature":a.get("temperature",.1),
            "messages":[
                {"role":"system","content":SYSTEM},
                {"role":"user","content":content}
            ],
            "keep_alive": "10m"
        },
        timeout=timeout or a.get("timeout_seconds",900),
    )
    r.raise_for_status()
    text=r.json()["choices"][0]["message"]["content"].strip()
    if text.startswith("```"):
        text=text.replace("```json","").replace("```","").strip()
    try:
        return json.loads(text)
    except Exception:
        x,y=text.find("{"),text.rfind("}")
        if x>=0 and y>x:return json.loads(text[x:y+1])
        raise ValueError("Invalid JSON from Qwen:\n"+text)

def _candidate_payload(candidates):
    keys=["candidate_id","event_time","search_start","search_end",
          "signal_strength","signals","evidence","transcript_context"]
    return [{k:c[k] for k in keys if k in c} for c in candidates]

def _temporal_diverse(candidates, limit, min_gap=45.0):
    """Pick high-quality candidates while preventing one event from monopolizing the shortlist."""
    ranked = sorted(candidates, key=lambda x: x.get("signal_strength", 0), reverse=True)
    chosen = []
    for c in ranked:
        t = float(c.get("event_time", c.get("search_start", 0)))
        if all(abs(t - float(x.get("event_time", x.get("search_start", 0)))) >= min_gap for x in chosen):
            chosen.append(c)
            if len(chosen) >= limit:
                break
    # Fill remaining slots if the stream has fewer well-separated events.
    if len(chosen) < limit:
        used = {c["candidate_id"] for c in chosen}
        for c in ranked:
            if c["candidate_id"] not in used:
                chosen.append(c)
                if len(chosen) >= limit:
                    break
    return chosen


def decide(cfg, full, candidates, frames):
    """Full-transcript editorial pass with broad candidate coverage.

    The COMPLETE timestamped transcript is sent to both Qwen stages.
    Stage 1 ranks the complete candidate index (compact metadata only).
    Stage 2 receives a larger, temporally diverse shortlist plus visual
    evidence. This prevents the old top-8 heuristic gate from reducing a
    multi-hour stream to one local event.
    """
    analysis = cfg["analysis"]
    candidate_limit = max(1, int(analysis.get("qwen_candidate_review_limit", 80)))
    shortlist_n = max(
        8,
        min(
            candidate_limit,
            int(analysis.get("qwen_text_shortlist", 24)),
            len(candidates),
        ),
    )

    # IMPORTANT: send every candidate's metadata, but NOT its repeated
    # transcript_context. The full transcript is already supplied separately.
    compact_payload = [
        {
            "candidate_id": c["candidate_id"],
            "event_time": c.get("event_time"),
            "search_start": c.get("search_start"),
            "search_end": c.get("search_end"),
            "signal_strength": c.get("signal_strength"),
            "signals": c.get("signals"),
            "evidence": c.get("evidence", [])[:4],
        }
        for c in candidates[:candidate_limit]
    ]

    prompt1 = f"""You are reviewing a COMPLETE {analysis.get('stream_hours_hint', 'livestream')}.

Read the ENTIRE timestamped transcript below before ranking anything.
The candidate windows are only leads. They are deliberately high-recall and
may include boring gameplay.

Return ONLY:
{{"shortlist":[{{"candidate_id":1,"score":96,"reason":"brief reason"}}]}}

Select up to {shortlist_n} genuinely promising moments from ACROSS THE WHOLE
STREAM. Do not restrict yourself to the first candidates or the highest
heuristic signal. Prefer temporal diversity: do not select many windows from
the same minute/event when better moments exist elsewhere.

Use setup/payoff across the transcript. Consider humor, reactions, rage,
clutch/skill, surprise, tension, strong opinions, storytelling and standalone
viewer value. Ordinary gameplay should be rejected.

=== COMPLETE TIMESTAMPED TRANSCRIPT - MASTER REFERENCE ===
{full}

=== ALL POTENTIAL CANDIDATES ===
{json.dumps(compact_payload, ensure_ascii=False)}
"""
    text_rank = call(
        cfg,
        prompt1,
        images=[],
        timeout=analysis.get("qwen_text_timeout_seconds", 900),
    )

    raw_shortlist = []
    for x in text_rank.get("shortlist", []):
        try:
            cid = int(x["candidate_id"])
        except (KeyError, TypeError, ValueError):
            continue
        match = next((c for c in candidates if c["candidate_id"] == cid), None)
        if match:
            y = dict(match)
            y["_qwen_rank_score"] = float(x.get("score", 0))
            y["_qwen_reason"] = str(x.get("reason", ""))
            raw_shortlist.append(y)

    # If Qwen returns too few, supplement from the entire candidate pool,
    # not just the old top-8 heuristic subset.
    by_id = {c["candidate_id"]: c for c in candidates}
    for c in _temporal_diverse(candidates, shortlist_n * 2, min_gap=45.0):
        if len(raw_shortlist) >= shortlist_n:
            break
        if c["candidate_id"] not in {x["candidate_id"] for x in raw_shortlist}:
            raw_shortlist.append(dict(c))

    selected = _temporal_diverse(raw_shortlist, shortlist_n, min_gap=60.0)

    vision_frames_per_candidate = max(
        1,
        min(3, int(analysis.get("qwen_vision_frames_per_candidate", 2))),
    )
    images = []
    for c in selected:
        images += frames.get(c["candidate_id"], [])[:vision_frames_per_candidate]

    selected_payload = [
        {
            "candidate_id": c["candidate_id"],
            "event_time": c.get("event_time"),
            "search_start": c.get("search_start"),
            "search_end": c.get("search_end"),
            "signal_strength": c.get("signal_strength"),
            "signals": c.get("signals"),
            "evidence": c.get("evidence", [])[:6],
            "qwen_rank_score": c.get("_qwen_rank_score"),
            "qwen_reason": c.get("_qwen_reason"),
        }
        for c in selected
    ]

    render_top = int(analysis.get("render_top", 0))
    qwen_output_limit = render_top if render_top > 0 else max(len(selected), len(candidates), 1)
    prompt2 = f"""Perform the FINAL editorial pass.

The COMPLETE timestamped transcript below is still the MASTER REFERENCE.
The shortlisted candidates are only proposed locations. Inspect their frames
for visual confirmation. You may reject them, but you should return multiple
strong clips when the stream contains multiple strong moments.

Return ONLY:
{{"clips":[{{
"candidate_id":1,"keep":true,"score":96,
"highlight_start":0.0,"highlight_end":0.0,
"final_start":0.0,"final_end":0.0,
"layout":"gameplay|full_camera","crop_focus":"left|center|right",
"category":"clutch|funny|reaction|rage|story|conversation|skill|surprise|mixed",
"title":"short title","hook":"short hook",
"reason":"why it is worth posting",
"caption_focus":["exact phrase from transcript"]
}}]}}

Return at most {qwen_output_limit} clips. You are producing a SHORTS QUEUE, not selecting one winner.
Return every genuinely postable moment you can justify from the shortlist. A
score of 70+ means renderable. Do not collapse the stream to one winner.
Prefer temporal diversity and non-overlapping moments. Final boundaries must
contain the highlight and use transcript timestamps. Prefer <=90 seconds
unless setup/payoff requires more. Avoid overlapping clips.

IMPORTANT:
- caption_focus MUST be exact words from the supplied transcript.
- Never invent speech.
- Use the transcript to determine what was actually said.
- If a candidate is weak, reject it.
- Favor temporal diversity across the stream.
- For gameplay clips, set crop_focus to left, center, or right based on where
  the important action is in the supplied frame(s). This controls the horizontal
  9:16 gameplay crop. Use center when uncertain.

Default context: {analysis['default_context_before']}s before and
{analysis['default_context_after']}s after the highlight.

=== COMPLETE TIMESTAMPED TRANSCRIPT - MASTER REFERENCE ===
{full}

=== SHORTLISTED CANDIDATES ===
{json.dumps(selected_payload, ensure_ascii=False)}

=== TEXT-ONLY RANKING ===
{json.dumps(text_rank, ensure_ascii=False)}
"""
    result = call(
        cfg,
        prompt2,
        images=images,
        timeout=analysis.get("qwen_vision_timeout_seconds", 1200),
    )

    # Defensive post-processing: Qwen can occasionally return duplicate or
    # overlapping selections. Keep the highest-scored selection per candidate
    # and enforce a small temporal gap between rendered clips.
    clips = result.get("clips", []) if isinstance(result, dict) else []
    clean = []
    seen = set()
    for c in sorted(clips, key=lambda x: float(x.get("score", 0)), reverse=True):
        try:
            cid = int(c.get("candidate_id"))
            a = float(c.get("final_start", c.get("highlight_start", 0)))
            b = float(c.get("final_end", c.get("highlight_end", 0)))
        except (TypeError, ValueError):
            continue
        if cid in seen or b <= a:
            continue
        if any(not (b <= float(x["_a"]) - 5 or a >= float(x["_b"]) + 5) for x in clean):
            continue
        c["candidate_id"] = cid
        clean.append({**c, "_a": a, "_b": b})
        seen.add(cid)
        if render_top > 0 and len(clean) >= render_top:
            break

    for c in clean:
        c.pop("_a", None)
        c.pop("_b", None)
    # If the vision model returns too few clips, retain its decisions but also
    # expose the text-stage shortlist so the pipeline can promote strong
    # candidates instead of silently producing one Short.
    return {"clips": clean, "text_rank": text_rank, "shortlist": [
        {**{k:v for k,v in c.items() if not str(k).startswith("_")}, "qwen_rank_score": c.get("_qwen_rank_score", 0), "qwen_reason": c.get("_qwen_reason", "")} for c in selected
    ]}
