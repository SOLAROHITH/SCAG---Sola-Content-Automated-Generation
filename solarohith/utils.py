import json, subprocess
from pathlib import Path

def run(cmd, check=True):
    p=subprocess.run(cmd,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    if check and p.returncode:
        raise RuntimeError("Command failed:\n"+" ".join(map(str,cmd))+"\n\n"+p.stderr[-6000:])
    return p

def load_yaml(p):
    import yaml
    return yaml.safe_load(Path(p).read_text(encoding="utf-8"))

def save_json(p,d):
    p=Path(p); p.parent.mkdir(parents=True,exist_ok=True)
    p.write_text(json.dumps(d,indent=2,ensure_ascii=False),encoding="utf-8")

def ffprobe_duration(p):
    """Return media duration in seconds, with fallbacks for files whose
    container-level duration is reported as N/A.

    Some OBS/recording/remuxed files have a valid video stream but no
    container ``format.duration``.  The old implementation attempted
    ``float("N/A")`` and crashed before the pipeline could start.
    """
    path = str(p)

    # 1) Prefer container duration, but also ask for stream durations in the
    # same probe so files with missing format metadata still work.
    r = run([
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration:stream=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        path,
    ], check=False)

    values = []
    for raw in (r.stdout or "").splitlines():
        raw = raw.strip()
        if not raw or raw.upper() == "N/A":
            continue
        try:
            value = float(raw)
            if value > 0:
                values.append(value)
        except ValueError:
            pass

    if values:
        # The first value is normally format.duration.  Using the maximum
        # protects against a shorter auxiliary stream (e.g. audio) being
        # listed before the video stream.
        return max(values)

    # 2) Last-resort fallback: let ffmpeg inspect the file and parse its
    # human-readable Duration line.  This handles unusual/remuxed files where
    # ffprobe exposes no usable duration field at all.
    r2 = run(["ffmpeg", "-hide_banner", "-i", path], check=False)
    import re
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", r2.stderr or "")
    if m:
        h, minute, sec = m.groups()
        return int(h) * 3600 + int(minute) * 60 + float(sec)

    raise RuntimeError(
        f"Could not determine video duration for: {path}\n"
        "ffprobe returned no usable duration (format/stream duration was N/A).\n"
        f"ffprobe output: {(r.stderr or '').strip()[-2000:]}"
    )




def ffprobe_audio_streams(p):
    """Return audio stream metadata from ffprobe."""
    r = run([
        "ffprobe", "-v", "error",
        "-select_streams", "a",
        "-show_entries", "stream=index,codec_name,channels:stream_tags=title",
        "-of", "json", str(p),
    ])
    data = json.loads(r.stdout or "{}")
    return data.get("streams", [])

def ts(s):
    s=max(0,float(s)); h=int(s//3600); m=int((s%3600)//60); sec=s%60
    return f"{h:02d}:{m:02d}:{sec:06.3f}"

def srt_ts(s):
    s=max(0,float(s)); h=int(s//3600); m=int((s%3600)//60); sec=int(s%60)
    ms=int(round((s-int(s))*1000))
    if ms>=1000: sec+=1; ms-=1000
    return f"{h:02d}:{m:02d}:{sec:02d},{ms:03d}"

def safe_name(x):
    x="".join(c if c.isalnum() or c in " _-" else "_" for c in (x or "clip"))
    return x[:90].strip() or "clip"
