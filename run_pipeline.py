try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

import sys
from pathlib import Path
from solarohith.pipeline import run_pipeline

if len(sys.argv) != 2:
    print('Usage: python run_pipeline.py "YOUTUBE_URL_OR_LOCAL_VIDEO"')
    raise SystemExit(2)

root = Path(__file__).resolve().parent
run_pipeline(sys.argv[1], root, root / "config.yaml", print)
