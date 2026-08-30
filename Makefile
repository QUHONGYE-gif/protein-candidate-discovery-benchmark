.PHONY: format lint test check replay-smoke m5b-synthetic-smoke

PYTHON ?= python

format:
	$(PYTHON) -m ruff format .
	$(PYTHON) -m ruff check --fix .

lint:
	$(PYTHON) -m ruff format --check .
	$(PYTHON) -m ruff check .
	$(PYTHON) -m mypy

test:
	$(PYTHON) -m pytest

check: lint test

replay-smoke:
	$(PYTHON) -m pytest tests/replay/test_hidden_oracle.py tests/replay/test_m3_replay.py tests/m3/test_m3_gate_a_packet.py tests/m3/test_m3_gate_a_runtime.py tests/m3/test_m3_gate_b_packet.py tests/m3/test_m3_gate_b_runtime.py tests/contracts/test_m3_protocol_v1.py tests/contracts/test_run_spec.py

m5b-synthetic-smoke:
	$(PYTHON) -m pytest tests/m5/test_same_state.py
