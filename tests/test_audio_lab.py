import json
import math
import os
from pathlib import Path
import subprocess

import pytest

from scripts import audio_lab
from scripts.audio_lab import (
    AudioLabError,
    DEFAULT_SELECTION,
    build_queue,
    fetch_external_source,
    has_cached_output,
    load_catalog_selection,
    load_enabled_clips,
    load_manifest_clips,
    load_profiles,
    publish_approved,
    read_json,
    resolve_source,
    select_by_id,
    source_download_window,
    validate_review_bundle,
    windowed_filter,
)


def test_audio_profiles_and_artist_backlog_are_structured():
    payload, profiles = load_profiles()
    backlog = json.loads(
        Path("content/artist_backlog.json").read_text(encoding="utf-8")
    )

    assert len(profiles) == 4
    assert {profile.id for profile in profiles} == {
        "bs-roformer",
        "melband-roformer",
        "mdx-vocal-ft",
        "htdemucs-ft",
    }
    assert len(payload["quality_fields"]) == 4
    assert len(backlog["artists"]) == 30
    assert len({artist["id"] for artist in backlog["artists"]}) == 30
    assert {artist["lane"] for artist in backlog["artists"]} == {
        "legends",
        "modern",
        "women",
        "rap-diaspora",
    }
    assert sum(
        backlog["target_sounds_per_artist"] for _ in backlog["artists"]
    ) == 90


def test_resolve_source_prefers_raw_media_then_falls_back_to_static(tmp_path):
    clip = {
        "id": "test-clip",
        "artist": "Test Artist",
        "phrase": "Test",
        "audio": "audio/test.mp3",
        "enabled": True,
    }
    source_dir = tmp_path / "source"
    static_dir = tmp_path / "static"
    deployed = static_dir / clip["audio"]
    deployed.parent.mkdir(parents=True)
    deployed.write_bytes(b"deployed")

    assert resolve_source(clip, source_dir, static_dir) == deployed

    raw = source_dir / "test-clip.wav"
    raw.parent.mkdir(parents=True)
    raw.write_bytes(b"raw")
    assert resolve_source(clip, source_dir, static_dir) == raw

    (source_dir / "test-clip.flac").write_bytes(b"also raw")
    with pytest.raises(AudioLabError, match="multiple raw sources"):
        resolve_source(clip, source_dir, static_dir)


def test_select_by_id_preserves_requested_order_and_rejects_unknown():
    clips = load_enabled_clips()
    selected = select_by_id(
        clips,
        ["jazzy-rambo", "daler-tunak-tunak-01"],
        label="clip ids",
    )
    assert [clip["id"] for clip in selected] == [
        "jazzy-rambo",
        "daler-tunak-tunak-01",
    ]
    with pytest.raises(AudioLabError, match="Unknown clip ids"):
        select_by_id(clips, ["not-real"], label="clip ids")


def test_manifest_loader_can_include_explicitly_staged_clips(tmp_path):
    manifest = tmp_path / "clips.json"
    manifest.write_text(
        json.dumps(
            [
                {
                    "id": "live-clip",
                    "artist": "Live Artist",
                    "phrase": "Live",
                    "audio": "audio/live.mp3",
                    "enabled": True,
                },
                {
                    "id": "staged-clip",
                    "artist": "Staged Artist",
                    "phrase": "Staged",
                    "audio": "audio/staged.mp3",
                    "enabled": False,
                },
            ]
        ),
        encoding="utf-8",
    )

    assert [clip["id"] for clip in load_enabled_clips(manifest)] == ["live-clip"]
    assert [clip["id"] for clip in load_manifest_clips(manifest)] == [
        "live-clip",
        "staged-clip",
    ]


def test_catalog_selection_contains_every_live_clip_and_explicit_staging():
    selected = load_catalog_selection(load_manifest_clips())
    selection = read_json(DEFAULT_SELECTION)
    enabled = [clip for clip in load_manifest_clips() if clip["enabled"]]

    assert len(selected) == len(enabled) + len(selection["staged_clip_ids"])
    assert sum(clip["enabled"] for clip in selected) == len(enabled)
    assert len({clip["id"] for clip in selected}) == len(selected)


