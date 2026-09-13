import re
import json
from pathlib import Path

from faster_whisper import WhisperModel
from .utils import run, save_json, ffprobe_audio_streams


def choose_speech_stream(video, audio_stream="auto"):
    """
    Pick the best audio stream for speech transcription.

    Rules:
      1. Explicit numeric stream wins.
      2. For auto, prefer a stream whose title contains mic/voice/commentary.
      3. Otherwise, for normal Outplayed multi-track files, prefer stream 1.
      4. Fall back to stream 0.

    Important: if a recording contains only one mixed stereo stream, there is
    no separate microphone track to recover. SCAG therefore keeps that stream
    but can select its left/right/mix channel via `audio_channel`.
    """
    streams = ffprobe_audio_streams(video)
    if not streams:
        raise RuntimeError(f"No audio streams found in source: {video}")

    if str(audio_stream).lower() != "auto":
        requested = int(audio_stream)
        if 0 <= requested < len(streams):
            return requested, streams[requested]
        print(
            f"WARNING: requested audio stream {requested} is unavailable; "
            "falling back to automatic selection."
        )

    mic_words = (
        "mic", "microphone", "voice", "commentary", "creator",
        "discord", "chat", "headset"
    )
    for i, stream in enumerate(streams):
        title = str((stream.get("tags") or {}).get("title", "")).lower()
        if any(token in title for token in mic_words):
            return i, stream

    if len(streams) > 1:
        return 1, streams[1]

    return 0, streams[0]


def _channel_filter(audio_channel, channels):
    """
    Return an FFmpeg audio filter for the requested channel mode.

    auto/mix -> mono mix
    left     -> left channel
    right    -> right channel
    """
    mode = str(audio_channel or "auto").lower()
    if mode in {"left", "l"} and channels >= 2:
        return "pan=mono|c0=c0"
    if mode in {"right", "r"} and channels >= 2:
        return "pan=mono|c0=c1"
    return "pan=mono|c0=0.5*c0+0.5*c1" if channels >= 2 else "aformat=channel_layouts=mono"


def extract_audio(
    video,
    wav,
    start=0.0,
    duration=None,
    audio_stream="auto",
    audio_channel="auto",
    force=False,
):
    """
    Extract the speech-recognition audio.

    `audio_stream=auto` is deliberately metadata-aware instead of blindly
    assuming stream 1. For normal Outplayed multi-track recordings it still
    prefers the microphone stream. For single-track stereo recordings, the
    audio is mixed to mono unless the user explicitly selects left/right.

    A speech-focused filter chain is applied before Whisper:
      - mono conversion
      - gentle high-pass / low-pass
      - light denoise
      - 16 kHz PCM

    This improves speech recognition when game audio is mixed into the same
    recording without changing the final exported audio.
    """
    wav = Path(wav)
    wav.parent.mkdir(parents=True, exist_ok=True)
    if wav.exists() and not force:
        return wav

    streams = ffprobe_audio_streams(video)
    if not streams:
        raise RuntimeError(f"No audio streams found in source: {video}")

    selected, meta = choose_speech_stream(video, audio_stream)
    channels = int(meta.get("channels") or 1)

    channel_filter = _channel_filter(audio_channel, channels)

    # Keep this intentionally conservative. We are preparing audio for ASR,
    # not altering the final video audio track.
    speech_filter = (
        f"{channel_filter},"
        "highpass=f=70,"
        "lowpass=f=9000,"
        "afftdn=nr=8:nf=-35,"
        "aresample=16000"
    )

    print(
        f"[SCAG] Whisper audio: stream {selected} | "
        f"channels={channels} | channel={audio_channel} | "
        f"title={(meta.get('tags') or {}).get('title', '') or 'untitled'}"
    )

    cmd = [
        "ffmpeg", "-y",
        "-ss", str(max(0.0, float(start))),
        "-i", str(video),
        "-map", f"0:a:{selected}",
    ]
    if duration is not None:
        cmd += ["-t", str(max(0.0, float(duration)))]

    cmd += [
        "-vn",
        "-af", speech_filter,
        "-ac", "1",
        "-ar", "16000",
        "-c:a", "pcm_s16le",
        str(wav),
    ]
    run(cmd)
    return wav


def _qwen_dtype(torch, value="auto"):
    value = str(value or "auto").lower()
    if value in {"float16", "fp16", "half"}:
        return torch.float16
    if value in {"bfloat16", "bf16"}:
        return torch.bfloat16
    if value in {"float32", "fp32"}:
        return torch.float32
    # NVIDIA GPUs used for SCAG generally run the ASR path well in FP16.
    return torch.float16 if torch.cuda.is_available() else torch.float32


