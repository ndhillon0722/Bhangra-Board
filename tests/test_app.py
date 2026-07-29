import json
from pathlib import Path

import pytest

import main
from main import (
    BASE_DIR,
    CONTENT_PATH,
    REVIEW_AUDIO_DIR,
    REVIEW_QUEUE_PATH,
    MemoryReviewStore,
    create_app,
    load_clips,
    load_review_queue,
    review_queue_identity,
)


@pytest.fixture()
def client():
    app = create_app({"TESTING": True})
    return app.test_client()


def test_index_renders_enabled_board(client):
    response = client.get("/")
    page = response.get_data(as_text=True)
    enabled = [clip for clip in load_clips(CONTENT_PATH) if clip["enabled"]]

    assert response.status_code == 200
    assert "<title>Bhangra Board" in page
    assert 'class="sound-tile"' in page
    assert f"{len(enabled)} sounds" in page
    assert (
        f"<strong>{len({clip['artist'] for clip in enabled})}</strong> artists"
        in page
    )
    assert 'href="mailto:yarr@bhangraboard.xyz"' in page
    assert "hello@bhangraboard.com" not in page
    assert 'href="/audio-review"' not in page
    assert 'href="/admin"' not in page
    assert "Dedicated to my beautiful wife, Nehu" in page
    assert "I just took the credit. ❤️" in page
    assert "Unconfirmed artist" not in page


def test_index_uses_consistent_generated_portrait_treatment(client):
    page = client.get("/").get_data(as_text=True)

    assert "?v=3" in page
    assert "--image-position: 50% 50%;" in page
    assert "--image-scale: 1;" in page
    assert "Every artist portrait" in page
    assert "About the artwork" in page
    assert "do not imply an artist’s endorsement" in page
    assert "admin-only development board" not in page
    assert "required before public launch" not in page
    assert "commons.wikimedia.org" not in page


def test_admin_alias_serves_audio_lab_without_public_navigation(client):
    for path in ("/admin", "/admin/", "/audio-review"):
        response = client.get(path)
        assert response.status_code == 200
        assert "Audio Lab" in response.get_data(as_text=True)
        assert response.headers["Cache-Control"] == "private, no-store"


def test_healthz_reports_content_totals(client):
    response = client.get("/healthz")
    enabled = [clip for clip in load_clips(CONTENT_PATH) if clip["enabled"]]

    assert response.status_code == 200
    assert response.get_json() == {
        "status": "ok",
        "clips": len(enabled),
        "artists": len({clip["artist"] for clip in enabled}),
    }


def test_security_headers_are_applied(client):
    response = client.get("/")

    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert "media-src 'self'" in response.headers["Content-Security-Policy"]


def test_manifest_has_unique_ids_and_safe_paths():
    clips = load_clips(CONTENT_PATH)

    assert len({clip["id"] for clip in clips}) == len(clips)
    for clip in clips:
        assert ".." not in Path(clip["audio"]).parts
        assert ".." not in Path(clip["image"]).parts


def test_all_enabled_assets_exist():
    for clip in load_clips(CONTENT_PATH):
        if not clip["enabled"]:
            continue
        assert (BASE_DIR / "static" / clip["audio"]).is_file()
        assert (BASE_DIR / "static" / clip["image"]).is_file()


def test_remote_audio_review_queue_and_assets_are_deployable():
    queue = load_review_queue(REVIEW_QUEUE_PATH)
    manifest = load_clips(CONTENT_PATH)
    enabled_clip_ids = {clip["id"] for clip in manifest if clip["enabled"]}
    selection = json.loads(
        (BASE_DIR / "content" / "audio_lab_selection.json").read_text(
            encoding="utf-8"
        )
    )
    expected_clip_ids = enabled_clip_ids | set(selection["staged_clip_ids"])

    assert len(queue["clips"]) == len(expected_clip_ids)
    assert {clip["id"] for clip in queue["clips"]} == expected_clip_ids
    for clip in queue["clips"]:
        variant_ids = {variant["id"] for variant in clip["variants"]}
        if clip["id"] in enabled_clip_ids:
            assert len(clip["variants"]) == 10
            assert "deployed" in variant_ids
        else:
            assert len(clip["variants"]) == 9
            assert "deployed" not in variant_ids
        for variant in clip["variants"]:
            asset = REVIEW_AUDIO_DIR / variant["path"]
            assert asset.is_file()
            assert asset.stat().st_size > 0
            assert len(variant["sha256"]) == 64


