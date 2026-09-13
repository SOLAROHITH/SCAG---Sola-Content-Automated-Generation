try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

import sys
import os, sys, subprocess, re, time
from pathlib import Path
import streamlit as st

ROOT = Path(__file__).resolve().parent
st.set_page_config(page_title="SCAG", page_icon="🎬", layout="wide")

st.title("🎬 SOLA ROHITH SCAG")
st.caption("Content Automated Generation · Local Ollama / Qwen")
st.info("Large local videos are supported. The dashboard upload limit is 10 GB; for 3-5 hour VODs, using a local path is still faster and avoids browser upload overhead.")

mode = st.radio("MODE", ["Automatic", "Manual"], horizontal=True)
st.divider()

uploaded = st.file_uploader(
    "SOURCE - drag & drop video",
    type=["mp4", "mkv", "mov", "webm", "avi"],
    help="Uploads up to 10 GB. For multi-GB streams, LOCAL VIDEO PATH is recommended.",
)
url = st.text_input(
    "YouTube URL",
    placeholder="https://www.youtube.com/watch?v=..."
)
local_path = st.text_input(
    "LOCAL VIDEO PATH (optional)",
    placeholder=r"C:\Users\Sola Rohith\Videos\stream.mp4",
    help="For very large files (3-5 hour streams), enter the local path instead of uploading through the browser."
)
subtitle_upload = st.file_uploader(
    "OPTIONAL SUBTITLES / TRANSCRIPT (.vtt)",
    type=["vtt"],
    help="If supplied, SCAG uses this timestamped VTT instead of ASR. For YouTube URLs, leave this empty to automatically try YouTube captions.",
)
subtitle_path = st.text_input(
    "LOCAL VTT PATH (optional)",
    placeholder=r"X:\Streams\stream.en.vtt",
    help="Existing local VTT takes priority over YouTube captions and Qwen3-ASR.",
)

out = st.text_input(
    "OUTPUT LOCATION",
    value=str(ROOT / "projects" / "output")
)

def subtitle_source():
    lp = subtitle_path.strip()
    if lp:
        p = Path(lp).expanduser()
        if not p.is_file():
            st.error(f"Local VTT not found: {lp}")
            return ""
        return str(p.resolve())
    if subtitle_upload:
        d = ROOT / "projects" / "_dashboard_uploads"
        d.mkdir(parents=True, exist_ok=True)
        p = d / subtitle_upload.name
        p.write_bytes(subtitle_upload.getvalue())
        return str(p.resolve())
    return ""



