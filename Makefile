.PHONY: venv-install test validate run audio-install audio-doctor audio-fetch audio-prepare audio-queue audio-package audio-review audio-publish

AUDIO_ARGS ?=

venv-install:
	python3 -m venv .venv
	.venv/bin/python -m pip install --upgrade pip
	.venv/bin/python -m pip install -r requirements-dev.txt

test:
	.venv/bin/python -m pytest

validate:
	.venv/bin/python -m scripts.validate_content

run:
	.venv/bin/flask --app main run --debug --port 8080

audio-install:
	/opt/homebrew/bin/python3.12 -m venv .audio-venv
	.audio-venv/bin/python -m pip install --upgrade pip
	.audio-venv/bin/python -m pip install -r requirements-audio.txt

audio-doctor:
	.audio-venv/bin/python -m scripts.audio_lab doctor

audio-fetch:
	.audio-venv/bin/python -m scripts.audio_lab fetch $(AUDIO_ARGS)

audio-prepare:
	.audio-venv/bin/python -m scripts.audio_lab prepare $(AUDIO_ARGS)

audio-queue:
	.audio-venv/bin/python -m scripts.audio_lab queue $(AUDIO_ARGS)

audio-package: audio-queue
	cp media/review/queue.json content/review_queue.json
	rsync -a --checksum --exclude='queue.json' --exclude='reviews.json' media/review/ static/review-audio/

audio-review:
	.venv/bin/python -m scripts.review_app

audio-publish:
	.audio-venv/bin/python -m scripts.audio_lab publish $(AUDIO_ARGS)
