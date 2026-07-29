#!/usr/bin/env python3
"""Validate the manifest and verify that enabled static assets exist."""

from __future__ import annotations

from pathlib import Path

from main import BASE_DIR, load_clips
from scripts.audio_lab import load_profiles, read_json


def main() -> None:
    clips = load_clips()
    missing: list[str] = []

    for clip in clips:
        if not clip["enabled"]:
            continue
        for field in ("audio", "image"):
            asset = BASE_DIR / "static" / clip[field]
            if not asset.is_file():
                missing.append(f"{clip['id']}: {field} -> {asset}")

    if missing:
        details = "\n".join(f"- {entry}" for entry in missing)
        raise SystemExit(f"Missing enabled assets:\n{details}")

    enabled = [clip for clip in clips if clip["enabled"]]
    artists = {clip["artist"] for clip in enabled}
    _, profiles = load_profiles()
    backlog = read_json(BASE_DIR / "content" / "artist_backlog.json")
    candidates = backlog.get("artists", []) if isinstance(backlog, dict) else []
    candidate_ids = [
        candidate.get("id") for candidate in candidates if isinstance(candidate, dict)
    ]
    if not candidates or len(candidate_ids) != len(set(candidate_ids)):
        raise SystemExit("Artist backlog must contain unique candidate ids.")
    required_candidate_fields = {
        "id",
        "artist",
        "lane",
        "priority",
        "candidate_tracks",
        "source_status",
        "portrait_status",
    }
    for candidate in candidates:
        missing_fields = required_candidate_fields - set(candidate)
        if missing_fields:
            raise SystemExit(
                f"{candidate.get('id', '<unknown>')}: missing backlog fields "
                f"{', '.join(sorted(missing_fields))}"
            )
        if len(candidate["candidate_tracks"]) < 3:
            raise SystemExit(
                f"{candidate['id']}: at least three candidate tracks are required."
            )

    target_sounds = len(candidates) * backlog["target_sounds_per_artist"]
    print(
        f"Validated {len(enabled)} enabled clips across {len(artists)} artists; "
        f"{len(profiles)} audio profiles; "
        f"{len(candidates)} candidate artists targeting {target_sounds} sounds."
    )


if __name__ == "__main__":
    main()