def test_guru_randhawa_candidates_are_staged_for_review_only():
    clips = {clip["id"]: clip for clip in load_manifest_clips()}
    staged = set(read_json(DEFAULT_SELECTION)["staged_clip_ids"])
    guru_ids = {
        "guru-randhawa-lahore",
        "guru-randhawa-high-rated-gabru",
        "guru-randhawa-suit-suit",
        "guru-randhawa-ban-ja-rani",
        "guru-randhawa-ishare-tere",
        "guru-randhawa-slowly-slowly",
        "guru-randhawa-made-in-india",
        "guru-randhawa-koi-na",
        "guru-randhawa-patola",
        "guru-randhawa-downtown",
    }

    assert guru_ids <= staged
    for clip_id in guru_ids:
        clip = clips[clip_id]
        assert clip["enabled"] is False
        assert clip["grade"] == "candidate"
        assert clip["image"] == "images/artists/guru-randhawa.jpg"
        assert clip["source_channel"] == "T-Series"
        assert clip["clearance_status"] == "needs-confirmation"
        assert "audio_review_status" not in clip


def test_completed_audio_reviews_are_reflected_on_the_board():
    reviewed = [
        clip
        for clip in load_manifest_clips()
        if clip.get("audio_review_status")
    ]

    assert len(reviewed) == 124
    assert sum(
        clip["audio_review_status"] == "approved" for clip in reviewed
    ) == 105
    assert sum(clip["audio_review_status"] == "hold" for clip in reviewed) == 7
    assert sum(
        clip["audio_review_status"] == "replace-source" for clip in reviewed
    ) == 12
    assert all(
        clip["enabled"] == (clip["audio_review_status"] == "approved")
        for clip in reviewed
    )


def test_latest_audio_lab_removals_are_off_board_and_out_of_staging():
    clips = {clip["id"]: clip for clip in load_manifest_clips()}
    selection = set(read_json(DEFAULT_SELECTION)["staged_clip_ids"])

    for clip_id in ("jazzy-dil-luteya-01", "jazzy-dil-luteya-02"):
        assert clips[clip_id]["enabled"] is False
        assert clips[clip_id]["audio_review_status"] == "replace-source"
        assert clip_id not in selection


def test_ap_dhillon_reviews_are_reflected_on_board_and_in_audio_lab():
    clips = {clip["id"]: clip for clip in load_manifest_clips()}
    selection = set(read_json(DEFAULT_SELECTION)["staged_clip_ids"])
    approved = {
        "ap-dhillon-excuses-kehndi-hundi-si",
        "ap-dhillon-insane-pagal-ne",
        "ap-dhillon-with-you-adavaan",
        "ap-dhillon-sleepless-raatan",
        "ap-dhillon-true-stories-dior",
        "ap-dhillon-true-stories-ap-tag",
    }
    held = {"ap-dhillon-summer-high-khabran"}

    assert approved.isdisjoint(selection)
    assert held <= selection
    for clip_id in approved:
        assert clips[clip_id]["enabled"] is True
        assert clips[clip_id]["audio_review_status"] == "approved"
        assert clips[clip_id]["image"] == "images/artists/ap-dhillon.jpg"
    for clip_id in held:
        assert clips[clip_id]["enabled"] is False
        assert clips[clip_id]["audio_review_status"] == "hold"
        assert clips[clip_id]["image"] == "images/artists/ap-dhillon.jpg"