def test_remote_audio_review_persists_valid_decisions():
    store = MemoryReviewStore()
    app = create_app({"TESTING": True, "REVIEW_STORE": store})
    client = app.test_client()

    page = client.get("/audio-review")
    queue_response = client.get("/audio-review/api/queue")
    queue = queue_response.get_json()["queue"]
    clip = queue["clips"][0]
    variant = clip["variants"][1]
    response = client.put(
        f"/audio-review/api/reviews/{clip['id']}",
        json={
            "decision": "approve",
            "selected_variant": variant["id"],
            "scores": {
                "vocal_clarity": 5,
                "music_suppression": 4,
                "artifact_control": 4,
                "recognizability": 5,
            },
            "notes": "Remote review winner.",
        },
        headers={"X-Appengine-User-Email": "reviewer@example.com"},
    )
    refreshed = client.get("/audio-review/api/queue").get_json()

    assert page.status_code == 200
    assert "Audio Lab" in page.get_data(as_text=True)
    assert page.headers["Cache-Control"] == "private, no-store"
    assert queue_response.status_code == 200
    assert response.status_code == 200
    assert refreshed["reviews"][clip["id"]]["selected_variant"] == variant["id"]
    assert (
        refreshed["reviews"][clip["id"]]["selected_variant_sha256"]
        == variant["sha256"]
    )
    assert refreshed["reviews"][clip["id"]]["reviewer"] == "reviewer@example.com"


def test_remote_audio_review_rejects_unknown_variants():
    app = create_app({"TESTING": True, "REVIEW_STORE": MemoryReviewStore()})
    client = app.test_client()
    clip_id = load_review_queue()["clips"][0]["id"]

    response = client.put(
        f"/audio-review/api/reviews/{clip_id}",
        json={
            "decision": "approve",
            "selected_variant": "not-a-real-variant",
            "scores": {},
            "notes": "",
        },
    )

    assert response.status_code == 400
    assert "not available" in response.get_json()["error"]


def test_review_queue_identity_ignores_generated_timestamp():
    queue = load_review_queue()
    rebuilt = dict(queue)
    rebuilt["generated_at"] = "2099-01-01T00:00:00+00:00"

    assert review_queue_identity(rebuilt) == review_queue_identity(queue)


def test_cloud_review_store_uses_stable_catalog_namespace(monkeypatch):
    captured = {}

    class FakeCloudStore(MemoryReviewStore):
        def __init__(self, bucket_name, prefix):
            super().__init__()
            captured["bucket"] = bucket_name
            captured["prefix"] = prefix

    monkeypatch.setattr(main, "CloudStorageReviewStore", FakeCloudStore)
    monkeypatch.setenv("REVIEW_BUCKET", "test-bucket")
    monkeypatch.setenv("REVIEW_PREFIX", "audio-review/reviews")

    create_app({"TESTING": True})

    assert captured == {
        "bucket": "test-bucket",
        "prefix": "audio-review/reviews/catalog-v1",
    }


def test_remote_audio_review_keeps_legacy_and_hides_stale_hashed_reviews():
    queue = load_review_queue()
    clip = queue["clips"][0]
    variant = clip["variants"][0]
    legacy_store = MemoryReviewStore()
    legacy_store.reviews[clip["id"]] = {
        "decision": "approve",
        "selected_variant": variant["id"],
        "scores": {},
        "notes": "Pre-hash review",
    }
    legacy_client = create_app(
        {"TESTING": True, "REVIEW_STORE": legacy_store}
    ).test_client()

    assert clip["id"] in legacy_client.get(
        "/audio-review/api/queue"
    ).get_json()["reviews"]

    stale_store = MemoryReviewStore()
    stale_store.reviews[clip["id"]] = {
        "decision": "approve",
        "selected_variant": variant["id"],
        "selected_variant_sha256": "f" * 64,
        "scores": {},
        "notes": "Stale audio",
    }
    stale_client = create_app(
        {"TESTING": True, "REVIEW_STORE": stale_store}
    ).test_client()

    assert clip["id"] not in stale_client.get(
        "/audio-review/api/queue"
    ).get_json()["reviews"]


