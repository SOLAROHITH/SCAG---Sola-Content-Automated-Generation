import argparse
from pathlib import Path

from modes.manual.pipeline import ManualPipeline


def parse_time(value: str) -> float:
    value = str(value).strip()
    if ":" not in value:
        return float(value)
    parts = value.split(":")
    if len(parts) == 2:
        m, s = parts
        return int(m) * 60 + float(s)
    if len(parts) == 3:
        h, m, s = parts
        return int(h) * 3600 + int(m) * 60 + float(s)
    raise ValueError(f"Invalid time: {value}")


def main():
    parser = argparse.ArgumentParser(
        prog="scag",
        description="Solarohith Content Automated Generation",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    manual = sub.add_parser(
        "manual-clip",
        help="Create a Short or Long clip from a local video or YouTube URL.",
    )
    manual.add_argument("source", help="Local video path or YouTube URL")
    manual.add_argument("--start", required=True, help="Start time, e.g. 00:01:20")
    manual.add_argument("--end", required=True, help="End time, e.g. 00:01:52")
    manual.add_argument("--format", choices=["short", "long"], default="short")
    manual.add_argument("--padding", type=float, default=0.0)
    manual.add_argument("--title", default="manual_clip")
    manual.add_argument(
        "--subtitles",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Enable/disable animated captions (Short defaults on, Long off).",
    )
    manual.add_argument("--config", default="config.yaml")
    manual.add_argument("--storage", default=None)

    args = parser.parse_args()

    if args.command == "manual-clip":
        cfg_path = Path(args.config).resolve()
        storage = args.storage or str(cfg_path.parent / "projects")
        result = ManualPipeline(config=str(cfg_path), storage=storage).run(
            source=args.source,
            start=parse_time(args.start),
            end=parse_time(args.end),
            fmt=args.format,
            padding=args.padding,
            title=args.title,
            subtitles=args.subtitles,
        )
        print(f"SUCCESS: {result['output_path']}")


if __name__ == "__main__":
    main()
