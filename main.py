from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from flask import Flask, jsonify, render_template, request

BASE_DIR = Path(__file__).resolve().parent
CONTENT_PATH = BASE_DIR / "content" / "clips.json"
REVIEW_QUEUE_PATH = BASE_DIR / "content" / "review_queue.json"
REVIEW_AUDIO_DIR = BASE_DIR / "static" / "review-audio"
ALLOWED_REVIEW_DECISIONS = {"approve", "hold", "replace-source"}

REQUIRED_CLIP_FIELDS = {
    "id",
    "artist",
    "phrase",
    "audio",
    "image",
    "enabled",
}
PUBLIC_CLIP_FIELDS = {
    "id",
    "artist",
    "phrase",
    "audio",
    "image",
    "image_position",
    "image_scale",
    "image_origin",
    "grade",
}


class ReviewStore(Protocol):
    def load(self) -> dict[str, dict[str, Any]]: ...

    def save(self, clip_id: str, review: dict[str, Any]) -> None: ...


class MemoryReviewStore:
    """Process-local fallback used by tests and local development."""

    def __init__(self) -> None:
        self.reviews: dict[str, dict[str, Any]] = {}

    def load(self) -> dict[str, dict[str, Any]]:
        return dict(self.reviews)

    def save(self, clip_id: str, review: dict[str, Any]) -> None:
        self.reviews[clip_id] = dict(review)


class CloudStorageReviewStore:
    """Persist one review per object in the App Engine app's default bucket."""

    def __init__(self, bucket_name: str, prefix: str) -> None:
        from google.cloud import storage

        self.client = storage.Client()
        self.bucket_name = bucket_name
        self.prefix = prefix.strip("/") + "/"

    def load(self) -> dict[str, dict[str, Any]]:
        reviews: dict[str, dict[str, Any]] = {}
        for blob in self.client.list_blobs(self.bucket_name, prefix=self.prefix):
            if not blob.name.endswith(".json"):
                continue
            payload = json.loads(blob.download_as_text(encoding="utf-8"))
            clip_id = blob.name.removeprefix(self.prefix).removesuffix(".json")
            if "/" not in clip_id and isinstance(payload, dict):
                reviews[clip_id] = payload
        return reviews

    def save(self, clip_id: str, review: dict[str, Any]) -> None:
        blob = self.client.bucket(self.bucket_name).blob(
            f"{self.prefix}{clip_id}.json"
        )
        blob.upload_from_string(
            json.dumps(review, ensure_ascii=False, sort_keys=True),
            content_type="application/json",
        )


def _is_safe_static_path(value: str) -> bool:
    path = Path(value)
    return (
        bool(value)
        and not path.is_absolute()
        and ".." not in path.parts
        and "\\" not in value
    )


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_clips(path: Path = CONTENT_PATH) -> list[dict[str, Any]]:
    """Load and validate the source-controlled clip manifest."""
    with path.open(encoding="utf-8") as manifest_file:
        payload = json.load(manifest_file)

    if not isinstance(payload, list):
        raise ValueError("Clip manifest must contain a JSON list.")

    clip_ids: set[str] = set()
    clips: list[dict[str, Any]] = []

    for index, clip in enumerate(payload):
        if not isinstance(clip, dict):
            raise ValueError(f"Clip #{index + 1} must be an object.")

        missing = REQUIRED_CLIP_FIELDS - clip.keys()
        if missing:
            raise ValueError(
                f"Clip #{index + 1} is missing: {', '.join(sorted(missing))}."
            )

        clip_id = clip["id"]
        if not isinstance(clip_id, str) or not clip_id:
            raise ValueError(f"Clip #{index + 1} has an invalid id.")
        if clip_id in clip_ids:
            raise ValueError(f"Duplicate clip id: {clip_id}.")
        clip_ids.add(clip_id)

        for field in ("audio", "image"):
            value = clip[field]
            if not isinstance(value, str) or not _is_safe_static_path(value):
                raise ValueError(f"{clip_id} has an unsafe {field} path.")

        if not isinstance(clip["enabled"], bool):
            raise ValueError(f"{clip_id} enabled must be true or false.")

        clips.append(clip)

    return clips