def test_review_queue_rejects_empty_audio(tmp_path, monkeypatch):
    review_root = tmp_path / "review-audio"
    empty_audio = review_root / "test-clip" / "reference.mp3"
    empty_audio.parent.mkdir(parents=True)
    empty_audio.write_bytes(b"")
    queue_path = tmp_path / "queue.json"
    queue_path.write_text(
        json.dumps(
            {
                "version": 1,
                "generated_at": "2026-07-27T00:00:00+00:00",
                "quality_fields": [],
                "clips": [
                    {
                        "id": "test-clip",
                        "variants": [
                            {
                                "id": "reference",
                                "path": "test-clip/reference.mp3",
                                "sha256": "0" * 64,
                            }
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(main, "REVIEW_AUDIO_DIR", review_root)

    with pytest.raises(ValueError, match="missing or empty review audio"):
        load_review_queue(queue_path)


def test_review_queue_rejects_audio_hash_mismatch(tmp_path, monkeypatch):
    review_root = tmp_path / "review-audio"
    audio = review_root / "test-clip" / "reference.mp3"
    audio.parent.mkdir(parents=True)
    audio.write_bytes(b"audio")
    queue_path = tmp_path / "queue.json"
    queue_path.write_text(
        json.dumps(
            {
                "version": 1,
                "quality_fields": [],
                "clips": [
                    {
                        "id": "test-clip",
                        "variants": [
                            {
                                "id": "reference",
                                "path": "test-clip/reference.mp3",
                                "sha256": "0" * 64,
                            }
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(main, "REVIEW_AUDIO_DIR", review_root)

    with pytest.raises(ValueError, match="hash does not match"):
        load_review_queue(queue_path)


def app_engine_handler(descriptor, url):
    marker = f"  - url: {url}\n"
    handler_start = descriptor.index(marker)
    next_handler = descriptor.find("\n  - url:", handler_start + len(marker))
    return descriptor[
        handler_start : next_handler if next_handler != -1 else len(descriptor)
    ]


def test_app_yaml_explicitly_admin_gates_every_review_surface():
    app_yaml = (BASE_DIR / "app.yaml").read_text(encoding="utf-8")

    for url in (
        "/static/review-audio",
        "/audio-review/api/.*",
        "/audio-review.*",
        "/admin.*",
        "/static",
        "/.*",
    ):
        handler = app_engine_handler(app_yaml, url)
        assert "login: admin" in handler
        assert "secure: always" in handler


def test_prod_yaml_exposes_only_the_soundboard():
    prod_yaml = (BASE_DIR / "app.prod.yaml").read_text(encoding="utf-8")

    assert 'REVIEW_BUCKET: "bhangraboard-prod.appspot.com"' in prod_yaml
    assert prod_yaml.index("  - url: /static/review-audio\n") < prod_yaml.index(
        "  - url: /static\n"
    )

    for url in (
        "/static/review-audio",
        "/audio-review/api/.*",
        "/audio-review.*",
        "/admin.*",
    ):
        handler = app_engine_handler(prod_yaml, url)
        assert "login: admin" in handler
        assert "secure: always" in handler

    assert "auth_fail_action: unauthorized" in app_engine_handler(
        prod_yaml, "/audio-review/api/.*"
    )
    assert "auth_fail_action: redirect" in app_engine_handler(
        prod_yaml, "/audio-review.*"
    )
    assert "auth_fail_action: redirect" in app_engine_handler(
        prod_yaml, "/admin.*"
    )
    assert "auth_fail_action: redirect" in app_engine_handler(
        prod_yaml, "/static/review-audio"
    )

    for url in ("/static", "/.*"):
        handler = app_engine_handler(prod_yaml, url)
        assert "login: admin" not in handler
        assert "secure: always" in handler