def models():
    try:
        p = subprocess.run(
            ["ollama", "list"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return (
            [x.split()[0] for x in p.stdout.strip().splitlines()[1:] if x.split()]
            if p.returncode == 0 else []
        )
    except Exception:
        return []


def source():
    # Prefer a local filesystem path. This avoids copying multi-GB VODs through
    # Streamlit's browser upload mechanism.
    lp = local_path.strip()
    if lp:
        p = Path(lp)
        if not p.is_file():
            st.error(f"Local video not found: {lp}")
            return ""
        return str(p)

    if uploaded:
        d = ROOT / "projects" / "_dashboard_uploads"
        d.mkdir(parents=True, exist_ok=True)
        p = d / uploaded.name
        # Stream to disk in chunks rather than building another full in-memory copy.
        with p.open("wb") as f:
            while True:
                chunk = uploaded.read(8 * 1024 * 1024)
                if not chunk:
                    break
                f.write(chunk)
        return str(p)

    return url.strip()


def run_process(command, env=None, status_label="Running SCAG..."):
    """Run a child process and render compact progress in the dashboard."""
    progress_bar = st.progress(0, text="Preparing...")
    download_details = st.empty()
    stage_details = st.empty()
    log_expander = st.expander("Detailed pipeline log", expanded=False)
    log_box = log_expander.empty()

    lines = []
    download_re = re.compile(
        r"\[DOWNLOAD\]\s+([\d.]+)%\s+\|\s+(.+?)\s+\|\s+(.+?)\s+\|\s+ETA\s+(.+)"
    )

    child_env = dict(os.environ) if env is None else dict(env)
    child_env["PYTHONUNBUFFERED"] = "1"

    p = subprocess.Popen(
        command,
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        env=child_env,
    )

    last_download = None

    for raw in p.stdout:
        line = raw.rstrip()
        if line:
            print(line, flush=True)
        if not line:
            continue

        # Keep the dashboard log compact: yt-dlp emits many near-duplicate progress lines.
        if line.startswith("[download]") and lines and lines[-1].startswith("[download]"):
            lines[-1] = line
        else:
            lines.append(line)
        if len(lines) > 300:
            lines = lines[-300:]

        m = download_re.search(line)
        if m:
            pct = float(m.group(1))
            amount = m.group(2)
            speed = m.group(3)
            eta = m.group(4)

            progress_bar.progress(
                min(1.0, max(0.0, pct / 100.0)),
                text=f"Downloading VOD - {pct:.1f}%",
            )
            download_details.markdown(
                f"**{amount}** &nbsp; • &nbsp; **{speed}** &nbsp; • &nbsp; "
                f"**ETA {eta}**"
            )
            last_download = pct
        elif "[DOWNLOAD]" in line:
            download_details.info(line.replace("[DOWNLOAD]", "").strip())
        else:
            # Show the latest pipeline stage without flooding the main UI.
            if re.match(r"\d/8\s", line) or line.startswith("COMPLETE"):
                stage_details.write(line)

            tm = re.search(
                r"\[TRANSCRIBE\].*?\b(\d+)\/(\d+)\b.*?([\d.]+)%.*",
                line,
            )
            if tm:
                done = int(tm.group(1))
                total = max(1, int(tm.group(2)))
                pct = min(100.0, max(0.0, float(tm.group(3))))
                progress_bar.progress(
                    pct / 100.0,
                    text=f"Transcribing entire stream - {pct:.1f}% "
                         f"({done}/{total} chunks)",
                )
                download_details.markdown(
                    f"**Transcription** • chunk **{done}/{total}** • "
                    f"**{pct:.1f}% of stream**"
                )
            elif "[TRANSCRIBE] Complete stream:" in line:
                progress_bar.progress(0.0, text="Transcribing entire stream - starting...")

        log_box.code("\n".join(lines[-80:]), language="text")

    rc = p.wait()

    if rc == 0:
        progress_bar.progress(1.0, text="Complete")
        return rc, lines

    progress_bar.empty()
    return rc, lines


if mode == "Automatic":
    ms = models()
    default_idx = next(
        (i for i, x in enumerate(ms) if x.startswith("qwen3-vl:8b")), 0
    )
    model = (
        st.selectbox("AI MODEL", ms, index=default_idx)
        if ms
        else st.text_input("AI MODEL", value="qwen3-vl:8b")
    )

    if st.button(
        "🚀 START SCAG",
        type="primary",
        use_container_width=True,
    ):
        s = source()
        if not s:
            st.error("Provide a local video or YouTube URL.")
        else:
            e = dict(os.environ)
            e["SCAG_OLLAMA_MODEL"] = model
            e["SCAG_OUTPUT_DIR"] = out
            vtt = subtitle_source()
            if vtt:
                e["SCAG_VTT_PATH"] = vtt

            with st.status("Running SCAG...", expanded=True) as q:
                rc, _ = run_process(
                    [sys.executable, "run_pipeline.py", s],
                    env=e,
                )
                q.update(
                    label="SCAG completed" if rc == 0 else "SCAG failed",
                    state="complete" if rc == 0 else "error",
                )

            if rc == 0:
                st.success(f"Completed. Output setting: {out}")

else:
    fmt = st.selectbox("OUTPUT FORMAT", ["short", "long"])
    a, c = st.columns(2)
    start = a.text_input("START", "00:00:00")
    end = c.text_input("END", "00:00:30")
    pad = st.number_input(
        "CONTEXT PADDING (seconds)", 0, 120, 0
    )
    title = st.text_input("CLIP TITLE", "manual_clip")

    if st.button(
        "🎬 CREATE CLIP",
        type="primary",
        use_container_width=True,
    ):
        s = source()
        if not s:
            st.error("Provide a local video or YouTube URL.")
        else:
            e = dict(os.environ)
            e["SCAG_OUTPUT_DIR"] = out

            with st.status("Creating clip...", expanded=True) as q:
                rc, _ = run_process(
                    [
                        sys.executable,
                        "-m",
                        "app.cli",
                        "manual-clip",
                        s,
                        "--start",
                        start,
                        "--end",
                        end,
                        "--format",
                        fmt,
                        "--padding",
                        str(pad),
                        "--title",
                        title,
                    ],
                    env=e,
                    status_label="Creating clip...",
                )
                q.update(
                    label="Clip created" if rc == 0 else "Clip failed",
                    state="complete" if rc == 0 else "error",
                )

            if rc == 0:
                st.success(f"Completed. Output setting: {out}")