def load_review_queue(path: Path = REVIEW_QUEUE_PATH) -> dict[str, Any]:
    """Load and validate the deployable audio-review queue."""
    with path.open(encoding="utf-8") as queue_file:
        queue = json.load(queue_file)

    if not isinstance(queue, dict) or queue.get("version") != 1:
        raise ValueError("Review queue must contain version 1 data.")
    if not isinstance(queue.get("quality_fields"), list):
        raise ValueError("Review queue quality_fields must be a list.")
    if not isinstance(queue.get("clips"), list):
        raise ValueError("Review queue clips must be a list.")

    clip_ids: set[str] = set()
    for clip in queue["clips"]:
        if not isinstance(clip, dict):
            raise ValueError("Every review clip must be an object.")
        clip_id = clip.get("id")
        if not isinstance(clip_id, str) or not clip_id or clip_id in clip_ids:
            raise ValueError(f"Invalid or duplicate review clip id: {clip_id!r}.")
        clip_ids.add(clip_id)

        variants = clip.get("variants")
        if not isinstance(variants, list) or not variants:
            raise ValueError(f"{clip_id}: at least one review variant is required.")
        variant_ids: set[str] = set()
        for variant in variants:
            if not isinstance(variant, dict):
                raise ValueError(f"{clip_id}: every variant must be an object.")
            variant_id = variant.get("id")
            path_value = variant.get("path")
            sha256 = variant.get("sha256")
            if (
                not isinstance(variant_id, str)
                or not variant_id
                or variant_id in variant_ids
            ):
                raise ValueError(f"{clip_id}: invalid duplicate variant id.")
            if not isinstance(path_value, str) or not _is_safe_static_path(path_value):
                raise ValueError(f"{clip_id}: unsafe review audio path.")
            if (
                not isinstance(sha256, str)
                or len(sha256) != 64
                or any(character not in "0123456789abcdef" for character in sha256)
            ):
                raise ValueError(f"{clip_id}: invalid review audio hash.")
            audio_path = (REVIEW_AUDIO_DIR / path_value).resolve()
            if REVIEW_AUDIO_DIR.resolve() not in audio_path.parents:
                raise ValueError(f"{clip_id}: review audio leaves its asset directory.")
            if not audio_path.is_file() or audio_path.stat().st_size <= 0:
                raise ValueError(
                    f"{clip_id}: missing or empty review audio {path_value}."
                )
            if _file_sha256(audio_path) != sha256:
                raise ValueError(f"{clip_id}: review audio hash does not match.")
            variant_ids.add(variant_id)

    return queue


