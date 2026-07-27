#!/usr/bin/env python3
"""Validate the manifest and verify that enabled static assets exist."""

from __future__ import annotations

from pathlib import Path

from main import BASE_DIR, load_clips


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
    print(f"Validated {len(enabled)} enabled clips across {len(artists)} artists.")


if __name__ == "__main__":
    main()

