#!/usr/bin/env python3
"""Prepare a short, web-ready soundboard clip with optional vocal isolation."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import tempfile
from pathlib import Path


def run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        check=True,
        capture_output=True,
        text=True,
    )


def require_command(name: str) -> str:
    path = shutil.which(name)
    if not path:
        raise SystemExit(
            f"{name} is required but was not found. "
            "See the audio workflow in README.md."
        )
    return path


def parse_loudnorm(stderr: str) -> dict[str, str]:
    start = stderr.rfind("{")
    end = stderr.rfind("}")
    if start < 0 or end < start:
        raise RuntimeError("FFmpeg did not return loudness measurements.")
    return json.loads(stderr[start : end + 1])


def separate_vocals(source: Path, working_dir: Path) -> Path:
    demucs = require_command("demucs")
    run(
        [
            demucs,
            "--two-stems",
            "vocals",
            "-n",
            "htdemucs_ft",
            "-o",
            str(working_dir),
            str(source),
        ]
    )
    vocal = working_dir / "htdemucs_ft" / source.stem / "vocals.wav"
    if not vocal.exists():
        raise RuntimeError(f"Expected Demucs vocal stem was not created: {vocal}")
    return vocal


def prepare_clip(
    source: Path,
    output: Path,
    start: float,
    duration: float,
    isolate_vocals: bool,
) -> None:
    ffmpeg = require_command("ffmpeg")
    output.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="bhangra-board-audio-") as temp_name:
        working_dir = Path(temp_name)
        input_path = (
            separate_vocals(source, working_dir) if isolate_vocals else source
        )

        trim_args = ["-ss", str(start), "-t", str(duration)]
        analysis_filter = (
            "highpass=f=80,"
            "loudnorm=I=-16:LRA=7:TP=-1:print_format=json"
        )
        analysis = subprocess.run(
            [
                ffmpeg,
                "-hide_banner",
                "-nostats",
                *trim_args,
                "-i",
                str(input_path),
                "-af",
                analysis_filter,
                "-f",
                "null",
                "-",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        measured = parse_loudnorm(analysis.stderr)

        fade_out_start = max(0.0, duration - 0.04)
        final_filter = (
            "highpass=f=80,"
            "loudnorm="
            "I=-16:LRA=7:TP=-1:"
            f"measured_I={measured['input_i']}:"
            f"measured_LRA={measured['input_lra']}:"
            f"measured_TP={measured['input_tp']}:"
            f"measured_thresh={measured['input_thresh']}:"
            f"offset={measured['target_offset']}:"
            "linear=true,"
            "afade=t=in:st=0:d=0.025,"
            f"afade=t=out:st={fade_out_start}:d=0.04"
        )

        run(
            [
                ffmpeg,
                "-hide_banner",
                "-y",
                *trim_args,
                "-i",
                str(input_path),
                "-af",
                final_filter,
                "-ar",
                "44100",
                "-ac",
                "2",
                "-codec:a",
                "libmp3lame",
                "-b:a",
                "160k",
                str(output),
            ]
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--start", type=float, default=0.0)
    parser.add_argument("--duration", type=float, required=True)
    parser.add_argument(
        "--isolate-vocals",
        action="store_true",
        help="Run the fine-tuned HTDemucs vocal stem before cleanup.",
    )
    args = parser.parse_args()

    if not args.source.is_file():
        raise SystemExit(f"Source does not exist: {args.source}")
    if args.start < 0 or args.duration <= 0:
        raise SystemExit("--start must be non-negative and --duration must be positive.")

    prepare_clip(
        args.source.resolve(),
        args.output.resolve(),
        args.start,
        args.duration,
        args.isolate_vocals,
    )


if __name__ == "__main__":
    main()

