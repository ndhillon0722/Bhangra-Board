from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from flask import Flask, jsonify, render_template, request

BASE_DIR = Path(__file__).resolve().parent
CONTENT_PATH = BASE_DIR / "content" / "clips.json"

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


def _is_safe_static_path(value: str) -> bool:
    path = Path(value)
    return (
        bool(value)
        and not path.is_absolute()
        and ".." not in path.parts
        and "\\" not in value
    )


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


def create_app(test_config: dict[str, Any] | None = None) -> Flask:
    app = Flask(__name__)
    app.config.from_mapping(
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
        if request.path == "/":
            response.headers.setdefault("Cache-Control", "no-cache")
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
