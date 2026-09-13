from pathlib import Path
import os
from datetime import datetime
from .utils import load_yaml,save_json,ffprobe_duration
from .downloader import download_vod, download_auto_captions
from .vtt_transcriber import write_transcript
from .transcriber import extract_audio,transcribe
from .candidates import make_candidates
from .frames import extract_frames,whole_scan
from .qwen import decide
from .editor import render,longform

def run_pipeline(source_input, root, config, progress=print, project_name=None):
    """Run the complete automated SCAG pipeline."""
    cfg = load_yaml(config)
    # Dashboard/CLI may override the local AI model and output directory.
    if os.getenv("SCAG_OLLAMA_MODEL"):
        cfg.setdefault("ai", {})["model"] = os.environ["SCAG_OLLAMA_MODEL"]
    if os.getenv("SCAG_OUTPUT_DIR"):
        cfg.setdefault("storage", {})["dashboard_output_root"] = os.environ["SCAG_OUTPUT_DIR"]

    project = Path(root) / "projects" / (
        project_name or datetime.now().strftime("%Y%m%d_%H%M%S")
    )
    source_dir = project / "source"
    work = project / "work"
    out = project / "output"
    configured_output = os.getenv("SCAG_OUTPUT_DIR")
    if configured_output:
        out = Path(configured_output).expanduser().resolve() / project.name

    for d in (source_dir, work, out):
        d.mkdir(parents=True, exist_ok=True)

    progress("1/8 Downloading complete VOD...")
    source_input = str(source_input).strip()

    # IMPORTANT: keep the original URL/path separate from project/source/.
    if source_input.startswith(("http://", "https://")):
        video = download_vod(source_input, source_dir, progress=progress)
    else:
        video = Path(source_input).expanduser().resolve()
        if not video.exists():
            raise FileNotFoundError(f"Source video not found: {video}")
        if not video.is_file():
            raise ValueError(f"Source path is not a file: {video}")

    video = Path(video).resolve()
    if not video.exists() or not video.is_file():
        raise FileNotFoundError(f"Downloaded/source video is invalid: {video}")

    duration = ffprobe_duration(video)
    progress(f"  Source: {video.name}")
    progress(f"  Duration: {duration:.2f}s")

    tcfg = cfg.get("transcription", {})
    audio_stream = tcfg.get("audio_stream", "auto")
    audio_channel = tcfg.get("audio_channel", "auto")

    # VTT is the preferred transcript source. A user-supplied VTT wins; for
    # YouTube URLs we automatically try YouTube captions before touching ASR.
    vtt_path = os.getenv("SCAG_VTT_PATH", "").strip()
    if vtt_path:
        vp = Path(vtt_path).expanduser().resolve()
        if not vp.is_file():
            raise FileNotFoundError(f"VTT subtitle file not found: {vp}")
        vtt_path = vp
        progress(f"[TRANSCRIBE] Using supplied VTT: {vp.name}")
    elif source_input.startswith(("http://", "https://")):
        vtt_path = download_auto_captions(source_input, work, progress=progress)

    wav = None
    if vtt_path:
        progress("2/8 Using timestamped VTT transcript (skipping ASR audio extraction)...")
        transcript = write_transcript(vtt_path, work / "transcript.json")
        progress(f"[TRANSCRIBE] VTT transcript ready - {transcript.get('segment_count', len(transcript.get('segments', [])))} segments")
    else:
        progress("2/8 Extracting complete-stream speech audio...")
        wav = extract_audio(
            video,
            work / "audio.wav",
            audio_stream=audio_stream,
            audio_channel=audio_channel,
            force=True,
        )
        progress("3/8 Transcribing ENTIRE stream with timestamps...")
        transcript = transcribe(wav, work / "transcript.json", cfg, force=False, progress=progress)
    full = "\n".join(
        f"[{s['start']:09.3f}] {s['text']}" for s in transcript["segments"]
    )
    (work / "transcript.txt").write_text(full, encoding="utf-8")

    progress("4/8 Building broad potential-candidate index...")
    if wav is None:
        progress("[CANDIDATES] Extracting audio only for candidate signal analysis...")
        wav = extract_audio(
            video, work / "audio.wav",
            audio_stream=audio_stream, audio_channel=audio_channel, force=True
        )
    candidates = make_candidates(
        transcript, wav, video, cfg, work / "candidates.json"
    )

    progress("5/8 Sampling the whole-stream visual timeline...")
    scans = whole_scan(
        video,
        duration,
        work / "frames" / "whole_stream",
        cfg["analysis"]["vision_scan_seconds"],
    )

    progress("6/8 Extracting dense visual evidence around candidates...")
    frames = {}
    for i, c in enumerate(candidates, 1):
        frames[c["candidate_id"]] = extract_frames(
            video,
            c["search_start"],
            c["search_end"],
            work / "frames" / f"candidate_{c['candidate_id']:04d}",
            cfg["analysis"]["candidate_frames"],
        )
        if i % 10 == 0:
            progress(f"  {i}/{len(candidates)} candidates prepared")

    save_json(
        work / "qwen_input.json",
        {
            "full_transcript": full,
            "candidates": candidates,
            "whole_stream_visual_samples": len(scans),
            "source_video": str(video),
            "duration_seconds": duration,
        },
    )

    progress(
        "7/8 Qwen editorial pass: full transcript + candidate list "
        "+ visual evidence..."
    )
    decisions = decide(cfg, full, candidates, frames)
    save_json(work / "qwen_decisions.json", decisions)

    clips = []
    qwen_clips = list(decisions.get("clips", []))
    # Qwen-VL occasionally returns only one item even when its text-stage
    # shortlist contains many strong moments. Promote the text-stage shortlist
    # as a safety net so the content machine produces a real Shorts queue.
    # render_top:
    #   > 0 = cap the Shorts queue at this many clips
    #   0    = render every eligible candidate
    target_render = int(cfg["analysis"].get("render_top", 0))
    existing_ids = {
        int(x.get("candidate_id"))
        for x in qwen_clips
        if str(x.get("candidate_id", "")).isdigit()
    }

    # First promote Qwen's text-stage shortlist when the vision pass returns
    # fewer clips than requested. This is deliberately not capped when
    # render_top=0: the user asked for every identified candidate as a Short.
    fallback_sources = list(decisions.get("shortlist", []))
    if not fallback_sources and not qwen_clips:
        fallback_sources = list(candidates)

    for sc in fallback_sources:
        cid = int(sc.get("candidate_id", -1)) if str(sc.get("candidate_id", "")).isdigit() else -1
        if cid < 0 or cid in existing_ids:
            continue
        if target_render > 0 and len(qwen_clips) >= target_render:
            break
        base = next((x for x in candidates if int(x["candidate_id"]) == cid), None)
        if not base:
            continue

        score = float(
            sc.get("_qwen_rank_score",
                   sc.get("qwen_rank_score",
                         sc.get("score", base.get("signal_strength", 0)))) or 0
        )

        # For Qwen-ranked candidates, respect the minimum score. For the
        # emergency all-candidate fallback, retain the candidate signal score
        # so no candidate silently disappears from the requested Shorts queue.
        is_qwen_ranked = "_qwen_rank_score" in sc or "qwen_rank_score" in sc
        if is_qwen_ranked and score < float(cfg["analysis"].get("candidate_min_score", 70)):
            continue

        start = float(base.get("event_time", base.get("search_start", 0)))
        final_start = max(0.0, float(base.get("search_start", start - 10)))
        final_end = min(duration, float(base.get("search_end", start + 10)))
        if final_end <= final_start:
            continue

        qwen_clips.append({
            "candidate_id": cid,
            "keep": True,
            "score": score,
            "highlight_start": start,
            "highlight_end": min(duration, start + 8.0),
            "final_start": final_start,
            "final_end": final_end,
            "layout": "gameplay|full_camera",
            "crop_focus": "center",
            "category": "mixed",
            "title": f"Stream Highlight {cid}",
            "hook": str(sc.get("_qwen_reason", sc.get("qwen_reason", sc.get("reason", "")))),
            "reason": str(sc.get("_qwen_reason", sc.get("qwen_reason", sc.get("reason", "")))),
            "caption_focus": base.get("evidence", [])[:3],
        })
        existing_ids.add(cid)

    # If render_top=0, guarantee that every broad candidate has a renderable
    # Short. Qwen's editorial decisions remain preferred; this final fallback
    # only fills candidate IDs that Qwen did not explicitly return.
    if target_render == 0:
        for base in candidates:
            cid = int(base["candidate_id"])
            if cid in existing_ids:
                continue
            start = float(base.get("event_time", base.get("search_start", 0)))
            final_start = max(0.0, float(base.get("search_start", start - 10)))
            final_end = min(duration, float(base.get("search_end", start + 10)))
            if final_end <= final_start:
                continue
            qwen_clips.append({
                "candidate_id": cid,
                "keep": True,
                "score": float(base.get("signal_strength", 0) or 0),
                "highlight_start": start,
                "highlight_end": min(duration, start + 8.0),
                "final_start": final_start,
                "final_end": final_end,
                "layout": "gameplay|full_camera",
                "crop_focus": "center",
                "category": "mixed",
                "title": f"Stream Highlight {cid}",
                "hook": "",
                "reason": "Broad candidate fallback",
                "caption_focus": base.get("evidence", [])[:3],
            })
            existing_ids.add(cid)
    for c in qwen_clips:
        if (
            not c.get("keep")
            or int(c.get("score", 0))
            < cfg["analysis"]["candidate_min_score"]
        ):
            continue

        hs = float(c.get("highlight_start", 0))
        he = float(c.get("highlight_end", hs + 5))
        fs = float(c.get("final_start", hs - 10))
        fe = float(c.get("final_end", he + 10))

        c.update(
            highlight_start=hs,
            highlight_end=he,
            final_start=max(0, fs),
            final_end=min(duration, fe),
        )
        if c["final_end"] > c["final_start"]:
            clips.append(c)

    # Only de-duplicate when a finite render cap is requested. With
    # render_top=0 the explicit contract is "one Short per identified
    # candidate", even if candidate windows overlap.
    if target_render > 0:
        dedup = []
        for c in sorted(clips, key=lambda x: float(x.get("score", 0)), reverse=True):
            a = float(c.get("final_start", 0))
            b = float(c.get("final_end", 0))
            if any(
                not (b <= float(x.get("final_start", 0)) - 5 or
                     a >= float(x.get("final_end", 0)) + 5)
                for x in dedup
            ):
                continue
            dedup.append(c)
            if len(dedup) >= target_render:
                break
        clips = dedup

    clips = sorted(clips, key=lambda x: float(x.get("final_start", 0)))

    progress(f"Rendering {len(clips)} Shorts with FFmpeg/NVENC...")
    rendered = []
    for i, c in enumerate(clips, 1):
        progress(
            f"  {i}/{len(clips)}: {c.get('score')} - "
            f"{c.get('title', 'clip')}"
        )
        try:
            rendered.append(str(render(video, c, transcript, cfg, out / "shorts")))
        except Exception as e:
            progress(f"  render failed: {e}")

    report = {
        "source": source_input,
        "video_path": str(video),
        "duration_seconds": duration,
        "candidate_count": len(candidates),
        "selected_clips": clips,
        "rendered": rendered,
    }

    if cfg["longform"]["enabled"] and clips:
        progress("Rendering long-form highlight compilation...")
        try:
            report["longform"] = str(
                longform(
                    video,
                    (clips if int(cfg["longform"].get("max_clips", 0)) <= 0 else clips[:int(cfg["longform"]["max_clips"])]),
                    transcript,
                    cfg,
                    out / "longform",
                )
            )
        except Exception as e:
            report["longform_error"] = str(e)

    save_json(out / "review.json", report)
    progress(f"COMPLETE. Review: {project}")
    return report

