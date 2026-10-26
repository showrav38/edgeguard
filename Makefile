# EdgeGuard — common commands. Run `make help` to list them.
# Python lives in a local virtualenv (.venv) so nothing is installed system-wide.

PY := .venv/bin/python

.PHONY: help venv train up down test demo check-m0 check-m1

help:            ## Show this list
	@grep -E '^[a-z0-9-]+:.*##' $(MAKEFILE_LIST) | sed 's/:.*##/ —/'

venv:            ## Create .venv with Python 3.11 and install requirements (needs `uv`)
	uv venv --python 3.11 .venv
	uv pip install --python $(PY) -r requirements.txt

train:           ## Train models, choose threshold, evaluate, export ONNX (M1)
	$(PY) training/train.py
	$(PY) training/choose_threshold.py
	$(PY) training/evaluate.py
	$(PY) training/rules_check.py
	$(PY) training/export_onnx.py

up:              ## Start all services with Docker compose (M6)
	docker compose up -d --build

down:            ## Stop all services
	docker compose down

test:            ## Run unit tests
	$(PY) -m pytest -q tests

demo:            ## Run the 2-minute scripted demo (M6)
	$(PY) scripts/demo.py

check-m0:        ## Milestone 0 check: skeleton is complete
	python3 tests/check_m0.py

check-m1:        ## Milestone 1 check: ONNX models, quality, latency
	$(PY) tests/check_m1.py
