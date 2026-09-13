from pathlib import Path
import streamlit as st
from modes.manual.pipeline import ManualPipeline

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config.yaml"

st.set_page_config(page_title="SCAG Manual Clip", page_icon="🎬", layout="wide")
st.title("🎬 SCAG Manual Clip")

source = st.text_input(
    "Local video path or YouTube URL",
    placeholder=r"X:\Videos\stream.mp4 or https://www.youtube.com/watch?v=...",
)
col1, col2 = st.columns(2)
with col1:
    start = st.text_input("Start", "00:01:20")
with col2:
    end = st.text_input("End", "00:01:52")

fmt = st.selectbox("Output", ["short", "long"])
padding = st.number_input("Extra context (seconds)", min_value=0.0, value=0.0, step=1.0)
title = st.text_input("Title", "manual_clip")
subs = st.checkbox("Animated captions", value=(fmt == "short"))

if st.button("Render", type="primary", disabled=not source):
    try:
        from app.cli import parse_time
        storage = ROOT / "projects"
        result = ManualPipeline(str(CONFIG), str(storage)).run(
            source, parse_time(start), parse_time(end),
            fmt=fmt, padding=padding, title=title, subtitles=subs
        )
        st.success(result["output_path"])
        st.json(result)
    except Exception as e:
        st.error(str(e))
        st.exception(e)
