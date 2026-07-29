#!/usr/bin/env python3
"""Run the local-only Bhangra Board audio comparison and approval desk."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from flask import Flask, abort, jsonify, render_template, request, send_from_directory

from scripts.audio_lab import (
    DEFAULT_QUEUE,
    DEFAULT_REVIEW_DIR,
    DEFAULT_REVIEWS,
    read_json,
    write_json,
)


SCRIPT_DIR = Path(__file__).resolve().parent
ALLOWED_DECISIONS = {"approve", "hold", "replace-source"}


def validate_review(
    payload: Any,
    clip: dict[str, Any],
    quality_ids: set[str],
) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("Review must be a JSON object.")

    decision = payload.get("decision")
    if decision not in ALLOWED_DECISIONS:
        raise ValueError("Decision must be approve, hold, or replace-source.")

    variant_ids = {variant["id"] for variant in clip["variants"]}
    selected = payload.get("selected_variant")
    if selected is not None and selected not in variant_ids:
        raise ValueError("Selected variant is not available for this clip.")
    if decision == "approve" and selected is None:
        raise ValueError("An approved clip requires a selected variant.")

    scores = payload.get("scores", {})
    if not isinstance(scores, dict) or set(scores) - quality_ids:
        raise ValueError("Scores contain an unknown quality field.")
    normalized_scores: dict[str, int] = {}
    for field_id, score in scores.items():
        if isinstance(score, bool) or not isinstance(score, int) or not 1 <= score <= 5:
            raise ValueError(f"{field_id} score must be an integer from 1 to 5.")
        normalized_scores[field_id] = score

    notes = payload.get("notes", "")
    if not isinstance(notes, str) or len(notes) > 2000:
        raise ValueError("Notes must be a string of at most 2000 characters.")

    return {
        "decision": decision,
        "selected_variant": selected,
        "scores": normalized_scores,
        "notes": notes.strip(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }


def create_review_app(
    *,
    review_root: Path = DEFAULT_REVIEW_DIR,
    queue_path: Path = DEFAULT_QUEUE,
    reviews_path: Path = DEFAULT_REVIEWS,
) -> Flask:
    app = Flask(
        __name__,
        template_folder=str(SCRIPT_DIR / "templates"),
        static_folder=str(SCRIPT_DIR / "static"),
        static_url_path="/review-static",
    )
    app.config.update(
        REVIEW_ROOT=review_root.resolve(),
        QUEUE_PATH=queue_path.resolve(),
        REVIEWS_PATH=reviews_path.resolve(),
        MAX_CONTENT_LENGTH=16 * 1024,
    )

    def queue_data() -> dict[str, Any]:
        queue = read_json(app.config["QUEUE_PATH"])
        if not isinstance(queue, dict) or queue.get("version") != 1:
            raise RuntimeError("The review queue must use version 1.")
        return queue

    @app.after_request
    def secure_local_response(response):
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "style-src 'self'; "
            "script-src 'self'; "
            "media-src 'self'; "
            "img-src 'self' data:; "
            "connect-src 'self'"
        )
        return response

    @app.get("/")
    def index():
        return render_template("review.html")

    @app.get("/api/queue")
    def get_queue():
        reviews_file = app.config["REVIEWS_PATH"]
        reviews = read_json(reviews_file) if reviews_file.is_file() else {}
        return jsonify({"queue": queue_data(), "reviews": reviews})

    @app.put("/api/reviews/<clip_id>")
    def put_review(clip_id: str):
        queue = queue_data()
        clips = {clip["id"]: clip for clip in queue.get("clips", [])}
        clip = clips.get(clip_id)
        if clip is None:
            abort(404)
        quality_ids = {
            field["id"] for field in queue.get("quality_fields", []) if "id" in field
        }
        try:
            normalized = validate_review(request.get_json(silent=True), clip, quality_ids)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400

        reviews_path = app.config["REVIEWS_PATH"]
        reviews = read_json(reviews_path) if reviews_path.is_file() else {}
        if not isinstance(reviews, dict):
            reviews = {}
        reviews[clip_id] = normalized
        write_json(reviews_path, reviews)
        return jsonify({"clip_id": clip_id, "review": normalized})

    @app.get("/media/<path:filename>")
    def media(filename: str):
        return send_from_directory(app.config["REVIEW_ROOT"], filename)

    return app


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9090)
    args = parser.parse_args()
    if args.host not in {"127.0.0.1", "localhost", "::1"}:
        raise SystemExit("The review desk is intentionally limited to localhost.")
    app = create_review_app()
    app.run(host=args.host, port=args.port, debug=False)


if __name__ == "__main__":
    main()