def test_zeus_bhamra_and_shinda_reviews_are_reflected():
    clips = {clip["id"]: clip for clip in load_manifest_clips()}
    selection = set(read_json(DEFAULT_SELECTION)["staged_clip_ids"])
    images = {
        "dr-zeus-kangna-ishaare": "images/artists/dr-zeus.jpg",
        "dr-zeus-ah-ni-kuriye": "images/artists/dr-zeus.jpg",
        "dr-zeus-jugni-ji-allah-waliyan": "images/artists/dr-zeus.jpg",
        "dr-zeus-gwandian-dhol": "images/artists/dr-zeus.jpg",
        "dr-zeus-woofer-gaddi-ch": "images/artists/dr-zeus.jpg",
        "dr-zeus-woofer-zeus-tag": "images/artists/dr-zeus.jpg",
        "kuljit-bhamra-rail-gaddi-aayi": "images/artists/kuljit-bhamra.jpg",
        "kuljit-bhamra-giddha-pao-haan-deo": "images/artists/kuljit-bhamra.jpg",
        "kuljit-bhamra-pyar-ka-hai-bairi": "images/artists/kuljit-bhamra.jpg",
        "kuljit-bhamra-nachdi-di-gut": "images/artists/kuljit-bhamra.jpg",
        "kuljit-bhamra-shortest-tabla-solo": "images/artists/kuljit-bhamra.jpg",
        "kuljit-bhamra-tabla-dhol-finale": "images/artists/kuljit-bhamra.jpg",
        "sukshinder-shinda-balle-balle": "images/artists/sukshinder-shinda.jpg",
        "sukshinder-shinda-gal-sunja": "images/artists/sukshinder-shinda.jpg",
        "sukshinder-shinda-oh-na-kuri-labdi": "images/artists/sukshinder-shinda.jpg",
        "sukshinder-shinda-panjabi-clap": "images/artists/sukshinder-shinda.jpg",
        "sukshinder-shinda-ghum-suhm": "images/artists/sukshinder-shinda.jpg",
        "sukshinder-shinda-akhian-paala": "images/artists/sukshinder-shinda.jpg",
    }
    approved = {
        "dr-zeus-kangna-ishaare",
        "dr-zeus-ah-ni-kuriye",
        "dr-zeus-jugni-ji-allah-waliyan",
        "dr-zeus-gwandian-dhol",
        "dr-zeus-woofer-gaddi-ch",
        "kuljit-bhamra-rail-gaddi-aayi",
        "kuljit-bhamra-nachdi-di-gut",
        "sukshinder-shinda-balle-balle",
        "sukshinder-shinda-gal-sunja",
        "sukshinder-shinda-oh-na-kuri-labdi",
        "sukshinder-shinda-panjabi-clap",
        "sukshinder-shinda-ghum-suhm",
        "sukshinder-shinda-akhian-paala",
    }
    held = {
        "dr-zeus-woofer-zeus-tag",
        "kuljit-bhamra-giddha-pao-haan-deo",
        "kuljit-bhamra-pyar-ka-hai-bairi",
    }
    replace_source = {
        "kuljit-bhamra-shortest-tabla-solo",
        "kuljit-bhamra-tabla-dhol-finale",
    }

    assert approved.isdisjoint(selection)
    assert held | replace_source <= selection
    for clip_id, image in images.items():
        assert clips[clip_id]["image"] == image
        assert (Path("static") / image).is_file()
    for clip_id in approved:
        assert clips[clip_id]["enabled"] is True
        assert clips[clip_id]["audio_review_status"] == "approved"
    for clip_id in held:
        assert clips[clip_id]["enabled"] is False
        assert clips[clip_id]["audio_review_status"] == "hold"
    for clip_id in replace_source:
        assert clips[clip_id]["enabled"] is False
        assert clips[clip_id]["audio_review_status"] == "replace-source"


def test_previously_pending_approvals_are_published():
    clips = {clip["id"]: clip for clip in load_manifest_clips()}
    selection = set(read_json(DEFAULT_SELECTION)["staged_clip_ids"])
    approved = {
        "sidhu-moose-wala-si-si-si",
        "sidhu-moose-wala-legend-laugh",
        "sidhu-moose-wala-bambiha-haye",
        "sidhu-moose-wala-295",
        "sidhu-moose-wala-so-high",
        "sidhu-moose-wala-ajj-kal",
        "lehmber-hussainpuri-sadi-gali",
        "lehmber-hussainpuri-gerra-de-de",
        "tigerstyle-blitzkrieg-tigerstyle-tag",
        "tigerstyle-blitzkrieg-brrrra",
        "tigerstyle-kaka-nachna-onda-nei",
        "panjabi-mc-mundian-to-bach-ke",
        "panjabi-mc-jogi",
        "panjabi-mc-bari-barsi",
        "diljit-goat-diamonds",
        "diljit-born-to-shine",
        "diljit-do-you-know",
        "diljit-case-chalda",
        "diljit-lover",
        "diljit-vibe-mildi",
        "diljit-kinni-kinni",
        "diljit-khutti-chizz",
    }
    held = {
        "tigerstyle-ms-rajni-ik-banere",
        "panjabi-mc-picha-ni-chad-de",
    }

    assert approved.isdisjoint(selection)
    assert held <= selection
    assert all(
        clips[clip_id]["enabled"]
        and clips[clip_id]["audio_review_status"] == "approved"
        for clip_id in approved
    )
    assert all(
        not clips[clip_id]["enabled"]
        and clips[clip_id]["audio_review_status"] == "hold"
        for clip_id in held
    )