def _stamp_value(obj, name, default=None):
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _qwen_word_timestamps(result):
    """Normalize qwen-asr ForcedAligner timestamps to SCAG word records."""
    stamps = getattr(result, "time_stamps", None) or []
    out = []
    for st in stamps:
        text = str(_stamp_value(st, "text", "") or "").strip()
        if not text:
            continue
        try:
            a = float(_stamp_value(st, "start_time", 0.0))
            b = float(_stamp_value(st, "end_time", a))
        except (TypeError, ValueError):
            continue
        if b <= a:
            continue
        out.append({"start": round(a, 3), "end": round(b, 3), "word": text, "probability": 1.0})
    return out


def _group_qwen_words(words, max_seconds=8.0, max_words=32):
    """Turn Qwen's word/character alignment into stable sentence-like segments."""
    segments=[]
    cur=[]
    last_end=None
    for w in words:
        if cur:
            gap=float(w["start"])-float(last_end or w["start"])
            punct=bool(re.search(r"[.!?]$", cur[-1]["word"]))
            too_long=(float(w["end"])-float(cur[0]["start"])) >= max_seconds
            too_many=len(cur) >= max_words
            if gap >= 0.65 or punct or too_long or too_many:
                segments.append(cur); cur=[]
        cur.append(w); last_end=w["end"]
    if cur: segments.append(cur)
    out=[]
    for g in segments:
        text=" ".join(x["word"] for x in g).strip()
        out.append({
            "start": g[0]["start"], "end": g[-1]["end"], "text": text,
            "avg_logprob": 0.0, "no_speech_prob": 0.0,
            "words": g,
        })
    return out


