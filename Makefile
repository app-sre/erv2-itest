.DEFAULT_GOAL := test

CONTAINER_ENGINE ?= $(shell which podman >/dev/null 2>&1 && echo podman || echo docker)

# do not print pypi commands to avoid the token leaking to the logs
.SILENT: pypi
.PHONY: pypi
pypi:
	uv build --sdist --wheel
	UV_PUBLISH_TOKEN=$(shell cat /run/secrets/app-sre-pypi-credentials/token) \
		uv publish

.PHONY: format
format:
	uv run ruff check
	uv run ruff format

.PHONY: test
test:
	uv run ruff check --no-fix
	uv run ruff format --check
	uv run mypy
	uv run pytest -vv --cov=erv2_itest --cov-report=term-missing --cov-report xml

.PHONY: _test
_test: test

.PHONY: build
build:
	$(CONTAINER_ENGINE) build -t erv2-itest:test --target test .

SMOKETEST_OUTPUT_DIR := .erv2-itests-smoketest

.PHONY: smoketest
smoketest:
	@$(CONTAINER_ENGINE) build -q -t erv2it-fake:latest -f smoketest/Dockerfile smoketest 2>&1 >/dev/null
	@cp .gitignore .gitignore.smoketest-backup
	-uv run erv2-itest smoketest/scenario_pass.yaml smoketest/scenario_fail.yaml \
		--no-dry-run --output-dir $(SMOKETEST_OUTPUT_DIR)
	@mv .gitignore.smoketest-backup .gitignore
	@rm -rf $(SMOKETEST_OUTPUT_DIR)

DEMO_OUTPUT_DIR := .erv2-itests-demo

.PHONY: demo
demo:
	@$(CONTAINER_ENGINE) build -q -t erv2it-demo:latest -f demo/Dockerfile demo 2>&1 >/dev/null
	@cp .gitignore .gitignore.demo-backup
	-uv run erv2-itest --scenarios-dir demo --no-dry-run --output-dir $(DEMO_OUTPUT_DIR)
	@mv .gitignore.demo-backup .gitignore
	@rm -rf $(DEMO_OUTPUT_DIR)

.PHONY: dev-env
dev-env:
	uv sync