def review_queue_identity(queue: dict[str, Any]) -> str:
    """Return a stable id for the review content, excluding build metadata."""
    identity_payload = {
        key: value for key, value in queue.items() if key != "generated_at"
    }
    return hashlib.sha256(
        json.dumps(
            identity_payload,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()[:16]


def validate_review(
    payload: Any,
    clip: dict[str, Any],
    quality_ids: set[str],
) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("Review must be a JSON object.")

    decision = payload.get("decision")
    if decision not in ALLOWED_REVIEW_DECISIONS:
        raise ValueError("Decision must be approve, hold, or replace-source.")

    variants_by_id = {variant["id"]: variant for variant in clip["variants"]}
    selected = payload.get("selected_variant")
    if selected is not None and selected not in variants_by_id:
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

    normalized = {
        "decision": decision,
        "selected_variant": selected,
        "scores": normalized_scores,
        "notes": notes.strip(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    if selected is not None:
        normalized["selected_variant_sha256"] = variants_by_id[selected]["sha256"]
    return normalized


def compatible_reviews(
    reviews: dict[str, dict[str, Any]],
    queue: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    """Hide reviews whose selected audio changed while preserving legacy records."""
    clips_by_id = {clip["id"]: clip for clip in queue["clips"]}
    compatible: dict[str, dict[str, Any]] = {}
    for clip_id, review in reviews.items():
        if not isinstance(review, dict) or clip_id not in clips_by_id:
            continue
        selected = review.get("selected_variant")
        selected_hash = review.get("selected_variant_sha256")
        if selected is None:
            if selected_hash is None:
                compatible[clip_id] = review
            continue
        variants_by_id = {
            variant["id"]: variant for variant in clips_by_id[clip_id]["variants"]
        }
        variant = variants_by_id.get(selected)
        if variant is None:
            continue
        if selected_hash is not None and selected_hash != variant["sha256"]:
            continue
        compatible[clip_id] = review
    return compatible


def create_app(test_config: dict[str, Any] | None = None) -> Flask:
    app = Flask(__name__)
    app.config.from_mapping(
        MAX_CONTENT_LENGTH=16 * 1024,
        REVIEW_QUEUE_PATH=REVIEW_QUEUE_PATH,
        REVIEW_STORE=None,
        SEND_FILE_MAX_AGE_DEFAULT=60 * 60 * 24 * 7,
    )
    if test_config:
        app.config.update(test_config)

    clips = load_clips()
    enabled_clips = [clip for clip in clips if clip["enabled"]]
    public_clips = [
        {key: clip[key] for key in PUBLIC_CLIP_FIELDS if key in clip}
        for clip in enabled_clips
    ]
    review_queue = load_review_queue(Path(app.config["REVIEW_QUEUE_PATH"]))
    review_queue_id = review_queue_identity(review_queue)
    review_store: ReviewStore | None = app.config["REVIEW_STORE"]
    if review_store is None:
        review_bucket = os.environ.get("REVIEW_BUCKET")
        if review_bucket:
            review_prefix = os.environ.get(
                "REVIEW_PREFIX",
                "audio-review/reviews",
            ).strip("/")
            review_store = CloudStorageReviewStore(
                review_bucket,
                f"{review_prefix}/catalog-v1",
            )
        else:
            review_store = MemoryReviewStore()

    @app.get("/")
    def index() -> str:
        artist_count = len({clip["artist"] for clip in public_clips})
        return render_template(
            "index.html",
            clips=public_clips,
            clip_count=len(public_clips),
            artist_count=artist_count,
        )

    @app.get("/healthz")
    def healthz():
        return jsonify(
            status="ok",
            clips=len(public_clips),
            artists=len({clip["artist"] for clip in public_clips}),
        )

    @app.get("/audio-review")
    @app.get("/admin", strict_slashes=False)
    def audio_review() -> str:
        return render_template("audio_review.html")

    @app.get("/audio-review/api/queue")
    def get_audio_review_queue():
        try:
            reviews = compatible_reviews(review_store.load(), review_queue)
        except Exception:
            app.logger.exception("Could not load audio reviews.")
            return jsonify(error="Review storage is temporarily unavailable."), 503
        return jsonify(
            queue=review_queue,
            queue_id=review_queue_id,
            reviews=reviews,
        )

    @app.put("/audio-review/api/reviews/<clip_id>")
    def put_audio_review(clip_id: str):
        clips_by_id = {clip["id"]: clip for clip in review_queue["clips"]}
        clip = clips_by_id.get(clip_id)
        if clip is None:
            return jsonify(error="Unknown review clip."), 404
        quality_ids = {
            field["id"]
            for field in review_queue["quality_fields"]
            if isinstance(field, dict) and isinstance(field.get("id"), str)
        }
        try:
            normalized = validate_review(
                request.get_json(silent=True),
                clip,
                quality_ids,
            )
        except ValueError as exc:
            return jsonify(error=str(exc)), 400

        reviewer = request.headers.get("X-Appengine-User-Email")
        if reviewer:
            normalized["reviewer"] = reviewer
        try:
            review_store.save(clip_id, normalized)
        except Exception:
            app.logger.exception("Could not save audio review for %s.", clip_id)
            return jsonify(error="Review storage is temporarily unavailable."), 503
        return jsonify(clip_id=clip_id, review=normalized)

    @app.after_request
    def add_security_headers(response):
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; "
            "base-uri 'self'; "
            "connect-src 'self'; "
            "font-src 'self'; "
            "form-action 'self'; "
            "frame-ancestors 'none'; "
            "img-src 'self' data:; "
            "media-src 'self'; "
            "object-src 'none'; "
            "script-src 'self'; "
            "style-src 'self' 'unsafe-inline'",
        )
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault(
            "Permissions-Policy",
            "camera=(), geolocation=(), microphone=(), payment=()",
        )
        if (
            request.path == "/"
            or request.path.startswith("/admin")
            or request.path.startswith("/audio-review")
        ):
            response.headers["Cache-Control"] = "private, no-store"
        return response

    return app


app = create_app()

# Python 3 second-generation runtimes need the bundled-service WSGI wrapper
# when app.yaml enables App Engine's legacy Users service for `login: admin`.
try:
    from google.appengine.api import wrap_wsgi_app
except ImportError:
    pass
else:
    app.wsgi_app = wrap_wsgi_app(app.wsgi_app)


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8080, debug=True)
