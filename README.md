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
- Separate App Engine Standard configurations for private dev and public prod
- Manifest, route, asset, and security-header tests
- Multi-model vocal-isolation lab with local A/B review and approval
- A 30-artist acquisition backlog targeting 90 additional sounds

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

The audio workstation is intentionally isolated from the App Engine
dependencies. It requires Apple Silicon or another supported PyTorch platform,
Python 3.10+, FFmpeg, and the pinned `audio-separator` package.

```sh
brew install ffmpeg python@3.12
make audio-install
make audio-doctor
```

`content/audio_profiles.json` defines the reproducible model bake-off:
BS-RoFormer, Mel-RoFormer, UVR MDX Vocal FT, and fine-tuned HTDemucs.
For each profile, the lab creates a vocal-only variant and a voice-forward
variant with the original bed mixed at -22 dB.

Place higher-quality owner-supplied source media at
`media/source/CLIP_ID.wav`. When no raw source is present, the lab uses the
current deployed MP3 as a fallback. Process one clip first:

```sh
make audio-prepare AUDIO_ARGS="--clip jazzy-rambo --profile bs-roformer"
```

Short external excerpts can be staged for internal review only by recording a
canonical YouTube URL, core start/end timecodes, context handles,
`clearance_status`, and a matching review window in `content/clips.json`.
Keep these candidates disabled, then fetch and render only explicitly named
clips:

```sh
make audio-fetch AUDIO_ARGS="--clip miss-pooja-gidha-pao"
make audio-prepare AUDIO_ARGS="--include-disabled --clip miss-pooja-gidha-pao"
```

Both `prepare` and an explicitly scoped `queue` require at least one `--clip`
whenever `--include-disabled` is used. With no clip arguments,
`make audio-queue` builds the declarative full catalog: every live board sound
plus only the disabled ids listed in `content/audio_lab_selection.json`.
Fetching or isolating an excerpt does not grant rights; external-source
candidates remain review-only until recording, composition, and portrait
clearance are documented.

Model weights download into the ignored `media/models/` directory. Separated
WAVs and the complete working review set remain under ignored `media/`
directories. Only a deliberately selected, lightweight review bundle is copied
into `content/review_queue.json` and `static/review-audio/` for an admin-gated
deployment; models and stems never ship to App Engine. Those two deployable
review paths are also gitignored because the GitHub repository is public.
`.gcloudignore` explicitly includes the local copies for App Engine, so prepare
the private bundle on the trusted deployment machine before releasing a new
version.

Rebuild and package that deployable bundle after board audio or catalog
membership changes:

```sh
make audio-package
```

Launch the local comparison desk:

```sh
make audio-review
```

Visit `http://127.0.0.1:9090`, play the model variants, score clarity, music
suppression, artifacts, and recognizability, then approve, hold, or request a
better source. Approved variants can be copied into `media/masters/`:

```sh
make audio-publish
```

The deployed dev version exposes the same desk at `/audio-review`. Its page,
API, UI assets, and MP3s are each covered by App Engine's `login: admin`
handlers. Every live clip includes an exact byte-for-byte `deployed` variant,
and every review variant carries a SHA-256 identity. Remote decisions are
stored one clip per JSON object in the app's private Cloud Storage bucket under
the stable `catalog-v1` namespace; a saved hash prevents a changed audio file
from silently inheriting an old approval.

Replacing a deployed MP3 is deliberately explicit and creates a timestamped
backup:

```sh
make audio-publish AUDIO_ARGS="--clip jazzy-rambo --replace-static"
```

The older single-file `scripts/process_audio.py` remains useful for precise
trimming and two-pass loudness normalization after a winning stem has been
chosen.

Always audition the isolated output against a lightly cleaned original.
Source separation can introduce metallic or watery artifacts, especially when
the only source is a low-bitrate MP3.

### Artist expansion

`content/artist_backlog.json` contains the first 30 candidate artists across
legends, modern performers, women, and rap/diaspora lanes. Each artist targets
three iconic sounds. Candidate track names are discovery prompts, not approved
sources.

New sounds move through:

1. owner-supplied or otherwise authorized source
2. phrase and timecode selection with extra audio handles
3. multi-model separation
4. local A/B quality review
5. portrait and rights review
6. manifest enablement and validation

Source separation does not change recording, composition, or publicity rights.
Track source and portrait permissions before enabling a candidate.

## App Engine deployment

`app.yaml` is the fully private development configuration. It applies
`login: admin` to the board, static assets, AudioLab, and AudioLab API.

`app.prod.yaml` is the production configuration. The main soundboard and its
ordinary static assets are public over HTTPS. AudioLab, its API, and all review
audio remain protected by `login: admin`. AudioLab is available at `/admin`
without being linked from the public board; `/audio-review` remains available
for existing bookmarks. A signed-in user with Viewer, Editor, Owner, or App
Engine App Admin on the production project can enter AudioLab.

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
rollback target.

Deploy production with the dedicated descriptor:

```sh
gcloud app deploy app.prod.yaml \
  --project=bhangraboard-prod \
  --version=prod-YYYYMMDD-N \
  --no-promote
```

Verify that `/` and `/static/...` return public content while `/audio-review`,
`/audio-review/api/...`, and `/static/review-audio/...` require an admin.
Promote only the verified version:

```sh
gcloud app services set-traffic default \
  --project=bhangraboard-prod \
  --splits=prod-YYYYMMDD-N=1
```

## Rights and credits

The prototype uses short excerpts supplied by the project owner and
generated, review-only artist likenesses in a shared portrait style. Recording
rights, artist likeness/publicity rights, and branding still require review
before a public launch.