def _transcribe_qwen3_asr(wav, out, cfg, progress):
    """Local Qwen3-ASR 1.7B + ForcedAligner transcription backend."""
    try:
        import torch
        from qwen_asr import Qwen3ASRModel
    except ImportError as e:
        raise RuntimeError(
            "Qwen3-ASR is not installed. Run: pip install -U qwen-asr\n"
            "Qwen's official package recommends Python 3.12 for this runtime."
        ) from e

    import math, wave, gc
    t=cfg.get("transcription", {})
    chunk_seconds=max(60, int(t.get("chunk_seconds", 180)))
    chunks_dir=Path(out).parent/"transcript_chunks"
    chunks_dir.mkdir(parents=True, exist_ok=True)
    partial_path=Path(out).parent/"transcript_partial.json"

    with wave.open(str(wav), "rb") as wf:
        sr=wf.getframerate(); ch=wf.getnchannels(); sw=wf.getsampwidth(); total_frames=wf.getnframes()
    if sr != 16000 or ch != 1 or sw != 2:
        raise RuntimeError(f"Expected 16 kHz mono PCM16 WAV, got {sr} Hz / {ch} ch / {sw*8}-bit")
    total_duration=total_frames/float(sr)
    total_chunks=max(1, math.ceil(total_duration/chunk_seconds))

    progress(f"[TRANSCRIBE] Qwen3-ASR: {total_duration/3600:.2f}h | {total_chunks} chunks x {chunk_seconds}s")
    model_id=t.get("qwen_model", "Qwen/Qwen3-ASR-1.7B")
    aligner_id=t.get("qwen_forced_aligner", "Qwen/Qwen3-ForcedAligner-0.6B")
    device=t.get("qwen_device", "cuda:0" if torch.cuda.is_available() else "cpu")
    dtype=_qwen_dtype(torch, t.get("qwen_dtype", "float16"))
    if device.startswith("cuda") and not torch.cuda.is_available():
        raise RuntimeError("Qwen3-ASR is configured for CUDA but torch.cuda.is_available() is false.")

    progress(f"[TRANSCRIBE] Loading Qwen3-ASR: {model_id}")
    model=Qwen3ASRModel.from_pretrained(
        model_id,
        dtype=dtype,
        device_map=device,
        max_inference_batch_size=int(t.get("qwen_batch_size", 1)),
        max_new_tokens=int(t.get("qwen_max_new_tokens", 4096)),
        forced_aligner=aligner_id if bool(t.get("qwen_timestamps", True)) else None,
        forced_aligner_kwargs=(
            {"dtype": dtype, "device_map": device}
            if bool(t.get("qwen_timestamps", True)) else None
        ),
    )
    progress(f"[TRANSCRIBE] Qwen3-ASR ready ({device}/{dtype})")

    lang_cfg=str(t.get("language", "auto")).lower()
    language=None if lang_cfg in {"auto","none",""} else t.get("language")
    context=t.get("initial_prompt", "Gaming livestream conversation. Marvel Rivals, heroes, abilities, teammates, opponents, reactions and creator commentary.")
    all_segments=[]; detected_language=None

    with wave.open(str(wav), "rb") as source_wav:
        for idx in range(total_chunks):
            a=idx*chunk_seconds; b=min(total_duration,a+chunk_seconds)
            cp=chunks_dir/f"chunk_{idx:04d}.json"
            wav_chunk=chunks_dir/f"chunk_{idx:04d}.wav"
            if cp.exists():
                payload=json.loads(cp.read_text(encoding="utf-8"))
                all_segments.extend(payload.get("segments",[])); detected_language=detected_language or payload.get("language")
                progress(f"[TRANSCRIBE] {idx+1}/{total_chunks} checkpoint ({b/total_duration*100:.1f}%)")
                continue
            source_wav.setpos(int(a*sr)); raw=source_wav.readframes(int((b-a)*sr))
            with wave.open(str(wav_chunk),"wb") as cw:
                cw.setnchannels(1); cw.setsampwidth(2); cw.setframerate(sr); cw.writeframes(raw)
            progress(f"[TRANSCRIBE] {idx+1}/{total_chunks} ({a/total_duration*100:.1f}%->{b/total_duration*100:.1f}%) transcribing with Qwen3-ASR...")
            kwargs={"audio":str(wav_chunk),"language":language,"return_time_stamps":bool(t.get("qwen_timestamps",True))}
            if context:
                kwargs["context"]=context
            results=model.transcribe(**kwargs)
            if not results:
                raise RuntimeError(f"Qwen3-ASR returned no result for chunk {idx+1}")
            r=results[0]
            detected_language=detected_language or getattr(r,"language",None)
            words=_qwen_word_timestamps(r)
            if not words:
                txt=str(getattr(r,"text","") or "").strip()
                if txt:
                    words=[{"start":0.0,"end":max(0.1,b-a),"word":txt,"probability":1.0}]
            # Align timestamps are relative to the chunk. Add the absolute stream offset.
            for w in words:
                w["start"]=round(w["start"]+a,3); w["end"]=round(w["end"]+a,3)
            segs=_group_qwen_words(words)
            all_segments.extend(segs)
            save_json(cp,{"chunk_index":idx,"start":a,"end":b,"language":getattr(r,"language",None),"segments":segs})
            try: wav_chunk.unlink()
            except OSError: pass
            progress(f"[TRANSCRIBE] {idx+1}/{total_chunks} complete | {b/total_duration*100:.1f}% of stream")
            save_json(partial_path,{"backend":"qwen3-asr","language":detected_language,"duration":total_duration,"completed_chunks":idx+1,"total_chunks":total_chunks,"segments":all_segments})

    all_segments.sort(key=lambda x:(x["start"],x["end"]))
    data={"backend":"qwen3-asr","model":model_id,"forced_aligner":aligner_id if t.get("qwen_timestamps",True) else None,"language":detected_language,"duration":total_duration,"audio_stream":t.get("audio_stream","auto"),"audio_channel":t.get("audio_channel","auto"),"chunk_seconds":chunk_seconds,"total_chunks":total_chunks,"segments":all_segments}
    save_json(out,data)
    Path(out).with_suffix(".txt").write_text("\n".join(f"[{x['start']:09.3f}] {x['text']}" for x in all_segments),encoding="utf-8")
    progress(f"[TRANSCRIBE] COMPLETE - {len(all_segments)} segments | language={detected_language or 'unknown'} | backend=Qwen3-ASR")
    # Release ASR + aligner before Qwen-VL loads.
    try:
        del model
        gc.collect()
        if torch.cuda.is_available(): torch.cuda.empty_cache()
    except Exception: pass
    return data


def transcribe(wav, out, cfg, force=False, progress=print):
    """Transcribe the ENTIRE stream with the configured local ASR backend."""
    out=Path(out); out.parent.mkdir(parents=True,exist_ok=True)
    if out.exists() and not force:
        return json.loads(out.read_text(encoding="utf-8"))
    backend=str(cfg.get("transcription",{}).get("backend","qwen3-asr")).lower()
    if backend in {"qwen3-asr","qwen","qwen_asr"}:
        return _transcribe_qwen3_asr(wav,out,cfg,progress)
    # Legacy Whisper backend remains available for comparison/fallback.
    return _transcribe_whisper_legacy(wav,out,cfg,progress)


