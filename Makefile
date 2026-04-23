# EvalOps top-level Makefile
# All targets are safe to run from the repo root.

.PHONY: help
help: ## Show this help
	@awk 'BEGIN {FS = ":.*##"; printf "\nUsage:\n  make \033[36m<target>\033[0m\n\nTargets:\n"} /^[a-zA-Z_-]+:.*?##/ { printf "  \033[36m%-22s\033[0m %s\n", $$1, $$2 }' $(MAKEFILE_LIST)

# ---------- Infra ----------

.PHONY: infra-up
infra-up: ## Start local stack (PG, Redis, Jaeger, Prometheus, Grafana, MinIO)
	cd infra && docker compose up -d

.PHONY: infra-down
infra-down: ## Stop local stack (keep volumes)
	cd infra && docker compose down

.PHONY: infra-nuke
infra-nuke: ## Stop local stack AND delete volumes
	cd infra && docker compose down -v

.PHONY: infra-ps
infra-ps: ## Show local stack status
	cd infra && docker compose ps

# ---------- Python eval-engine ----------

.PHONY: py-install
py-install: ## Install eval-engine in editable mode with dev deps
	cd services/eval-engine && pip install -e ".[dev]"

.PHONY: py-test
py-test: ## Run eval-engine unit tests
	cd services/eval-engine && pytest -q

.PHONY: py-lint
py-lint: ## Ruff lint eval-engine
	cd services/eval-engine && ruff check src tests

# ---------- Go control-plane ----------

.PHONY: go-build
go-build: ## Build control-plane binary
	cd services/control-plane && go build -o bin/control-plane ./cmd/server

.PHONY: go-run
go-run: ## Run control-plane locally
	cd services/control-plane && go run ./cmd/server

.PHONY: go-test
go-test: ## Run Go tests
	cd services/control-plane && go test ./...

# ---------- Protos ----------

.PHONY: proto-lint
proto-lint: ## Lint protos with buf
	cd proto && buf lint

.PHONY: proto-gen
proto-gen: ## Regenerate Go + Python stubs from proto/ (uses buf remote plugins)
	cd proto && buf generate
	@echo "stubs regenerated — commit the diff"

.PHONY: proto-check
proto-check: ## Fail if committed proto stubs differ from what buf would generate
	cd proto && buf generate
	@if ! git diff --quiet -- \
		services/control-plane/internal/proto \
		services/eval-engine/src/evalops/v1; then \
		echo "ERROR: proto stubs out of sync. Run 'make proto-gen' and commit."; \
		git diff --stat -- services/control-plane/internal/proto services/eval-engine/src/evalops/v1; \
		exit 1; \
	fi
	@echo "OK: committed proto stubs match generator output"

# ---------- SUT extension (Agent sidecar) ----------

.PHONY: sidecar-deploy
sidecar-deploy: ## Rsync the sidecar source into the reference-app tree
	./scripts/deploy-sidecar.sh

.PHONY: sidecar-reinstall
sidecar-reinstall: ## Rsync AND pip install -e the deployed sidecar
	./scripts/deploy-sidecar.sh --reinstall

.PHONY: sidecar-check
sidecar-check: ## Fail if the sidecar source and deploy target have diverged
	./scripts/deploy-sidecar.sh --check

# ---------- Smoke test ----------

.PHONY: smoke
smoke: ## Run end-to-end smoke test against the mock SUT
	cd services/eval-engine && python -m evalops.cli run \
		--benchmark ../../datasets/rag-toy \
		--sut mock \
		--out ../../runs/smoke.json
	cd services/eval-engine && python -m evalops.cli report ../../runs/smoke.json
