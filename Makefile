# EdgeGuard — common commands. Run `make help` to list them.
# Python lives in a local virtualenv (.venv) so nothing is installed system-wide.

PY := .venv/bin/python

.PHONY: help venv train up down test demo check-m0 check-m1 check-m2 check-m3 check-m4 check-m5 plc edge broker dashboard

help:            ## Show this list
	@grep -E '^[a-z0-9-]+:.*##' $(MAKEFILE_LIST) | sed 's/:.*##/ —/'

venv:            ## Create .venv with Python 3.11 and install requirements (needs `uv`)
	uv venv --python 3.11 .venv
	uv pip install --python $(PY) -r requirements.txt

train:           ## Train models, choose threshold, evaluate, export ONNX (M1)
	$(PY) training/train.py
	$(PY) training/choose_threshold.py
	$(PY) training/calibrate_drift.py
	$(PY) training/evaluate.py
	$(PY) training/rules_check.py
	$(PY) training/export_onnx.py

plc:             ## Run the simulated PLC (OPC UA server) locally on port 4840
	$(PY) -m plc_sim.server

edge:            ## Run the edge app locally (needs a PLC and the broker)
	$(PY) -m edge_app.main

dashboard:       ## Run the dashboard dev server on :5173 (needs nvm Node, see README)
	cd dashboard && npm install && npm run dev

broker:          ## Start only the MQTT broker (Docker)
	docker compose up -d mosquitto

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

check-m2:        ## Milestone 2 check: PLC simulator OPC UA interface
	$(PY) tests/check_m2.py

check-m3:        ## Milestone 3 check: PLC -> edge app -> write-back + MQTT, 500 readings
check-m3: broker
	$(PY) tests/check_m3.py

check-m4:        ## Milestone 4 check: drift warning + store-and-forward through a 20 s broker outage
check-m4: broker
	$(PY) tests/check_m4.py

check-m5:        ## Milestone 5 check: dashboard tests/build + demo commands, heartbeat, simulated outage
check-m5: broker
	$(PY) tests/check_m5.py