def _transcribe_whisper_legacy(wav, out, cfg, progress):
    """Legacy SCAG Whisper implementation, retained for A/B testing."""
    import math, wave
    t=cfg["transcription"]; chunk_seconds=max(30,int(t.get("chunk_seconds",300)))
    chunks_dir=out.parent/"transcript_chunks"; chunks_dir.mkdir(parents=True,exist_ok=True)
    with wave.open(str(wav),"rb") as wf:
        sample_rate=wf.getframerate(); channels=wf.getnchannels(); total_frames=wf.getnframes(); sample_width=wf.getsampwidth()
    total_duration=total_frames/float(sample_rate); total_chunks=max(1,math.ceil(total_duration/chunk_seconds))
    progress(f"[TRANSCRIBE] Legacy Whisper: {total_duration/3600:.2f}h | {total_chunks} chunks x {chunk_seconds}s")
    progress("[TRANSCRIBE] Loading Whisper model...")
    model=WhisperModel(t["model"],device=t["device"],compute_type=t["compute_type"])
    progress(f"[TRANSCRIBE] Whisper ready: {t['model']} ({t['device']}/{t['compute_type']})")
    language_cfg=str(t.get("language","auto")).lower(); language=None if language_cfg in {"auto","none",""} else t.get("language")
    all_segments=[]; detected_language=None
    def do_chunk(chunk_wav,offset):
        segs,info=model.transcribe(str(chunk_wav),language=language,beam_size=int(t.get("beam_size",5)),best_of=int(t.get("best_of",5)),temperature=float(t.get("temperature",0.0)),vad_filter=True,word_timestamps=True,condition_on_previous_text=bool(t.get("condition_on_previous_text",False)),compression_ratio_threshold=float(t.get("compression_ratio_threshold",2.2)),log_prob_threshold=float(t.get("log_prob_threshold",-0.8)),no_speech_threshold=float(t.get("no_speech_threshold",0.6)),initial_prompt=t.get("initial_prompt","Gaming livestream conversation."))
        out=[]
        for seg in segs:
            words=[]
            for w in (seg.words or []):
                if str(w.word or "").strip(): words.append({"start":round(float(w.start)+offset,3),"end":round(float(w.end)+offset,3),"word":str(w.word).strip(),"probability":float(getattr(w,"probability",0.0))})
            if seg.text.strip() or words: out.append({"start":round(float(seg.start)+offset,3),"end":round(float(seg.end)+offset,3),"text":seg.text.strip(),"avg_logprob":float(getattr(seg,"avg_logprob",0.0)),"no_speech_prob":float(getattr(seg,"no_speech_prob",0.0)),"words":words})
        return out,getattr(info,"language",None)
    with wave.open(str(wav),"rb") as source_wav:
        for idx in range(total_chunks):
            a=idx*chunk_seconds;b=min(total_duration,a+chunk_seconds); cp=chunks_dir/f"chunk_{idx:04d}.json"; wp=chunks_dir/f"chunk_{idx:04d}.wav"
            if cp.exists():
                p=json.loads(cp.read_text(encoding="utf-8"));all_segments.extend(p.get("segments",[]));detected_language=detected_language or p.get("language");continue
            source_wav.setpos(int(a*sample_rate)); raw=source_wav.readframes(int((b-a)*sample_rate))
            with wave.open(str(wp),"wb") as cw: cw.setnchannels(1);cw.setsampwidth(2);cw.setframerate(16000);cw.writeframes(raw)
            progress(f"[TRANSCRIBE] {idx+1}/{total_chunks} ({a/total_duration*100:.1f}%->{b/total_duration*100:.1f}%) transcribing...")
            segs,lang=do_chunk(wp,a);detected_language=detected_language or lang;all_segments.extend(segs);save_json(cp,{"chunk_index":idx,"start":a,"end":b,"language":lang,"segments":segs})
            try: wp.unlink()
            except OSError: pass
            progress(f"[TRANSCRIBE] {idx+1}/{total_chunks} complete | {b/total_duration*100:.1f}% of stream")
    all_segments.sort(key=lambda x:(x["start"],x["end"]))
    data={"backend":"whisper","language":detected_language,"duration":total_duration,"audio_stream":t.get("audio_stream","auto"),"audio_channel":t.get("audio_channel","auto"),"chunk_seconds":chunk_seconds,"total_chunks":total_chunks,"segments":all_segments}
    save_json(out,data);out.with_suffix(".txt").write_text("\n".join(f"[{x['start']:09.3f}] {x['text']}" for x in all_segments),encoding="utf-8")
    progress(f"[TRANSCRIBE] COMPLETE - {len(all_segments)} segments | language={detected_language or 'unknown'}")
    return data