def test_manifest_allows_enabled_youtube_provenance_after_approval(tmp_path):
    manifest = tmp_path / "clips.json"
    manifest.write_text(
        json.dumps(
            [
                {
                    "id": "approved-clip",
                    "artist": "Approved Artist",
                    "phrase": "Approved",
                    "audio": "audio/approved.mp3",
                    "enabled": True,
                    "source_provider": "youtube",
                    "source_url": "https://www.youtube.com/watch?v=example",
                }
            ]
        ),
        encoding="utf-8",
    )

    assert load_manifest_clips(manifest)[0]["enabled"] is True


def test_manifest_loader_rejects_duplicate_ids_and_nonfinite_windows(tmp_path):
    base_clip = {
        "id": "test-clip",
        "artist": "Test Artist",
        "phrase": "Test",
        "audio": "audio/test.mp3",
        "enabled": False,
    }
    manifest = tmp_path / "clips.json"
    manifest.write_text(json.dumps([base_clip, base_clip]), encoding="utf-8")
    with pytest.raises(AudioLabError, match="Duplicate clip id"):
        load_manifest_clips(manifest)

    invalid = dict(base_clip, review_start=math.inf)
    manifest.write_text(json.dumps([invalid]), encoding="utf-8")
    with pytest.raises(AudioLabError, match="review_start"):
        load_manifest_clips(manifest)


def test_manifest_loader_validates_staged_youtube_window(tmp_path):
    manifest = tmp_path / "clips.json"
    manifest.write_text(
        json.dumps(
            [
                {
                    "id": "staged-clip",
                    "artist": "Staged Artist",
                    "phrase": "Phrase",
                    "audio": "audio/staged.mp3",
                    "enabled": False,
                    "source_provider": "youtube",
                    "source_url": "https://www.youtube.com/watch?v=example",
                    "source_start_seconds": 47,
                    "source_end_seconds": 48,
                    "source_context_seconds": 1.5,
                    "review_start": 0,
                    "review_duration": 1,
                    "clearance_status": "needs-confirmation",
                }
            ]
        ),
        encoding="utf-8",
    )

    with pytest.raises(AudioLabError, match="must match the source"):
        load_manifest_clips(manifest)


def test_windowed_filter_trims_context_before_rendering():
    assert windowed_filter("highpass=f=70") == "highpass=f=70"
    assert windowed_filter("highpass=f=70", 1.5, 2) == (
        "atrim=start=1.5:duration=2,"
        "asetpts=PTS-STARTPTS,"
        "highpass=f=70"
    )


def test_source_download_window_preserves_context_and_core_timecode():
    clip = {
        "id": "staged-clip",
        "source_start_seconds": 47,
        "source_end_seconds": 48,
        "source_context_seconds": 1.5,
    }

    assert source_download_window(clip) == (45.5, 49.5, 1.5, 1.0)


