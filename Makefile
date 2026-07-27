.PHONY: venv-install test validate run

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
