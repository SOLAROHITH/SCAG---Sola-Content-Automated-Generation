# SCAG Windows setup

This build is intended to run from the extracted project folder on any drive,
including X:.

## 1. Create the venv

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

If PowerShell blocks activation:

```powershell
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
```

## 2. Verify dependencies

```powershell
python -c "import faster_whisper; print('faster-whisper: OK')"
python -c "import openai; print('OpenAI client: OK')"
python -m app.cli --help
```

## 3. Ollama

SCAG is configured for:

```text
http://127.0.0.1:11434/v1
qwen3.6:latest
```

Ollama must be running before Qwen analysis.

## 4. Manual Short

```powershell
python -m app.cli manual-clip "X:\Videos\stream.mp4" --start 00:01:20 --end 00:01:52 --format short --padding 10 --title clutch
```

YouTube URLs work in the same command.

Shorts have animated word-level captions enabled by default.

## 5. Manual Long

```powershell
python -m app.cli manual-clip "X:\Videos\stream.mp4" --start 00:01:20 --end 00:08:30 --format long --title segment
```

Long-form rendering is clean by default.

## 6. Overnight

```powershell
python run_pipeline.py "X:\Videos\stream.mp4"
```

or a YouTube VOD URL.

The overnight pipeline:
1. downloads/resolves the complete source;
2. extracts audio;
3. transcribes the complete stream with word timestamps;
4. creates a broad candidate index;
5. samples visual evidence;
6. gives Qwen the complete timestamped transcript + candidate leads + visual evidence;
7. lets Qwen choose editorial moments;
8. expands each selected core by default 10 seconds before/after;
9. renders Shorts with NVENC and animated captions;
10. optionally creates a clean long-form compilation.