def test_fetch_external_source_builds_a_sectioned_youtube_download(
    tmp_path, monkeypatch
):
    clip = {
        "id": "staged-clip",
        "artist": "Staged Artist",
        "phrase": "Phrase",
        "audio": "audio/staged.mp3",
        "enabled": False,
        "source_provider": "youtube",
        "source_url": "https://www.youtube.com/watch?v=example",
        "source_start_seconds": 47,
        "source_end_seconds": 48,
        "source_context_seconds": 1.5,
        "review_start": 1.5,
        "review_duration": 1,
        "clearance_status": "needs-confirmation",
    }
    commands = []

    monkeypatch.setattr(audio_lab, "require_executable", lambda name: name)
    monkeypatch.setattr(audio_lab, "audio_duration", lambda path: 4.0)

    def fake_run(command):
        commands.append(command)
        (tmp_path / "staged-clip.wav").write_bytes(b"fetched")

    monkeypatch.setattr(audio_lab, "run_command", fake_run)

    fetched = fetch_external_source(clip, tmp_path)

    assert fetched == tmp_path / "staged-clip.wav"
    assert len(commands) == 1
    assert commands[0][0] == "yt-dlp"
    assert commands[0][commands[0].index("--format") + 1] == (
        "bestaudio[ext=m4a]/bestaudio/best"
    )
    assert commands[0][commands[0].index("--download-sections") + 1] == "*45.5-49.5"
    assert commands[0][-1] == clip["source_url"]


def test_fetch_external_source_retries_with_generic_audio_format(
    tmp_path, monkeypatch
):
    clip = {
        "id": "staged-clip",
        "artist": "Staged Artist",
        "phrase": "Phrase",
        "audio": "audio/staged.mp3",
        "enabled": False,
        "source_provider": "youtube",
        "source_url": "https://www.youtube.com/watch?v=example",
        "source_start_seconds": 47,
        "source_end_seconds": 48,
        "source_context_seconds": 1.5,
        "review_start": 1.5,
        "review_duration": 1,
        "clearance_status": "needs-confirmation",
    }
    commands = []

    monkeypatch.setattr(audio_lab, "require_executable", lambda name: name)
    monkeypatch.setattr(audio_lab, "audio_duration", lambda path: 4.0)

    def fake_run(command):
        commands.append(command)
        if len(commands) == 1:
            (tmp_path / "staged-clip.m4a.part").write_bytes(b"partial")
            raise subprocess.CalledProcessError(1, command)
        (tmp_path / "staged-clip.wav").write_bytes(b"fetched")

    monkeypatch.setattr(audio_lab, "run_command", fake_run)

    assert fetch_external_source(clip, tmp_path) == (
        tmp_path / "staged-clip.wav"
    )
    assert len(commands) == 2
    assert commands[1][commands[1].index("--format") + 1] == "bestaudio/best"
    assert not (tmp_path / "staged-clip.m4a.part").exists()


def test_fetch_external_source_rejects_live_clips(tmp_path):
    with pytest.raises(AudioLabError, match="limited to disabled"):
        fetch_external_source(
            {
                "id": "live-clip",
                "enabled": True,
            },
            tmp_path,
        )


def test_build_queue_and_publish_approved_master(tmp_path):
    profiles_payload, profiles = load_profiles()
    profile = profiles[0]
    clips = [
        {
            "id": "test-clip",
            "artist": "Test Artist",
            "phrase": "Balle",
            "grade": "C",
            "audio": "audio/test.mp3",
            "enabled": True,
        }
    ]
    review_root = tmp_path / "review"
    static_root = tmp_path / "static"
    deployed = static_root / "audio" / "test.mp3"
    deployed.parent.mkdir(parents=True)
    deployed.write_bytes(b"deployed")
    clip_dir = review_root / "test-clip"
    clip_dir.mkdir(parents=True)
    (clip_dir / "reference.mp3").write_bytes(b"reference")
    winning = clip_dir / f"{profile.id}-vocal.mp3"
    winning.write_bytes(b"winner")
    (clip_dir / f"{profile.id}-voice-forward.mp3").write_bytes(b"forward")

    queue = build_queue(
        clips,
        profiles_payload,
        [profile],
        review_root,
        static_root,
    )
    assert len(queue["clips"]) == 1
    assert [variant["id"] for variant in queue["clips"][0]["variants"]] == [
        "deployed",
        "reference",
        f"{profile.id}-vocal",
        f"{profile.id}-voice-forward",
    ]
    assert (clip_dir / "deployed.mp3").read_bytes() == deployed.read_bytes()
    assert all(
        len(variant["sha256"]) == 64
        for variant in queue["clips"][0]["variants"]
    )

    masters = tmp_path / "masters"
    published = publish_approved(
        queue,
        {
            "test-clip": {
                "decision": "approve",
                "selected_variant": f"{profile.id}-vocal",
            }
        },
        clips,
        review_root=review_root,
        master_root=masters,
        static_dir=static_root,
    )
    assert published == [masters / "test-clip.mp3"]
    assert published[0].read_bytes() == b"winner"

    with pytest.raises(AudioLabError, match="no longer matches the review"):
        publish_approved(
            queue,
            {
                "test-clip": {
                    "decision": "approve",
                    "selected_variant": f"{profile.id}-vocal",
                    "selected_variant_sha256": "0" * 64,
                }
            },
            clips,
            review_root=review_root,
            master_root=masters,
            static_dir=static_root,
        )


