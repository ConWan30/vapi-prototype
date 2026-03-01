# VAPI Project — Makefile
# Run 'make help' to see all available targets.
#
# Platform note: This Makefile targets bash on Linux/macOS and Git Bash on Windows.
# On native Windows cmd.exe, run commands directly (see target definitions below).

PYTHON      ?= python
PYTEST      ?= $(PYTHON) -m pytest
BRIDGE_TESTS = bridge/tests/
SDK_TESTS    = sdk/tests/
HW_TESTS     = tests/hardware/
EXTRA_IGNORE = --ignore=bridge/tests/test_e2e_simulation.py

.PHONY: help test test-bridge test-sdk test-contracts test-hardware test-e2e test-all \
        lint lint-py lint-sol coverage capture docs clean

# ---------------------------------------------------------------------------
# Default target
# ---------------------------------------------------------------------------
help:
	@echo "VAPI Project Makefile"
	@echo ""
	@echo "Test targets:"
	@echo "  make test            Run all non-hardware tests (bridge + sdk)"
	@echo "  make test-bridge     Run bridge pytest (~728 tests)"
	@echo "  make test-sdk        Run SDK pytest (28 tests)"
	@echo "  make test-contracts  Run Hardhat tests (~341 tests)"
	@echo "  make test-hardware   Run hardware tests (requires DualShock Edge)"
	@echo "  make test-e2e        Run E2E tests (requires Hardhat node at :8545)"
	@echo "  make test-all        Run ALL tests including hardware"
	@echo ""
	@echo "Quality targets:"
	@echo "  make lint            Run all linters (ruff + solhint)"
	@echo "  make lint-py         Run ruff on Python sources"
	@echo "  make lint-sol        Run solhint on Solidity contracts"
	@echo "  make coverage        Run bridge pytest with coverage report"
	@echo ""
	@echo "Hardware targets:"
	@echo "  make capture         Capture 60s DualShock Edge session"
	@echo "  make calibrate       Run threshold calibrator on sessions/*.json"
	@echo "  make first-session   Run guided first hardware session protocol"
	@echo ""
	@echo "Other targets:"
	@echo "  make docs            Open docs/README.md"
	@echo "  make clean           Remove __pycache__, .pyc, test artifacts"

# ---------------------------------------------------------------------------
# Core test targets
# ---------------------------------------------------------------------------
test: test-bridge test-sdk

test-bridge:
	@echo "=== Running Bridge Tests ==="
	$(PYTEST) $(BRIDGE_TESTS) $(EXTRA_IGNORE) -q --tb=short

test-sdk:
	@echo "=== Running SDK Tests ==="
	$(PYTEST) $(SDK_TESTS) -v --tb=short

test-contracts:
	@echo "=== Running Hardhat Contract Tests ==="
	cd contracts && npx hardhat test

test-hardware:
	@echo "=== Running Hardware Tests (DualShock Edge required) ==="
	$(PYTEST) $(HW_TESTS) -v -m hardware --tb=short

test-e2e:
	@echo "=== Running E2E Tests (requires Hardhat node at http://127.0.0.1:8545) ==="
	HARDHAT_RPC_URL=http://127.0.0.1:8545 $(PYTEST) bridge/tests/test_e2e_simulation.py -v --tb=short

test-all: test-bridge test-sdk test-contracts test-hardware

# Phase 37 targeted tests
test-phase37:
	@echo "=== Phase 37 Targeted Tests ==="
	$(PYTEST) \
		bridge/tests/test_credential_enforcement_store.py \
		bridge/tests/test_credential_suspension.py \
		bridge/tests/test_tournament_gate_v3.py \
		bridge/tests/test_alert_router.py \
		bridge/tests/test_enforcement_endpoint.py \
		bridge/tests/test_enforcement_agent_tool.py \
		-v --tb=short

# ---------------------------------------------------------------------------
# Coverage
# ---------------------------------------------------------------------------
coverage:
	@echo "=== Bridge Coverage Report ==="
	$(PYTEST) $(BRIDGE_TESTS) $(EXTRA_IGNORE) \
		--cov=bridge/vapi_bridge \
		--cov-report=term-missing \
		--cov-report=html:htmlcov \
		-q

# ---------------------------------------------------------------------------
# Linting
# ---------------------------------------------------------------------------
lint: lint-py lint-sol

lint-py:
	@echo "=== Python Linting (ruff) ==="
	@if command -v ruff >/dev/null 2>&1; then \
		ruff check bridge/vapi_bridge/ sdk/ controller/ scripts/ tests/; \
	else \
		echo "ruff not installed. Run: pip install ruff"; \
	fi

lint-sol:
	@echo "=== Solidity Linting (solhint) ==="
	@if command -v solhint >/dev/null 2>&1; then \
		cd contracts && solhint 'contracts/**/*.sol'; \
	else \
		echo "solhint not installed. Run: npm install -g solhint"; \
	fi

# ---------------------------------------------------------------------------
# Hardware capture targets
# ---------------------------------------------------------------------------
capture:
	@echo "=== Capturing 60s DualShock Edge Session ==="
	$(PYTHON) scripts/capture_session.py --duration 60

capture-long:
	@echo "=== Capturing 300s DualShock Edge Session ==="
	$(PYTHON) scripts/capture_session.py --duration 300

calibrate:
	@echo "=== Running Threshold Calibrator ==="
	@if ls sessions/*.json 1>/dev/null 2>&1; then \
		$(PYTHON) scripts/threshold_calibrator.py sessions/*.json; \
	else \
		echo "No session files found in sessions/. Run 'make capture' first."; \
	fi

first-session:
	@echo "=== Guided First Hardware Session ==="
	$(PYTHON) scripts/first_session_protocol.py

# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------
docs:
	@echo "Documentation index: docs/README.md"
	@echo "Architecture: docs/architecture.md"
	@echo "Hardware guide: docs/hardware-testing-guide.md"
	@echo "Detection benchmarks: docs/detection-benchmarks.md"

clean:
	@echo "=== Cleaning build artifacts ==="
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -name "*.pyc" -delete 2>/dev/null || true
	find . -name ".pytest_cache" -type d -exec rm -rf {} + 2>/dev/null || true
	find . -name "htmlcov" -type d -exec rm -rf {} + 2>/dev/null || true
	find . -name ".coverage" -delete 2>/dev/null || true
	@echo "Clean complete."
