#!/usr/bin/env python3
"""Batch vocal-isolation, review rendering, and approval publishing tools."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence


BASE_DIR = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = BASE_DIR / "content" / "clips.json"
DEFAULT_PROFILES = BASE_DIR / "content" / "audio_profiles.json"
DEFAULT_SELECTION = BASE_DIR / "content" / "audio_lab_selection.json"
DEFAULT_SOURCE_DIR = BASE_DIR / "media" / "source"
DEFAULT_STEM_DIR = BASE_DIR / "media" / "stems"
DEFAULT_MODEL_DIR = BASE_DIR / "media" / "models"
DEFAULT_REVIEW_DIR = BASE_DIR / "media" / "review"
DEFAULT_MASTER_DIR = BASE_DIR / "media" / "masters"
DEFAULT_QUEUE = DEFAULT_REVIEW_DIR / "queue.json"
DEFAULT_REVIEWS = DEFAULT_REVIEW_DIR / "reviews.json"
STATIC_DIR = BASE_DIR / "static"
MIN_SEPARATOR_SECONDS = 12.0
DEFAULT_SOURCE_CONTEXT_SECONDS = 1.5
MAX_SOURCE_EXCERPT_SECONDS = 30.0
MAX_SOURCE_CONTEXT_SECONDS = 10.0

AUDIO_EXTENSIONS = (".wav", ".flac", ".aiff", ".aif", ".m4a", ".mp3")
SAFE_ID = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
SAFE_FIELD_ID = re.compile(r"^[a-z][a-z0-9_]*$")

VOCAL_FILTER = (
    "highpass=f=70,"
    "acompressor=threshold=0.125:ratio=3:attack=5:release=80:makeup=1.5,"
    "loudnorm=I=-16:LRA=7:TP=-1.5,"
    "aeval='if(isnan(val(ch))+isinf(val(ch)),0,val(ch))':c=same,"
    "afade=t=in:st=0:d=0.025"
)
REFERENCE_FILTER = (
    "highpass=f=70,"
    "loudnorm=I=-16:LRA=7:TP=-1.5,"
    "aeval='if(isnan(val(ch))+isinf(val(ch)),0,val(ch))':c=same,"
    "afade=t=in:st=0:d=0.025"
)


class AudioLabError(RuntimeError):
    """A user-facing audio lab error."""


@dataclass(frozen=True)
class Profile:
    id: str
    label: str
    model: str
    description: str
    extra_args: tuple[str, ...] = ()


def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise AudioLabError(f"Required file does not exist: {path}") from exc
    except json.JSONDecodeError as exc:
        raise AudioLabError(f"Invalid JSON in {path}: {exc}") from exc


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(
        json.dumps(value, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_catalog_selection(
    clips: Sequence[dict[str, Any]],
    path: Path = DEFAULT_SELECTION,
) -> list[dict[str, Any]]:
    """Return every enabled clip plus the explicitly staged AudioLab catalog."""
    payload = read_json(path)
    if not isinstance(payload, dict) or payload.get("version") != 1:
        raise AudioLabError(f"{path} must contain version 1 selection data.")
    staged_ids = payload.get("staged_clip_ids")
    if not isinstance(staged_ids, list) or not all(
        isinstance(clip_id, str) and SAFE_ID.fullmatch(clip_id)
        for clip_id in staged_ids
    ):
        raise AudioLabError(f"{path}: staged_clip_ids must be a list of safe ids.")
    if len(staged_ids) != len(set(staged_ids)):
        raise AudioLabError(f"{path}: staged_clip_ids must be unique.")

    by_id = {clip["id"]: clip for clip in clips}
    unknown = sorted(set(staged_ids) - set(by_id))
    if unknown:
        raise AudioLabError(
            f"{path}: unknown staged clip ids: {', '.join(unknown)}"
        )
    enabled = [clip for clip in clips if clip["enabled"]]
    invalid = [clip_id for clip_id in staged_ids if by_id[clip_id]["enabled"]]
    if invalid:
        raise AudioLabError(
            f"{path}: staged ids must remain disabled: {', '.join(invalid)}"
        )
    return [*enabled, *(by_id[clip_id] for clip_id in staged_ids)]


def load_profiles(path: Path = DEFAULT_PROFILES) -> tuple[dict[str, Any], list[Profile]]:
    payload = read_json(path)
    if not isinstance(payload, dict) or payload.get("version") != 1:
        raise AudioLabError(f"{path} must contain version 1 profile data.")

    raw_profiles = payload.get("profiles")
    if not isinstance(raw_profiles, list) or not raw_profiles:
        raise AudioLabError(f"{path} must define at least one profile.")

    profiles: list[Profile] = []
    seen: set[str] = set()
    for raw in raw_profiles:
        if not isinstance(raw, dict):
            raise AudioLabError("Every audio profile must be an object.")
        profile_id = raw.get("id")
        if not isinstance(profile_id, str) or not SAFE_ID.fullmatch(profile_id):
            raise AudioLabError(f"Unsafe audio profile id: {profile_id!r}")
        if profile_id in seen:
            raise AudioLabError(f"Duplicate audio profile id: {profile_id}")
        seen.add(profile_id)

        extra_args = raw.get("extra_args", [])
        if not isinstance(extra_args, list) or not all(
            isinstance(value, str) and value.startswith("--") for value in extra_args
        ):
            raise AudioLabError(
                f"{profile_id}: extra_args must contain CLI option strings."
            )

        for field in ("label", "model", "description"):
            if not isinstance(raw.get(field), str) or not raw[field].strip():
                raise AudioLabError(f"{profile_id}: {field} is required.")

        profiles.append(
            Profile(
                id=profile_id,
                label=raw["label"],
                model=raw["model"],
                description=raw["description"],
                extra_args=tuple(extra_args),
            )
        )

    bed_db = payload.get("voice_forward_bed_db")
    if not isinstance(bed_db, (int, float)) or not -36 <= float(bed_db) <= -6:
        raise AudioLabError("voice_forward_bed_db must be between -36 and -6 dB.")

    quality_fields = payload.get("quality_fields")
    if not isinstance(quality_fields, list) or not quality_fields:
        raise AudioLabError("At least one quality field is required.")
    quality_ids = [field.get("id") for field in quality_fields if isinstance(field, dict)]
    if len(quality_ids) != len(set(quality_ids)) or not all(
        isinstance(field_id, str) and SAFE_FIELD_ID.fullmatch(field_id)
        for field_id in quality_ids
    ):
        raise AudioLabError("Quality field ids must be unique safe identifiers.")

    return payload, profiles


def load_manifest_clips(path: Path = DEFAULT_MANIFEST) -> list[dict[str, Any]]:
    clips = read_json(path)
    if not isinstance(clips, list):
        raise AudioLabError(f"{path} must contain a JSON list.")
    if not all(isinstance(clip, dict) for clip in clips):
        raise AudioLabError(f"{path} must contain only clip objects.")
    seen: set[str] = set()
    for clip in clips:
        clip_id = clip.get("id")
        if not isinstance(clip_id, str) or not SAFE_ID.fullmatch(clip_id):
            raise AudioLabError(f"Unsafe or missing clip id: {clip_id!r}")
        if clip_id in seen:
            raise AudioLabError(f"Duplicate clip id: {clip_id}")
        seen.add(clip_id)
        if not isinstance(clip.get("enabled"), bool):
            raise AudioLabError(f"{clip_id}: enabled must be true or false.")
        for field in ("artist", "phrase", "audio"):
            if not isinstance(clip.get(field), str) or not clip[field]:
                raise AudioLabError(f"{clip_id}: {field} is required.")
        review_start = clip.get("review_start", 0)
        review_duration = clip.get("review_duration")
        if (
            isinstance(review_start, bool)
            or not isinstance(review_start, (int, float))
            or not math.isfinite(float(review_start))
            or review_start < 0
        ):
            raise AudioLabError(f"{clip_id}: review_start must be zero or greater.")
        if review_duration is not None and (
            isinstance(review_duration, bool)
            or not isinstance(review_duration, (int, float))
            or not math.isfinite(float(review_duration))
            or review_duration <= 0
        ):
            raise AudioLabError(f"{clip_id}: review_duration must be greater than zero.")
        if clip.get("source_provider") == "youtube" and not clip["enabled"]:
            validate_staged_youtube_source(clip)
    return clips


def load_enabled_clips(path: Path = DEFAULT_MANIFEST) -> list[dict[str, Any]]:
    return [clip for clip in load_manifest_clips(path) if clip["enabled"]]


def select_by_id(
    values: Sequence[Any],
    requested: Sequence[str] | None,
    *,
    label: str,
) -> list[Any]:
    if not requested:
        return list(values)
    by_id = {value.id if isinstance(value, Profile) else value["id"]: value for value in values}
    unknown = sorted(set(requested) - set(by_id))
    if unknown:
        raise AudioLabError(f"Unknown {label}: {', '.join(unknown)}")
    return [by_id[value_id] for value_id in requested]


def resolve_source(
    clip: dict[str, Any],
    source_dir: Path = DEFAULT_SOURCE_DIR,
    static_dir: Path = STATIC_DIR,
) -> Path:
    source_candidates = [
        source_dir / f"{clip['id']}{extension}" for extension in AUDIO_EXTENSIONS
    ]
    existing = [candidate for candidate in source_candidates if candidate.is_file()]
    if len(existing) > 1:
        joined = ", ".join(str(path) for path in existing)
        raise AudioLabError(f"{clip['id']}: multiple raw sources found: {joined}")
    if existing:
        return existing[0]

    fallback = static_dir / clip["audio"]
    if not fallback.is_file():
        raise AudioLabError(
            f"{clip['id']}: no raw source and deployed clip is missing: {fallback}"
        )
    return fallback


def require_executable(name: str) -> str:
    sibling = Path(sys.executable).parent / name
    if sibling.is_file():
        return str(sibling)
    resolved = shutil.which(name)
    if not resolved:
        raise AudioLabError(
            f"{name} was not found. Run `make audio-install` and retry."
        )
    return resolved


def separator_command(
    source: Path,
    profile: Profile,
    output_dir: Path,
    model_dir: Path = DEFAULT_MODEL_DIR,
) -> list[str]:
    return [
        require_executable("audio-separator"),
        "--model_filename",
        profile.model,
        "--output_format",
        "WAV",
        "--output_dir",
        str(output_dir),
        "--model_file_dir",
        str(model_dir),
        "--single_stem",
        "Vocals",
        "--sample_rate",
        "44100",
        "--custom_output_names",
        json.dumps({"Vocals": "vocals"}, separators=(",", ":")),
        *profile.extra_args,
        str(source),
    ]


def run_command(command: Sequence[str]) -> None:
    subprocess.run(list(command), check=True)


def has_cached_output(path: Path, inputs: Iterable[Path] = ()) -> bool:
    """Return whether an output is non-empty and no older than its inputs."""
    if not path.is_file() or path.stat().st_size <= 0:
        return False
    output_mtime = path.stat().st_mtime_ns
    for source in inputs:
        if (
            not source.is_file()
            or source.stat().st_size <= 0
            or output_mtime < source.stat().st_mtime_ns
        ):
            return False
    return True


def clip_review_window(clip: dict[str, Any]) -> tuple[float, float | None]:
    start = float(clip.get("review_start", 0))
    raw_duration = clip.get("review_duration")
    duration = float(raw_duration) if raw_duration is not None else None
    return start, duration


def source_download_window(clip: dict[str, Any]) -> tuple[float, float, float, float]:
    """Return download start/end plus the core review start/duration."""
    clip_id = clip["id"]
    core_start = clip.get("source_start_seconds")
    core_end = clip.get("source_end_seconds")
    context = clip.get("source_context_seconds", DEFAULT_SOURCE_CONTEXT_SECONDS)
    if (
        isinstance(core_start, bool)
        or isinstance(core_end, bool)
        or not isinstance(core_start, (int, float))
        or not isinstance(core_end, (int, float))
        or not math.isfinite(float(core_start))
        or not math.isfinite(float(core_end))
        or core_start < 0
        or core_end <= core_start
    ):
        raise AudioLabError(
            f"{clip_id}: source_start_seconds/source_end_seconds must define "
            "a positive excerpt."
        )
    if (
        isinstance(context, bool)
        or not isinstance(context, (int, float))
        or not math.isfinite(float(context))
        or context < 0
    ):
        raise AudioLabError(
            f"{clip_id}: source_context_seconds must be zero or greater."
        )
    if float(core_end) - float(core_start) > MAX_SOURCE_EXCERPT_SECONDS:
        raise AudioLabError(
            f"{clip_id}: staged excerpts cannot exceed "
            f"{MAX_SOURCE_EXCERPT_SECONDS:g} seconds."
        )
    if float(context) > MAX_SOURCE_CONTEXT_SECONDS:
        raise AudioLabError(
            f"{clip_id}: source context cannot exceed "
            f"{MAX_SOURCE_CONTEXT_SECONDS:g} seconds per side."
        )

    download_start = max(0.0, float(core_start) - float(context))
    download_end = float(core_end) + float(context)
    review_start = float(core_start) - download_start
    review_duration = float(core_end) - float(core_start)
    return download_start, download_end, review_start, review_duration


def validate_staged_youtube_source(clip: dict[str, Any]) -> None:
    """Validate reproducible metadata for a disabled YouTube review candidate."""
    clip_id = clip["id"]
    if clip["enabled"]:
        raise AudioLabError(
            f"{clip_id}: YouTube review candidates must remain disabled."
        )
    source_url = clip.get("source_url")
    if not isinstance(source_url, str) or not source_url.startswith(
        ("https://www.youtube.com/", "https://youtu.be/")
    ):
        raise AudioLabError(f"{clip_id}: a canonical YouTube source_url is required.")
    if not isinstance(clip.get("clearance_status"), str) or not clip[
        "clearance_status"
    ].strip():
        raise AudioLabError(f"{clip_id}: clearance_status is required.")

    _, _, expected_start, expected_duration = source_download_window(clip)
    declared_start, declared_duration = clip_review_window(clip)
    if declared_duration is None or (
        abs(declared_start - expected_start) > 0.001
        or abs(declared_duration - expected_duration) > 0.001
    ):
        raise AudioLabError(
            f"{clip_id}: review_start/review_duration must match the source "
            "timecode and context."
        )


def fetch_external_source(
    clip: dict[str, Any],
    source_dir: Path = DEFAULT_SOURCE_DIR,
    *,
    force: bool = False,
) -> Path:
    """Fetch one explicitly staged YouTube excerpt with context handles."""
    clip_id = clip["id"]
    if clip["enabled"]:
        raise AudioLabError(
            f"{clip_id}: source fetching is limited to disabled review candidates."
        )
    if clip.get("source_provider") != "youtube":
        raise AudioLabError(f"{clip_id}: only staged YouTube sources are supported.")
    validate_staged_youtube_source(clip)
    source_url = clip["source_url"]

    download_start, download_end, _, _ = (
        source_download_window(clip)
    )
    declared_start, declared_duration = clip_review_window(clip)
    assert declared_duration is not None

    source_dir.mkdir(parents=True, exist_ok=True)
    existing = [
        source_dir / f"{clip_id}{extension}" for extension in AUDIO_EXTENSIONS
    ]
    existing = [path for path in existing if path.is_file()]
    if existing and not force:
        if len(existing) > 1:
            joined = ", ".join(str(path) for path in existing)
            raise AudioLabError(f"{clip_id}: multiple raw sources found: {joined}")
        return existing[0]
    for path in existing:
        path.unlink()

    output_template = source_dir / f"{clip_id}.%(ext)s"
    audio_formats = (
        "bestaudio[ext=m4a]/bestaudio/best",
        "bestaudio/best",
    )
    last_error: subprocess.CalledProcessError | None = None
    for audio_format in audio_formats:
        try:
            run_command(
                [
                    require_executable("yt-dlp"),
                    "--no-playlist",
                    "--format",
                    audio_format,
                    "--download-sections",
                    f"*{download_start:g}-{download_end:g}",
                    "--force-keyframes-at-cuts",
                    "--extract-audio",
                    "--audio-format",
                    "wav",
                    "--audio-quality",
                    "0",
                    "--output",
                    str(output_template),
                    source_url,
                ]
            )
            break
        except subprocess.CalledProcessError as exc:
            last_error = exc
            for artifact in source_dir.glob(f"{clip_id}.*"):
                artifact.unlink()
    else:
        assert last_error is not None
        raise last_error
    fetched = [
        source_dir / f"{clip_id}{extension}" for extension in AUDIO_EXTENSIONS
    ]
    fetched = [path for path in fetched if path.is_file()]
    if len(fetched) != 1:
        raise AudioLabError(
            f"{clip_id}: expected one fetched audio source, found {len(fetched)}."
        )
    duration = audio_duration(fetched[0])
    required_duration = declared_start + declared_duration
    if duration + 0.05 < required_duration:
        raise AudioLabError(
            f"{clip_id}: fetched {duration:.3f}s, but the review window needs "
            f"{required_duration:.3f}s."
        )
    return fetched[0]


def windowed_filter(
    audio_filter: str,
    start: float = 0,
    duration: float | None = None,
) -> str:
    if start == 0 and duration is None:
        return audio_filter
    trim_parts = [f"start={start:g}"]
    if duration is not None:
        trim_parts.append(f"duration={duration:g}")
    return (
        f"atrim={':'.join(trim_parts)},"
        f"asetpts=PTS-STARTPTS,"
        f"{audio_filter}"
    )


def audio_duration(source: Path) -> float:
    completed = subprocess.run(
        [
            require_executable("ffprobe"),
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(source),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    try:
        duration = float(completed.stdout.strip())
    except ValueError as exc:
        raise AudioLabError(f"Could not measure audio duration for {source}.") from exc
    if duration <= 0:
        raise AudioLabError(f"Audio source has no usable duration: {source}")
    return duration


def pad_separator_input(source: Path, output: Path, duration: float) -> Path:
    output.parent.mkdir(parents=True, exist_ok=True)
    run_command(
        [
            require_executable("ffmpeg"),
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(source),
            "-af",
            f"apad=whole_dur={MIN_SEPARATOR_SECONDS}",
            "-t",
            str(MIN_SEPARATOR_SECONDS),
            "-ar",
            "44100",
            "-ac",
            "2",
            "-codec:a",
            "pcm_s16le",
            str(output),
        ]
    )
    print(
        f"  padded {duration:.2f}s source to {MIN_SEPARATOR_SECONDS:.0f}s "
        "for model inference",
        flush=True,
    )
    return output


def trim_separator_output(source: Path, output: Path, duration: float) -> Path:
    run_command(
        [
            require_executable("ffmpeg"),
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(source),
            "-af",
            f"atrim=start=0:end={duration},asetpts=PTS-STARTPTS",
            "-ar",
            "44100",
            "-ac",
            "2",
            "-codec:a",
            "pcm_s16le",
            str(output),
        ]
    )
    return output


def find_vocal_output(output_dir: Path) -> Path:
    expected = output_dir / "vocals.wav"
    if has_cached_output(expected):
        return expected
    matches = sorted(
        path
        for path in output_dir.glob("*.wav")
        if "vocal" in path.name.casefold() and has_cached_output(path)
    )
    if len(matches) == 1:
        matches[0].replace(expected)
        return expected
    raise AudioLabError(
        f"Expected one vocal WAV in {output_dir}, found {len(matches)}."
    )


def separate_vocal(
    source: Path,
    clip_id: str,
    profile: Profile,
    stem_root: Path = DEFAULT_STEM_DIR,
    model_dir: Path = DEFAULT_MODEL_DIR,
    *,
    force: bool = False,
) -> Path:
    output_dir = stem_root / clip_id / profile.id
    output_dir.mkdir(parents=True, exist_ok=True)
    vocal = output_dir / "vocals.wav"
    if has_cached_output(vocal, (source,)) and not force:
        return vocal
    if vocal.exists():
        vocal.unlink()

    print(f"[{clip_id}] separating with {profile.label}", flush=True)
    duration = audio_duration(source)
    separator_source = source
    padded_input = output_dir / "_separator-input.wav"
    if duration < MIN_SEPARATOR_SECONDS:
        separator_source = pad_separator_input(source, padded_input, duration)

    run_command(separator_command(separator_source, profile, output_dir, model_dir))
    separated = find_vocal_output(output_dir)
    if separator_source == source:
        return separated

    padded_vocal = output_dir / "_separator-vocals.wav"
    separated.replace(padded_vocal)
    trim_separator_output(padded_vocal, vocal, duration)
    padded_input.unlink(missing_ok=True)
    padded_vocal.unlink(missing_ok=True)
    return vocal


def encode_reference(
    source: Path,
    output: Path,
    *,
    start: float = 0,
    duration: float | None = None,
    force: bool = False,
) -> Path:
    if has_cached_output(output, (source,)) and not force:
        return output
    output.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg = require_executable("ffmpeg")
    run_command(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(source),
            "-af",
            windowed_filter(REFERENCE_FILTER, start, duration),
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
    return output


def encode_vocal(
    vocal: Path,
    output: Path,
    *,
    start: float = 0,
    duration: float | None = None,
    force: bool = False,
) -> Path:
    if has_cached_output(output, (vocal,)) and not force:
        return output
    output.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg = require_executable("ffmpeg")
    run_command(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(vocal),
            "-af",
            windowed_filter(VOCAL_FILTER, start, duration),
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
    return output


def encode_voice_forward(
    vocal: Path,
    source: Path,
    output: Path,
    bed_db: float,
    *,
    start: float = 0,
    duration: float | None = None,
    force: bool = False,
) -> Path:
    if has_cached_output(output, (vocal, source)) and not force:
        return output
    output.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg = require_executable("ffmpeg")
    vocal_filter = windowed_filter("highpass=f=70", start, duration)
    bed_filter = windowed_filter(f"volume={bed_db}dB", start, duration)
    filter_graph = (
        f"[0:a]{vocal_filter}[vocal];"
        f"[1:a]{bed_filter}[bed];"
        "[vocal][bed]amix=inputs=2:duration=first:dropout_transition=0,"
        "acompressor=threshold=0.125:ratio=3:attack=5:release=80:makeup=1.5,"
        "loudnorm=I=-16:LRA=7:TP=-1.5,"
        "aeval='if(isnan(val(ch))+isinf(val(ch)),0,val(ch))':c=same,"
        "afade=t=in:st=0:d=0.025[out]"
    )
    run_command(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(vocal),
            "-i",
            str(source),
            "-filter_complex",
            filter_graph,
            "-map",
            "[out]",
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
    return output


def render_profile_variants(
    clip_id: str,
    source: Path,
    vocal: Path,
    profile: Profile,
    review_root: Path,
    bed_db: float,
    *,
    start: float = 0,
    duration: float | None = None,
    force: bool = False,
) -> list[dict[str, str]]:
    clip_dir = review_root / clip_id
    vocal_output = clip_dir / f"{profile.id}-vocal.mp3"
    forward_output = clip_dir / f"{profile.id}-voice-forward.mp3"
    encode_vocal(
        vocal,
        vocal_output,
        start=start,
        duration=duration,
        force=force,
    )
    encode_voice_forward(
        vocal,
        source,
        forward_output,
        bed_db,
        start=start,
        duration=duration,
        force=force,
    )
    return [
        {
            "id": f"{profile.id}-vocal",
            "label": f"{profile.label} · vocal only",
            "profile": profile.id,
            "mix": "vocal",
            "path": str(vocal_output.relative_to(review_root)),
        },
        {
            "id": f"{profile.id}-voice-forward",
            "label": f"{profile.label} · voice forward",
            "profile": profile.id,
            "mix": "voice-forward",
            "path": str(forward_output.relative_to(review_root)),
        },
    ]


def sync_deployed_variant(
    clip: dict[str, Any],
    review_root: Path = DEFAULT_REVIEW_DIR,
    static_dir: Path = STATIC_DIR,
) -> Path:
    """Mirror the exact live board MP3 into AudioLab without re-encoding it."""
    source = static_dir / clip["audio"]
    if not source.is_file() or source.stat().st_size <= 0:
        raise AudioLabError(f"{clip['id']}: deployed board audio is missing.")
    output = review_root / clip["id"] / "deployed.mp3"
    output.parent.mkdir(parents=True, exist_ok=True)
    if not output.is_file() or file_sha256(output) != file_sha256(source):
        shutil.copy2(source, output)
    return output


def variants_for_clip(
    clip: dict[str, Any],
    profiles: Sequence[Profile],
    review_root: Path = DEFAULT_REVIEW_DIR,
    static_dir: Path = STATIC_DIR,
) -> list[dict[str, str]]:
    clip_dir = review_root / clip["id"]
    variants: list[dict[str, str]] = []
    if clip["enabled"]:
        deployed = sync_deployed_variant(clip, review_root, static_dir)
        variants.append(
            {
                "id": "deployed",
                "label": "Deployed board audio",
                "profile": "deployed",
                "mix": "deployed",
                "path": str(deployed.relative_to(review_root)),
                "sha256": file_sha256(deployed),
            }
        )
    reference = clip_dir / "reference.mp3"
    if has_cached_output(reference):
        variants.append(
            {
                "id": "reference",
                "label": "Current reference",
                "profile": "reference",
                "mix": "reference",
                "path": str(reference.relative_to(review_root)),
                "sha256": file_sha256(reference),
            }
        )
    for profile in profiles:
        for mix, suffix in (
            ("vocal", "vocal"),
            ("voice-forward", "voice-forward"),
        ):
            path = clip_dir / f"{profile.id}-{suffix}.mp3"
            if has_cached_output(path):
                variants.append(
                    {
                        "id": f"{profile.id}-{suffix}",
                        "label": (
                            f"{profile.label} · "
                            f"{'vocal only' if mix == 'vocal' else 'voice forward'}"
                        ),
                        "profile": profile.id,
                        "mix": mix,
                        "path": str(path.relative_to(review_root)),
                        "sha256": file_sha256(path),
                    }
                )
    return variants


def build_queue(
    clips: Sequence[dict[str, Any]],
    profiles_payload: dict[str, Any],
    profiles: Sequence[Profile],
    review_root: Path = DEFAULT_REVIEW_DIR,
    static_dir: Path = STATIC_DIR,
) -> dict[str, Any]:
    queue_clips = []
    for clip in clips:
        variants = variants_for_clip(clip, profiles, review_root, static_dir)
        if not variants:
            continue
        queue_clips.append(
            {
                "id": clip["id"],
                "artist": clip["artist"],
                "phrase": clip["phrase"],
                "current_grade": clip.get("grade"),
                "deployed_audio": clip["audio"],
                "variants": variants,
            }
        )
    return {
        "version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "quality_fields": profiles_payload["quality_fields"],
        "clips": queue_clips,
    }


def validate_review_bundle(
    queue: dict[str, Any],
    clips: Sequence[dict[str, Any]],
    profiles: Sequence[Profile],
    review_root: Path = DEFAULT_REVIEW_DIR,
) -> None:
    """Require a complete, non-empty, decodable comparison set."""
    expected_clip_ids = [clip["id"] for clip in clips]
    queue_clips = queue.get("clips")
    if not isinstance(queue_clips, list):
        raise AudioLabError("Review queue clips must be a list.")
    queue_ids = [clip.get("id") for clip in queue_clips if isinstance(clip, dict)]
    if queue_ids != expected_clip_ids:
        raise AudioLabError(
            "Review bundle is incomplete or out of order: expected "
            f"{len(expected_clip_ids)} clips, found {len(queue_ids)}."
        )

    base_variant_ids = {"reference"}
    for profile in profiles:
        base_variant_ids.update(
            {
                f"{profile.id}-vocal",
                f"{profile.id}-voice-forward",
            }
        )

    root = review_root.resolve()
    clips_by_id = {clip["id"]: clip for clip in clips}
    for clip in queue_clips:
        clip_id = clip["id"]
        expected_variant_ids = set(base_variant_ids)
        if clips_by_id[clip_id]["enabled"]:
            expected_variant_ids.add("deployed")
        variants = clip.get("variants")
        if not isinstance(variants, list):
            raise AudioLabError(f"{clip_id}: variants must be a list.")
        variant_ids = [variant.get("id") for variant in variants]
        if (
            len(variant_ids) != len(expected_variant_ids)
            or set(variant_ids) != expected_variant_ids
        ):
            raise AudioLabError(
                f"{clip_id}: expected {len(expected_variant_ids)} complete "
                f"review variants, found {len(variant_ids)}."
            )

        for variant in variants:
            relative = variant.get("path")
            if not isinstance(relative, str):
                raise AudioLabError(f"{clip_id}: review variant path is missing.")
            asset = (review_root / relative).resolve()
            if root not in asset.parents or not has_cached_output(asset):
                raise AudioLabError(
                    f"{clip_id}: review variant is unsafe, missing, or empty: "
                    f"{relative}"
                )
            try:
                audio_duration(asset)
            except (AudioLabError, subprocess.CalledProcessError) as exc:
                raise AudioLabError(
                    f"{clip_id}: review variant is not decodable: {relative}"
                ) from exc
            declared_hash = variant.get("sha256")
            if (
                not isinstance(declared_hash, str)
                or len(declared_hash) != 64
                or declared_hash != file_sha256(asset)
            ):
                raise AudioLabError(
                    f"{clip_id}: review variant hash is missing or stale: {relative}"
                )


def prepare(
    clips: Sequence[dict[str, Any]],
    profiles_payload: dict[str, Any],
    profiles: Sequence[Profile],
    *,
    source_dir: Path = DEFAULT_SOURCE_DIR,
    stem_root: Path = DEFAULT_STEM_DIR,
    model_dir: Path = DEFAULT_MODEL_DIR,
    review_root: Path = DEFAULT_REVIEW_DIR,
    queue_path: Path = DEFAULT_QUEUE,
    queue_profiles: Sequence[Profile] | None = None,
    force: bool = False,
) -> dict[str, Any]:
    bed_db = float(profiles_payload["voice_forward_bed_db"])
    for clip in clips:
        source = resolve_source(clip, source_dir)
        review_start, review_duration = clip_review_window(clip)
        if review_duration is not None:
            source_duration = audio_duration(source)
            if review_start + review_duration > source_duration + 0.05:
                raise AudioLabError(
                    f"{clip['id']}: review window ends at "
                    f"{review_start + review_duration:.3f}s, beyond the "
                    f"{source_duration:.3f}s source."
                )
        clip_review_dir = review_root / clip["id"]
        encode_reference(
            source,
            clip_review_dir / "reference.mp3",
            start=review_start,
            duration=review_duration,
            force=force,
        )
        for profile in profiles:
            vocal = separate_vocal(
                source,
                clip["id"],
                profile,
                stem_root,
                model_dir,
                force=force,
            )
            render_profile_variants(
                clip["id"],
                source,
                vocal,
                profile,
                review_root,
                bed_db,
                start=review_start,
                duration=review_duration,
                force=force,
            )
    effective_queue_profiles = (
        queue_profiles if queue_profiles is not None else profiles
    )
    queue = build_queue(
        clips,
        profiles_payload,
        effective_queue_profiles,
        review_root,
    )
    validate_review_bundle(
        queue,
        clips,
        effective_queue_profiles,
        review_root,
    )
    write_json(queue_path, queue)
    return queue


def publish_approved(
    queue: dict[str, Any],
    reviews: dict[str, Any],
    clips: Sequence[dict[str, Any]],
    *,
    review_root: Path = DEFAULT_REVIEW_DIR,
    master_root: Path = DEFAULT_MASTER_DIR,
    static_dir: Path = STATIC_DIR,
    requested: Sequence[str] | None = None,
    replace_static: bool = False,
) -> list[Path]:
    clip_by_id = {clip["id"]: clip for clip in clips}
    queue_by_id = {clip["id"]: clip for clip in queue.get("clips", [])}
    selected_ids = set(requested or queue_by_id)
    unknown = selected_ids - set(queue_by_id)
    if unknown:
        raise AudioLabError(f"Unknown review clips: {', '.join(sorted(unknown))}")

    published: list[Path] = []
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    for clip_id in sorted(selected_ids):
        review = reviews.get(clip_id)
        if not isinstance(review, dict) or review.get("decision") != "approve":
            continue
        selected_variant = review.get("selected_variant")
        variants = {
            variant["id"]: variant for variant in queue_by_id[clip_id]["variants"]
        }
        if selected_variant not in variants:
            raise AudioLabError(f"{clip_id}: approved variant is not in the queue.")
        selected = variants[selected_variant]
        selected_hash = selected.get("sha256")
        saved_hash = review.get("selected_variant_sha256")
        if saved_hash is not None and saved_hash != selected_hash:
            raise AudioLabError(
                f"{clip_id}: approved variant hash no longer matches the review."
            )

        relative = Path(selected["path"])
        source = (review_root / relative).resolve()
        root = review_root.resolve()
        if root not in source.parents or not source.is_file():
            raise AudioLabError(f"{clip_id}: unsafe or missing approved media path.")
        if (
            not isinstance(selected_hash, str)
            or len(selected_hash) != 64
            or file_sha256(source) != selected_hash
        ):
            raise AudioLabError(
                f"{clip_id}: approved media hash does not match the queue."
            )

        master = master_root / f"{clip_id}.mp3"
        master.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, master)
        published.append(master)

        if replace_static:
            deployed = static_dir / clip_by_id[clip_id]["audio"]
            if deployed.is_file():
                backup = master_root / "backups" / timestamp / deployed.name
                backup.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(deployed, backup)
            deployed.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, deployed)

    return published


def doctor(profiles: Sequence[Profile]) -> int:
    checks = {
        "python": sys.executable,
        "ffmpeg": shutil.which("ffmpeg"),
        "ffprobe": shutil.which("ffprobe"),
        "audio-separator": (
            str(Path(sys.executable).parent / "audio-separator")
            if (Path(sys.executable).parent / "audio-separator").is_file()
            else shutil.which("audio-separator")
        ),
        "yt-dlp": (
            str(Path(sys.executable).parent / "yt-dlp")
            if (Path(sys.executable).parent / "yt-dlp").is_file()
            else shutil.which("yt-dlp")
        ),
    }
    failed = False
    for name, value in checks.items():
        state = value or "MISSING"
        print(f"{name:16} {state}")
        failed = failed or value is None
    print(f"{'profiles':16} {len(profiles)} configured")
    for profile in profiles:
        print(f"{'':16} {profile.id}: {profile.model}")
    return 1 if failed else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--profiles", type=Path, default=DEFAULT_PROFILES)
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("doctor", help="Check local audio dependencies.")

    fetch_parser = subparsers.add_parser(
        "fetch",
        help="Fetch short, explicitly staged source excerpts with context handles.",
    )
    fetch_parser.add_argument("--clip", action="append", dest="clips")
    fetch_parser.add_argument("--force", action="store_true")

    prepare_parser = subparsers.add_parser(
        "prepare",
        help="Separate vocals and render review variants.",
    )
    prepare_parser.add_argument("--clip", action="append", dest="clips")
    prepare_parser.add_argument("--profile", action="append", dest="profile_ids")
    prepare_parser.add_argument("--force", action="store_true")
    prepare_parser.add_argument(
        "--include-disabled",
        action="store_true",
        help="Allow explicitly selected staged clips that are not live on the board.",
    )

    queue_parser = subparsers.add_parser(
        "queue",
        help="Rebuild the review queue from existing rendered variants.",
    )
    queue_parser.add_argument("--clip", action="append", dest="clips")
    queue_parser.add_argument("--profile", action="append", dest="profile_ids")
    queue_parser.add_argument(
        "--include-disabled",
        action="store_true",
        help="Allow explicitly selected staged clips that are not live on the board.",
    )

    publish_parser = subparsers.add_parser(
        "publish",
        help="Copy approved variants into media/masters.",
    )
    publish_parser.add_argument("--clip", action="append", dest="clips")
    publish_parser.add_argument(
        "--replace-static",
        action="store_true",
        help="Also replace deployed MP3s, keeping timestamped backups.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        profiles_payload, all_profiles = load_profiles(args.profiles)
        catalog_clips = load_manifest_clips(args.manifest)
        enabled_clips = [clip for clip in catalog_clips if clip["enabled"]]

        if args.command == "doctor":
            return doctor(all_profiles)

        requested_disabled = getattr(args, "include_disabled", False)
        requested_clips = getattr(args, "clips", None)
        if args.command == "queue" and not requested_clips:
            if requested_disabled:
                raise AudioLabError(
                    "--include-disabled requires at least one explicit --clip."
                )
            clips = load_catalog_selection(catalog_clips)
        else:
            include_disabled = requested_disabled or args.command in {
                "fetch",
                "publish",
            }
            if requested_disabled and not requested_clips:
                raise AudioLabError(
                    "--include-disabled requires at least one explicit --clip."
                )
            available_clips = catalog_clips if include_disabled else enabled_clips
            clips = select_by_id(
                available_clips,
                requested_clips,
                label="clip ids",
            )
        profiles = select_by_id(
            all_profiles,
            getattr(args, "profile_ids", None),
            label="profile ids",
        )

        if args.command == "fetch":
            if not args.clips:
                raise AudioLabError("fetch requires at least one explicit --clip.")
            fetched = [
                fetch_external_source(clip, force=args.force) for clip in clips
            ]
            print(f"Fetched {len(fetched)} staged sources into {DEFAULT_SOURCE_DIR}.")
            return 0

        if args.command == "prepare":
            queue = prepare(
                clips,
                profiles_payload,
                profiles,
                queue_profiles=all_profiles,
                force=args.force,
            )
            print(
                f"Prepared {len(queue['clips'])} clips for review at "
                f"{DEFAULT_REVIEW_DIR}."
            )
            return 0

        if args.command == "queue":
            queue = build_queue(clips, profiles_payload, profiles)
            validate_review_bundle(queue, clips, profiles)
            write_json(DEFAULT_QUEUE, queue)
            print(f"Queued {len(queue['clips'])} clips at {DEFAULT_QUEUE}.")
            return 0

        if args.command == "publish":
            queue = read_json(DEFAULT_QUEUE)
            reviews = read_json(DEFAULT_REVIEWS) if DEFAULT_REVIEWS.exists() else {}
            published = publish_approved(
                queue,
                reviews,
                catalog_clips,
                requested=args.clips,
                replace_static=args.replace_static,
            )
            print(f"Published {len(published)} approved masters.")
            return 0

        raise AudioLabError(f"Unsupported command: {args.command}")
    except (AudioLabError, subprocess.CalledProcessError) as exc:
        print(f"audio-lab: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
