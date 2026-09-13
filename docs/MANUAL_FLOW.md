# SCAG Manual Flow

The manual flow uses the exact same rendering contract as the overnight machine.

```text
Local video / YouTube URL
        ↓
Source resolver
        ↓
ClipRequest
        ↓
Shared Renderer
        ↓
FFmpeg
        ↓
QC
        ↓
storage/projects/manual/renders/{short|long}/
```

## CLI

```bash
python -m app.cli manual-clip VIDEO_OR_URL \
  --start 00:01:20 \
  --end 00:01:52 \
  --format short \
  --padding 10 \
  --title marvel_rivals_clutch
```

For long-form:

```bash
python -m app.cli manual-clip VIDEO_OR_URL \
  --start 00:01:20 \
  --end 00:08:30 \
  --format long \
  --title full_segment
```

## UI

```bash
streamlit run app/manual_ui.py
```

The important architectural point is that ManualPipeline does not implement a second FFmpeg system. It creates the same `ClipRequest` used by SCAG's automated pipeline and hands it to the shared renderer.
