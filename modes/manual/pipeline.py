
def _youtube_auto_caption(source, work, progress):
    if not str(source).startswith(("http://", "https://")):
        return None
    progress("[TRANSCRIBE] Trying YouTube English auto captions...")
    r = subprocess.run(
        ["yt-dlp","--write-auto-subs","--sub-langs","en","--sub-format","vtt",
         "--skip-download","-o",str(work/"youtube_caption.%(ext)s"),str(source)],
        capture_output=True, text=True, encoding="utf-8", errors="replace"
    )
    files=list(work.glob("youtube_caption*.en.vtt"))
    if r.returncode != 0 or not files:
        progress("[TRANSCRIBE] YouTube captions unavailable; falling back to Qwen3-ASR.")
        return None
    write_transcript(files[0], work/"transcript.json")
    progress("[TRANSCRIBE] Using YouTube auto-generated VTT transcript.")
    return work/"transcript.json"

from pathlib import Path
import subprocess, json
from solarohith.vtt_transcriber import write_transcript

from solarohith.downloader import download_vod
from solarohith.transcriber import extract_audio, transcribe
from solarohith.editor import render
from solarohith.utils import load_yaml, ffprobe_duration


class ManualPipeline:
    """
    Manual flow:
      local path OR YouTube URL
      -> selected range (+ optional padding)
      -> selected-range Whisper transcription with word timestamps
      -> shared FFmpeg/NVENC renderer
    """

    def __init__(self, config="config.yaml", storage="projects"):
        self.config_path = Path(config).resolve()
        self.cfg = load_yaml(self.config_path)
        self.storage = Path(storage).resolve()

    def _resolve_source(self, source, work):
        source = str(source).strip()
        if source.startswith(("http://", "https://")):
            return download_vod(source, work / "source")
        path = Path(source).expanduser().resolve()
        if not path.exists():
            raise FileNotFoundError(f"Source video not found: {path}")
        return path

    def run(
        self,
        source,
        start,
        end,
        fmt="short",
        padding=0.0,
        title="manual_clip",
        subtitles=None,
    ):
        if fmt not in {"short", "long"}:
            raise ValueError("fmt must be 'short' or 'long'")

        start = float(start)
        end = float(end)
        if end <= start:
            raise ValueError("end must be greater than start")

        if subtitles is None:
            subtitles = fmt == "short"

        project = self.storage / "manual"
        work = project / "work"
        output = project / ("shorts" if fmt == "short" else "longform")
        work.mkdir(parents=True, exist_ok=True)
        output.mkdir(parents=True, exist_ok=True)

        video = self._resolve_source(source, work)
        duration = ffprobe_duration(video)

        final_start = max(0.0, start - float(padding))
        final_end = min(duration, end + float(padding))
        if final_end <= final_start:
            raise ValueError("Selected range is outside the video.")

        # Extract only the selected/padded audio. Whisper timestamps therefore
        # begin at zero and are shifted into the source-video timebase below.
        audio = work / f"{title}_audio.wav"
        tcfg = self.cfg.get("transcription", {})
        audio_stream = tcfg.get("audio_stream", "auto")
        audio_channel = tcfg.get("audio_channel", "auto")

        extract_audio(
            video,
            audio,
            start=final_start,
            duration=final_end - final_start,
            audio_stream=audio_stream,
            audio_channel=audio_channel,
            force=True,
        )

        youtube_vtt = _youtube_auto_caption(source, work, progress)

        if youtube_vtt:

            transcript = json.loads(youtube_vtt.read_text(encoding='utf-8'))

        else:

            transcript = transcribe(
            audio,
            work / f"{title}_transcript.json",
            self.cfg,
            force=True,
        )

        for segment in transcript["segments"]:
            segment["start"] += final_start
            segment["end"] += final_start
            for word in segment.get("words", []):
                word["start"] += final_start
                word["end"] += final_start

        rendered = render(
            video,
            {
                "title": title,
                "final_start": final_start,
                "final_end": final_end,
                "highlight_start": start,
                "highlight_end": end,
                "layout": "gameplay",
                "category": "manual",
            },
            transcript,
            self.cfg,
            output,
            subtitles=subtitles,
        )

        return {
            "success": True,
            "output_path": str(rendered),
            "format": fmt,
            "captions": bool(subtitles),
            "start": final_start,
            "end": final_end,
        }
