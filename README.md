# Bhangra Board

A playful, responsive soundboard for iconic Bhangra and Punjabi rap voices.
Tap an artist face to play a short clip; repeated faces represent different
sounds from the same artist.

The old Android prototype is preserved in the `legacy-android` Git tag.

## What is implemented

- Flask application with a source-controlled clip manifest
- Responsive seven-column desktop and three-column mobile board
- Search by artist or phrase
- Number-key shortcuts for the first ten visible sounds
- Replay, limited overlapping playback, playing-state animation, and live status
- Creative Commons portrait attribution
- App Engine Standard configuration protected by `login: admin`
- Manifest, route, asset, and security-header tests
- Repeatable FFmpeg cleanup and optional HTDemucs vocal-isolation script

## Run locally

Python 3.12 or newer is recommended.

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-dev.txt
flask --app main run --debug --port 8080
```

Visit `http://127.0.0.1:8080`. App Engine's `login: admin` gate is applied by
the platform in deployment and is intentionally not simulated by Flask.

Run verification:

```sh
pytest
python -m scripts.validate_content
```

## Content workflow

`content/clips.json` is the source of truth. Every enabled entry needs:

- a globally unique `id`
- artist and phrase labels
- an MP3 under `static/audio/`
- a portrait under `static/images/artists/`
- `enabled: true`

Ambiguous or unapproved clips stay in the manifest with `enabled: false`.
The application only sends public display fields to the browser; internal
source IDs and review notes remain server-side.

### Audio cleanup

Install FFmpeg, and optionally install a Demucs command-line environment for
source separation. Raw and working audio belongs in the ignored `media/`
directories; only approved short MP3s belong under `static/audio/`.

```sh
python scripts/process_audio.py \
  media/source/example.wav \
  static/audio/example.mp3 \
  --start 12.4 \
  --duration 2.1 \
  --isolate-vocals
```

The script can extract the fine-tuned HTDemucs vocal stem, then performs
high-pass filtering, two-pass EBU R128 normalization to -16 LUFS / -1 dBTP,
short fades, and a 160 kbps MP3 export.

Always audition the isolated output against a lightly cleaned original.
Source separation can introduce metallic or watery artifacts, especially when
the only source is a low-bitrate MP3.

## Admin-only App Engine deployment

The current configuration uses App Engine's bundled Users service and applies
`login: admin` to both the static directory and dynamic catch-all. A signed-in
user with Viewer, Editor, Owner, or App Engine App Admin on the project can
enter the site.

Authenticate and select the existing project:

```sh
gcloud auth login
gcloud config set project PROJECT_ID
```

If the project does not already have an App Engine application, choose its
region deliberately; it cannot be changed later:

```sh
gcloud app regions list
gcloud app create --region=REGION --ssl-policy=TLS_VERSION_1_2
```

Deploy a fresh, non-promoted development version:

```sh
gcloud app deploy app.yaml \
  --version=dev-YYYYMMDD-N \
  --no-promote
```

Use a new version name for each deployment so the previous version remains a
rollback target. Before public launch, deploy a separate version with an
explicitly reviewed authentication configuration.

## Rights and credits

The prototype uses short excerpts supplied by the project owner and
Creative Commons photographs credited in the in-app Photo Credits dialog.
Recording rights, artist likeness/publicity rights, and branding still require
review before a public launch.