def test_cached_output_must_be_nonempty_and_newer_than_inputs(tmp_path):
    source = tmp_path / "source.wav"
    output = tmp_path / "output.mp3"
    source.write_bytes(b"source")
    output.write_bytes(b"output")
    source_time = source.stat().st_mtime_ns
    os.utime(output, ns=(source_time + 1, source_time + 1))

    assert has_cached_output(output, (source,))

    os.utime(source, ns=(source_time + 2, source_time + 2))
    assert not has_cached_output(output, (source,))

    output.write_bytes(b"")
    assert not has_cached_output(output)


def test_review_bundle_requires_every_decodable_variant(tmp_path, monkeypatch):
    profiles_payload, profiles = load_profiles()
    clip = {
        "id": "test-clip",
        "artist": "Test Artist",
        "phrase": "Balle",
        "audio": "audio/test.mp3",
        "enabled": False,
    }
    review_root = tmp_path / "review"
    clip_dir = review_root / clip["id"]
    clip_dir.mkdir(parents=True)
    paths = [clip_dir / "reference.mp3"]
    for profile in profiles:
        paths.extend(
            [
                clip_dir / f"{profile.id}-vocal.mp3",
                clip_dir / f"{profile.id}-voice-forward.mp3",
            ]
        )
    for path in paths:
        path.write_bytes(b"audio")
    monkeypatch.setattr(audio_lab, "audio_duration", lambda path: 1.0)

    queue = build_queue([clip], profiles_payload, profiles, review_root)
    validate_review_bundle(queue, [clip], profiles, review_root)

    paths[-1].write_bytes(b"")
    with pytest.raises(AudioLabError, match="missing, or empty"):
        validate_review_bundle(queue, [clip], profiles, review_root)


def test_encode_vocal_regenerates_empty_cache_and_sanitizes_nonfinite_samples(
    tmp_path, monkeypatch
):
    vocal = tmp_path / "vocal.wav"
    vocal.write_bytes(b"vocal")
    output = tmp_path / "review.mp3"
    output.write_bytes(b"")
    commands = []

    monkeypatch.setattr(audio_lab, "require_executable", lambda name: name)
    monkeypatch.setattr(audio_lab, "run_command", commands.append)

    audio_lab.encode_vocal(vocal, output)

    assert len(commands) == 1
    audio_filter = commands[0][commands[0].index("-af") + 1]
    assert "isnan(val(ch))+isinf(val(ch))" in audio_filter

    output.write_bytes(b"cached")
    commands.clear()
    audio_lab.encode_vocal(vocal, output)
    assert commands == []


def test_all_loudness_filters_sanitize_nonfinite_samples(tmp_path, monkeypatch):
    commands = []
    monkeypatch.setattr(audio_lab, "require_executable", lambda name: name)
    monkeypatch.setattr(audio_lab, "run_command", commands.append)

    source = tmp_path / "source.wav"
    vocal = tmp_path / "vocal.wav"
    audio_lab.encode_reference(source, tmp_path / "reference.mp3")
    audio_lab.encode_voice_forward(
        vocal,
        source,
        tmp_path / "voice-forward.mp3",
        -18,
    )

    sanitizer = "isnan(val(ch))+isinf(val(ch))"
    reference_filter = commands[0][commands[0].index("-af") + 1]
    forward_filter = commands[1][commands[1].index("-filter_complex") + 1]
    assert sanitizer in audio_lab.VOCAL_FILTER
    assert sanitizer in audio_lab.REFERENCE_FILTER
    assert sanitizer in reference_filter
    assert sanitizer in forward_filter
