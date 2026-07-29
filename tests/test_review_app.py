import json

from scripts.review_app import create_review_app


def review_queue():
    return {
        "version": 1,
        "generated_at": "2026-07-27T00:00:00+00:00",
        "quality_fields": [
            {
                "id": "vocal_clarity",
                "label": "Vocal clarity",
                "help": "Is it clear?",
            }
        ],
        "clips": [
            {
                "id": "test-clip",
                "artist": "Test Artist",
                "phrase": "Balle",
                "current_grade": "C",
                "deployed_audio": "audio/test.mp3",
                "variants": [
                    {
                        "id": "reference",
                        "label": "Current reference",
                        "profile": "reference",
                        "mix": "reference",
                        "path": "test-clip/reference.mp3",
                    },
                    {
                        "id": "model-vocal",
                        "label": "Model vocal",
                        "profile": "model",
                        "mix": "vocal",
                        "path": "test-clip/model-vocal.mp3",
                    },
                ],
            }
        ],
    }


def test_review_api_persists_a_valid_decision(tmp_path):
    review_root = tmp_path / "review"
    review_root.mkdir()
    queue_path = review_root / "queue.json"
    reviews_path = review_root / "reviews.json"
    queue_path.write_text(json.dumps(review_queue()), encoding="utf-8")
    app = create_review_app(
        review_root=review_root,
        queue_path=queue_path,
        reviews_path=reviews_path,
    )
    client = app.test_client()

    response = client.put(
        "/api/reviews/test-clip",
        json={
            "decision": "approve",
            "selected_variant": "model-vocal",
            "scores": {"vocal_clarity": 5},
            "notes": "Clean and recognizable.",
        },
    )

    assert response.status_code == 200
    stored = json.loads(reviews_path.read_text(encoding="utf-8"))
    assert stored["test-clip"]["selected_variant"] == "model-vocal"
    assert stored["test-clip"]["scores"]["vocal_clarity"] == 5


def test_review_api_rejects_invalid_approval_and_sets_security_headers(tmp_path):
    review_root = tmp_path / "review"
    review_root.mkdir()
    queue_path = review_root / "queue.json"
    queue_path.write_text(json.dumps(review_queue()), encoding="utf-8")
    app = create_review_app(
        review_root=review_root,
        queue_path=queue_path,
        reviews_path=review_root / "reviews.json",
    )
    client = app.test_client()

    invalid = client.put(
        "/api/reviews/test-clip",
        json={
            "decision": "approve",
            "selected_variant": None,
            "scores": {"vocal_clarity": 9},
            "notes": "",
        },
    )
    page = client.get("/")

    assert invalid.status_code == 400
    assert page.status_code == 200
    assert "Audio Lab" in page.get_data(as_text=True)
    assert page.headers["X-Frame-Options"] == "DENY"
    assert "media-src 'self'" in page.headers["Content-Security-Policy"]
