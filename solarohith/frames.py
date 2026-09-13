from pathlib import Path
from .utils import run, ts


def _extract_one(video, t0, out_path, quality=8):
    """Extract one frame robustly on Windows/FFmpeg builds.

    Some FFmpeg Windows builds can fail to initialize the MJPEG encoder when
    writing JPEG from H.264 files (especially with non-full-range YUV). PNG
    avoids that encoder path entirely. We also use a decode-friendly seek
    fallback and scale evidence frames to keep Qwen payloads reasonable.
    """
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if out_path.exists() and out_path.stat().st_size > 0:
        return out_path

    # Fast seek first. RGB PNG is intentionally used instead of MJPEG/JPEG
    # because the latter can fail with ff_frame_thread_encoder_init on some
    # Windows FFmpeg builds.
    commands = [
        [
            "ffmpeg", "-y", "-ss", ts(t0), "-i", str(video),
            "-frames:v", "1", "-vf", "scale=1280:-2,format=rgb24",
            "-c:v", "png", "-compression_level", str(quality),
            str(out_path),
        ],
        [
            "ffmpeg", "-y", "-i", str(video), "-ss", ts(t0),
            "-frames:v", "1", "-vf", "scale=1280:-2,format=rgb24",
            "-c:v", "png", "-compression_level", str(quality),
            str(out_path),
        ],
    ]

    last_error = None
    for cmd in commands:
        try:
            run(cmd)
            if out_path.exists() and out_path.stat().st_size > 0:
                return out_path
        except Exception as e:
            last_error = e
            try:
                if out_path.exists() and out_path.stat().st_size == 0:
                    out_path.unlink()
            except OSError:
                pass

    # Do not make one corrupt/unseekable frame kill an otherwise valid long
    # pipeline. The caller can continue with the remaining evidence frames.
    if last_error:
        print(f"[FRAMES] Warning: could not extract frame at {t0:.3f}s: {last_error}")
    return None


def extract_frames(video, a, b, out, count=6):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    fs = [.05, .20, .35, .55, .75, .95][:count]
    paths = []
    for i, f in enumerate(fs):
        p = out / f"frame_{i:02d}.png"
        t0 = max(0.0, float(a) + (float(b) - float(a)) * f)
        frame = _extract_one(video, t0, p, quality=8)
        if frame:
            paths.append(frame)
    return paths


def whole_scan(video, duration, out, every):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    paths = []
    step = max(1, int(every))
    for i, t0 in enumerate(range(0, int(duration), step)):
        p = out / f"scan_{i:05d}.png"
        frame = _extract_one(video, float(t0), p, quality=9)
        if frame:
            paths.append((t0, frame))
    return paths
