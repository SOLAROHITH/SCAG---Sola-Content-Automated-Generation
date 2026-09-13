# SCAG Animated Captions

SCAG Shorts use a real word-synchronous caption layer generated from Whisper
word timestamps and burned into the final MP4 with FFmpeg/libass.

## Style

- large bold centered captions;
- white normal words;
- the currently spoken word changes to the configured highlight colour;
- the active word performs a small scale "pop";
- heavier outline on the active word;
- automatic compact 1–2 line grouping;
- lower-center safe placement;
- exact word timing comes from Whisper.

The caption look is controlled from `config.yaml`; you do not need to edit
the Python caption engine for normal style changes.

```yaml
captions:
  font: Arial
  font_size: 76
  normal_color: "&H00FFFFFF"
  highlight_color: "&H00FF66CC"
  outline_color: "&H00000000"
  outline_width: 6
  shadow: 3
  pop_scale: 110
  pop_ms: 110
  margin_bottom: 470
  max_words: 6
  max_chars: 28
  max_line_chars: 18
```

## Important: Outplayed microphone track

Outplayed commonly stores game/system audio and microphone audio on separate
tracks. The official Overwolf support documentation gives Track 1 as game
sound and Track 2 as microphone.

SCAG therefore uses `audio_stream: auto` for transcription. When multiple
audio streams exist it prefers audio stream index `1` (the second audio
track), which is the microphone track in the normal Outplayed layout. If only
one stream exists, it automatically uses stream `0`.

This fixes a common failure mode where SCAG transcribes game/announcer audio
instead of what the creator actually said.

The final Short also mixes Outplayed audio streams 0 + 1 into one normal AAC
track, so the exported social video contains both game audio and microphone
audio.

## Manual

Manual mode transcribes only the selected time range. Shorts default to
animated captions. Re-running a manual clip forces a fresh transcription so a
previously cached transcript from the wrong audio track cannot be reused.

## Overnight

The full-stream Whisper pass creates word timestamps once. Those timestamps
are reused for selected Shorts, so Overnight Mode does not retranscribe each
selected clip.

## Long-form

Long-form remains clean by default. The same editor can render captions later
with `--subtitles`.

## Requirements

- faster-whisper with word timestamps
- FFmpeg with the `subtitles` filter / libass support
- CUDA is recommended for the configured large-v3 Whisper model


## Audio troubleshooting

For transcription:

```yaml
transcription:
  audio_stream: auto
  audio_channel: auto
```

`auto` first looks for a stream labelled mic/voice/commentary, then uses the
normal Outplayed microphone stream when multiple tracks exist. For a single
stereo recording, `auto` mixes both channels. If your microphone is isolated
to one side, use `left` or `right`.

The temporary ASR audio is speech-filtered; the final video audio is not.

If the source has only one mixed game+microphone stream, SCAG cannot mathematically
separate the two after recording. In that case, the best fix is to record the
microphone as a separate track in Outplayed/OBS.
