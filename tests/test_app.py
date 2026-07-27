from pathlib import Path

import pytest

from main import BASE_DIR, CONTENT_PATH, create_app, load_clips


@pytest.fixture()
def client():
    app = create_app({"TESTING": True})
    return app.test_client()


def test_index_renders_enabled_board(client):
    response = client.get("/")
    page = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "<title>Bhangra Board" in page
    assert 'class="sound-tile"' in page
    assert "18 sounds" in page
    assert "<strong>5</strong> artists" in page
    assert "Unconfirmed artist" not in page


def test_healthz_reports_content_totals(client):
    response = client.get("/healthz")

    assert response.status_code == 200
    assert response.get_json() == {
        "status": "ok",
        "clips": 18,
        "artists": 5,
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
