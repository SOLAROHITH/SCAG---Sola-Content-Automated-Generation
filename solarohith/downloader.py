from pathlib import Path
import time
import yt_dlp


def download_vod(url, out_dir, progress=print):
    """
    Download the highest-quality MP4-compatible video/audio available.

    Designed for multi-hour VODs:
    - keeps .part files
    - resumes interrupted downloads
    - retries HTTP and fragment failures aggressively
    - does NOT intentionally reduce source quality
    - emits compact [DOWNLOAD] progress lines for the Streamlit dashboard
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / "source.mp4"

    if out.exists() and out.stat().st_size > 10_000_000:
        progress(f"[DOWNLOAD] Existing complete source found: {out.name}")
        return out

    last_report = {"time": 0.0, "pct": None}

    def hook(d):
        status = d.get("status")
        now = time.monotonic()

        if status == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            downloaded = d.get("downloaded_bytes", 0)
            pct = (downloaded / total * 100.0) if total else None

            # Avoid flooding Streamlit while still giving a smooth dashboard.
            if pct is not None:
                should_report = (
                    last_report["pct"] is None
                    or pct - last_report["pct"] >= 0.5
                    or now - last_report["time"] >= 1.0
                )
            else:
                should_report = now - last_report["time"] >= 1.0

            if should_report:
                speed = d.get("speed")
                eta = d.get("eta")

                def fmt_bytes(n):
                    if not n:
                        return "-"
                    units = ["B", "KiB", "MiB", "GiB"]
                    x = float(n)
                    for u in units:
                        if x < 1024 or u == units[-1]:
                            return f"{x:.1f} {u}"
                        x /= 1024

                speed_s = f"{fmt_bytes(speed)}/s" if speed else "-"
                eta_s = (
                    f"{int(eta)//3600:02d}:{(int(eta)%3600)//60:02d}:{int(eta)%60:02d}"
                    if eta is not None else "-"
                )
                size_s = fmt_bytes(total)

                if pct is not None:
                    progress(
                        f"[DOWNLOAD] {pct:.1f}% | "
                        f"{fmt_bytes(downloaded)} / {size_s} | "
                        f"{speed_s} | ETA {eta_s}"
                    )
                    last_report["pct"] = pct
                else:
                    progress(
                        f"[DOWNLOAD] {fmt_bytes(downloaded)} / {size_s} | "
                        f"{speed_s} | ETA {eta_s}"
                    )
                last_report["time"] = now

        elif status == "finished":
            progress("[DOWNLOAD] Video stream finished; preparing merge...")

    opts = {
        # Highest-quality MP4-compatible video + best M4A audio.
        # No artificial resolution/bitrate reduction.
        "format": "bv*[ext=mp4]+ba[ext=m4a]/b[ext=mp4]/best",

        "merge_output_format": "mp4",
        "outtmpl": str(out_dir / "download.%(ext)s"),
        "noplaylist": True,

        # Long-VOD resilience.
        "retries": 20,
        "fragment_retries": 20,
        "continuedl": True,
        "nopart": False,
        "socket_timeout": 60,
        "concurrent_fragment_downloads": 1,

        # Keep chunking conservative for connection stability while
        # retaining the selected source quality.
        "http_chunk_size": 10 * 1024 * 1024,

        "progress_hooks": [hook],
        "quiet": True,
        "no_warnings": False,
    }

    progress("[DOWNLOAD] Starting/resuming highest-quality VOD download...")
    progress("[DOWNLOAD] Partial .part files are preserved for resume.")

    with yt_dlp.YoutubeDL(opts) as y:
        info = y.extract_info(url, download=True)
        made = Path(y.prepare_filename(info))

    for p in [made, made.with_suffix(".mp4"), out_dir / "download.mp4"]:
        if p.exists():
            if p.resolve() != out.resolve():
                if out.exists():
                    out.unlink()
                p.replace(out)
            progress(f"[DOWNLOAD] Complete: {out.name}")
            return out

    raise FileNotFoundError("No MP4 produced by yt-dlp.")


def download_auto_captions(url, out_dir, progress=print):
    """Download YouTube English auto/manual captions as VTT when available."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    prefix = out_dir / "youtube_caption"
    progress("[TRANSCRIBE] Checking YouTube English auto captions...")
    opts = {
        "skip_download": True,
        "writesubtitles": True,
        "writeautomaticsub": True,
        "subtitleslangs": ["en", "en-US", "en-GB"],
        "subtitlesformat": "vtt",
        "outtmpl": str(prefix) + ".%(ext)s",
        "noplaylist": True,
        "quiet": True,
        "no_warnings": False,
        "retries": 3,
        "socket_timeout": 30,
    }
    try:
        with yt_dlp.YoutubeDL(opts) as y:
            y.download([str(url)])
    except Exception as exc:
        progress(f"[TRANSCRIBE] YouTube caption lookup failed: {exc}")
        return None
    files = sorted(out_dir.glob("youtube_caption*.vtt"))
    if not files:
        progress("[TRANSCRIBE] No usable YouTube VTT found; falling back to Qwen3-ASR.")
        return None
    # Prefer an English file if yt-dlp emitted language-specific filenames.
    english = [f for f in files if ".en" in f.name.lower()]
    chosen = english[0] if english else files[0]
    progress(f"[TRANSCRIBE] Found YouTube VTT: {chosen.name}")
    return chosen
