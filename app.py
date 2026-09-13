try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

import sys
from pathlib import Path
import streamlit as st

from solarohith.pipeline import run_pipeline

ROOT = Path(__file__).resolve().parent
CONFIG = ROOT / "config.yaml"

st.set_page_config(
    page_title="Solarohith Content Machine",
    page_icon="🎬",
    layout="wide",
)
st.title("🎬 Solarohith Content Machine")
st.caption("Full stream -> transcript + candidates -> Qwen editor -> FFmpeg/NVENC Shorts")

source = st.text_input(
    "Local video path or YouTube VOD URL",
    placeholder=r"X:\path\to\stream.mp4 or https://www.youtube.com/watch?v=...",
)

if st.button("🚀 Start Overnight Pipeline", type="primary", disabled=not source):
    box = st.empty()
    lines = []

    def progress(x):
        lines.append(str(x))
        box.code("\n".join(lines[-40:]))

    try:
        result = run_pipeline(source, ROOT, CONFIG, progress)
        st.success("Finished.")
        st.json(result)
    except Exception as e:
        st.error(str(e))
        st.exception(e)

with st.expander("Configuration"):
    st.code(CONFIG.read_text(encoding="utf-8"), language="yaml")
