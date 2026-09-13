from pathlib import Path

from .utils import run, ts, safe_name, ffprobe_audio_streams
from .captions import make_ass


def esc(p):
    # Escape a filesystem path for the FFmpeg subtitles filter.
    return (
        str(Path(p).resolve())
        .replace("\\", "/")
        .replace(":", "\\:")
        .replace("'", "\\'")
    )


def esc_drawtext(text):
    # FFmpeg drawtext uses ':' as an option separator and also treats
    # backslash, apostrophe and '%' specially.
    return (
        str(text)
        .replace("\\", "\\\\")
        .replace("%", "\\%")
        .replace("'", "\\'")
        .replace(":", "\\:")
        .replace(",", "\\,")
    )


def _center_gameplay_crop(source_width, source_height, out_width, out_height):
    """Return a centered crop that exactly fills the gameplay panel."""
    target_ratio = float(out_width) / float(out_height)
    source_ratio = float(source_width) / float(source_height)
    if source_ratio > target_ratio:
        crop_h = source_height
        crop_w = int(round(crop_h * target_ratio))
        x = (source_width - crop_w) // 2
        y = 0
    else:
        crop_w = source_width
        crop_h = int(round(crop_w / target_ratio))
        x = 0
        y = max(0, (source_height - crop_h) // 2)
    return crop_w, crop_h, x, y


def render(video, c, t, cfg, outdir, subtitles=True):
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    a = float(c["final_start"])
    b = float(c["final_end"])
    # Keep every render bounded to the requested clip.
    dur = max(1.0, b - a)

    name = safe_name(c.get("title", "clip"))
    out = outdir / f"{name}_{int(a)}.mp4"

    W = int(cfg["output"]["width"])
    H = int(cfg["output"]["height"])
    fps = int(cfg["output"]["fps"])

    f = cfg["source"]["facecam"]
    fx, fy, fw, fh = [int(f[k]) for k in ["x", "y", "width", "height"]]

    ass = None
    ccfg = cfg.get("captions", {})
    if subtitles and ccfg.get("enabled", True):
        ass = outdir / f"{name}_{int(a)}.ass"
        make_ass(
            transcript=t,
            clip_start=a,
            clip_end=b,
            output=ass,
            width=W,
            height=H,
            font_name=ccfg.get("font", "Arial"),
            font_size=int(ccfg.get("font_size", 64)),
            normal=ccfg.get("normal_color", "&H00FFFFFF"),
            highlight=ccfg.get("highlight_color", "&H00FF66CC"),
            outline=ccfg.get("outline_color", "&H00000000"),
            outline_width=int(ccfg.get("outline_width", 5)),
            shadow=int(ccfg.get("shadow", 3)),
            margin_v=int(ccfg.get("margin_bottom", 470)),
            pop_scale=int(ccfg.get("pop_scale", 112)),
            max_words=int(ccfg.get("max_words", 5)),
            max_chars=int(ccfg.get("max_chars", 24)),
            max_line_chars=int(ccfg.get("max_line_chars", 16)),
            pop_ms=int(ccfg.get("pop_ms", 100)),
        )

    subtitle_filter = f",subtitles='{esc(ass)}'" if ass else ""

    # ------------------------------------------------------------
    # VIDEO: top 1/3 facecam + bottom 2/3 centered gameplay
    # ------------------------------------------------------------
    sw = int(cfg["source"].get("width", 1920))
    sh = int(cfg["source"].get("height", 1080))
    f = cfg["source"]["facecam"]
    fx, fy, fw, fh = [int(f[k]) for k in ["x", "y", "width", "height"]]

    gcfg = cfg.get("templates", {}).get("gameplay", {})
    cam_panel_h = int(gcfg.get("camera_height", round(H / 3)))
    game_panel_h = int(gcfg.get("gameplay_height", H - cam_panel_h))
    cam_panel_h = max(1, min(cam_panel_h, H - 1))
    game_panel_h = max(1, min(game_panel_h, H - cam_panel_h))

    cw, ch, gx, gy = _center_gameplay_crop(sw, sh, W, game_panel_h)

    # The facecam is baked into the recording. Extract it into the top panel;
    # independently center-crop the gameplay into the lower two-thirds.
    face_fit = bool(f.get("output_mode", "fit") == "fit") and bool(gcfg.get("facecam_fit", True))
    face_bg = str(gcfg.get("facecam_background", "blurred")).lower()
    face_pad = int(f.get("padding", gcfg.get("facecam_padding", 0)))

    vf_parts = [
        f"[0:v]split=2[game_src][cam_src]",
        f"[game_src]crop={cw}:{ch}:{gx}:{gy},scale={W}:{game_panel_h}:flags=lanczos[game]",
    ]

    if face_fit:
        # Preserve the real camera aspect ratio. Do NOT use
        # force_original_aspect_ratio=increase followed by a crop: that was
        # the reason the previous version only showed the top of the head.
        if face_bg == "blurred":
            vf_parts.append(
                f"[cam_src]crop={fw}:{fh}:{fx}:{fy},"
                f"scale={W}:{cam_panel_h}:force_original_aspect_ratio=increase,"
                f"crop={W}:{cam_panel_h}:0:0,gblur=sigma=28,eq=brightness=-0.18[cam_bg]"
            )
        else:
            vf_parts.append(f"color=black:s={W}x{cam_panel_h}:r={fps}[cam_bg]")

        # Fit the ENTIRE camera inside the top panel, centered, with no crop.
        vf_parts.append(
            f"[cam_src]crop={fw}:{fh}:{fx}:{fy},"
            f"scale={W-2*face_pad}:{cam_panel_h-2*face_pad}:"
            f"force_original_aspect_ratio=decrease:flags=lanczos[cam_fit]"
        )
        vf_parts.append(
            f"[cam_bg][cam_fit]overlay=(W-w)/2:(H-h)/2:format=auto[cam]"
        )
    else:
        # Optional legacy fill mode. This may crop the camera to fill the panel.
        vf_parts.append(
            f"[cam_src]crop={fw}:{fh}:{fx}:{fy},"
            f"scale={W}:{cam_panel_h}:force_original_aspect_ratio=increase,"
            f"crop={W}:{cam_panel_h}:0:0[cam]"
        )

    vf_parts.extend([
        f"color=black:s={W}x{H}:r={fps}[base]",
        f"[base][cam]overlay=0:0:format=auto[top]",
        f"[top][game]overlay=0:{cam_panel_h}:format=auto[comp]",
    ])

    if subtitle_filter:
        vf_parts.append(f"[comp]null{subtitle_filter}[captioned]")
        current = "[captioned]"
    else:
        current = "[comp]"

    # V16 had a NameError here because these variables were never defined.
    # Keep branding fully config-driven and optional.
    branding = cfg.get("branding", {})
    brand = str(branding.get("text", "")).strip()
    fs = int(branding.get("font_size", 34))
    mb = int(branding.get("margin_bottom", 34))
    if brand and bool(branding.get("enabled", True)):
        vf_parts.append(
            f"{current}drawtext=text='{esc_drawtext(brand)}':"
            f"fontcolor=white:fontsize={fs}:"
            f"x=(w-text_w)/2:y=h-{mb}-text_h:"
            f"box=1:boxcolor=black@0.45:boxborderw=12[v]"
        )
    else:
        vf_parts.append(f"{current}null[v]")

    vf = ";".join(vf_parts)

    # ------------------------------------------------------------
    # AUDIO
    # ------------------------------------------------------------
    audio_streams = ffprobe_audio_streams(video)
    if len(audio_streams) >= 2 and cfg.get("output", {}).get("mix_game_and_mic", True):
        audio_filter = (
            ";[0:a:0][0:a:1]"
            "amix=inputs=2:duration=longest:dropout_transition=0:normalize=1,"
            "aresample=async=1:first_pts=0[aout]"
        )
    elif len(audio_streams) >= 1:
        audio_filter = ";[0:a:0]aresample=async=1:first_pts=0[aout]"
    else:
        raise RuntimeError("Source video contains no audio stream.")
    audio_map = "[aout]"
    vf += audio_filter

    run([
        "ffmpeg", "-y",
        "-ss", ts(a),
        "-t", ts(dur),
        "-i", str(video),
        "-filter_complex", vf,
        "-map", "[v]",
        "-map", audio_map,
        "-c:v", "h264_nvenc",
        "-preset", "p5",
        "-b:v", cfg["output"]["video_bitrate"],
        "-maxrate", cfg["output"]["video_bitrate"],
        "-bufsize", "24M",
        "-r", str(fps),
        "-c:a", "aac",
        "-ac", "2",
        "-b:a", cfg["output"]["audio_bitrate"],
        "-movflags", "+faststart",
        "-t", ts(dur),
        "-shortest",
        str(out),
    ])
    return out



def render_landscape(video, c, t, cfg, outdir, subtitles=False):
    """Render a highlight in native 16:9 landscape for the long-form reel."""
    outdir=Path(outdir); outdir.mkdir(parents=True,exist_ok=True)
    a=max(0.0,float(c["final_start"])); b=max(a+1.0,float(c["final_end"])); dur=b-a
    name=safe_name(c.get("title","highlight")); out=outdir/f"{name}_{int(a)}_16x9.mp4"
    W=int(cfg.get("longform",{}).get("width",1920)); H=int(cfg.get("longform",{}).get("height",1080)); fps=int(cfg["output"].get("fps",30))
    ass=None
    ccfg=cfg.get("captions",{})
    if subtitles and ccfg.get("enabled",True):
        ass=outdir/f"{name}_{int(a)}.ass"
        make_ass(t,a,b,ass,width=W,height=H,font_name=ccfg.get("font","Arial"),font_size=int(cfg.get("longform",{}).get("caption_font_size",56)),normal=ccfg.get("normal_color","&H00FFFFFF"),highlight=ccfg.get("highlight_color","&H00FF66CC"),outline=ccfg.get("outline_color","&H00000000"),outline_width=int(ccfg.get("outline_width",5)),shadow=int(ccfg.get("shadow",3)),margin_v=int(cfg.get("longform",{}).get("caption_margin_bottom",70)),pop_scale=int(ccfg.get("pop_scale",110)),max_words=int(ccfg.get("max_words",6)),max_chars=int(ccfg.get("max_chars",28)),max_line_chars=int(ccfg.get("max_line_chars",28)),pop_ms=int(ccfg.get("pop_ms",110)))
    vf="[0:v]scale={}:{}:force_original_aspect_ratio=decrease,pad={}:{}:(ow-iw)/2:(oh-ih)/2:color=black".format(W,H,W,H)
    if ass: vf += f",subtitles='{esc(ass)}'"
    branding=cfg.get("branding",{}); brand=str(branding.get("text","")).strip()
    if brand and bool(branding.get("enabled",True)):
        vf += f",drawtext=text='{esc_drawtext(brand)}':fontcolor=white:fontsize={int(branding.get('font_size',34))}:x=(w-text_w)/2:y=h-{int(branding.get('margin_bottom',34))}-text_h:box=1:boxcolor=black@0.45:boxborderw=12"
    vf += "[v]"
    streams=ffprobe_audio_streams(video)
    if len(streams)>=2 and cfg.get("output",{}).get("mix_game_and_mic",True):
        af="[0:a:0][0:a:1]amix=inputs=2:duration=longest:dropout_transition=0:normalize=1,aresample=async=1:first_pts=0[a]"
    else:
        af="[0:a:0]aresample=async=1:first_pts=0[a]"
    filt=vf+";"+af
    run(["ffmpeg","-y","-ss",ts(a),"-t",ts(dur),"-i",str(video),"-filter_complex",filt,"-map","[v]","-map","[a]","-c:v","h264_nvenc","-preset","p5","-b:v",cfg["output"].get("video_bitrate","12M"),"-maxrate",cfg["output"].get("video_bitrate","12M"),"-bufsize","24M","-r",str(fps),"-c:a","aac","-ac","2","-b:a",cfg["output"].get("audio_bitrate","192k"),"-movflags","+faststart","-shortest",str(out)])
    return out

def longform(video, clips, t, cfg, outdir):
    """Build a 16:9 chronological highlight reel from all approved clips."""
    outdir=Path(outdir); parts=outdir/"parts"; parts.mkdir(parents=True,exist_ok=True)
    ordered=sorted(clips,key=lambda c:float(c.get("final_start",0)))
    ps=[]
    for i,c in enumerate(ordered):
        x=dict(c); x["title"]=f"highlight_{i+1}_{c.get('title','clip')}"
        try: ps.append(render_landscape(video,x,t,cfg,parts,subtitles=bool(cfg.get("longform",{}).get("captions",False))))
        except Exception as e:
            print(f"[LONGFORM] skipped {i+1}: {e}")
    if not ps: raise RuntimeError("No clips available for long-form compilation.")
    concat=outdir/"concat.txt"
    concat.write_text("\n".join(f"file '{p.resolve().as_posix()}'" for p in ps),encoding="utf-8")
    out=outdir/"highlights_16x9.mp4"
    run(["ffmpeg","-y","-f","concat","-safe","0","-i",str(concat),"-c","copy","-movflags","+faststart",str(out)])
    return out
