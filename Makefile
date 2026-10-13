# Common tasks for this agent, in one place.
#
# Run `make` with no target to list everything. All of it runs from this
# directory and assumes Colima is the Docker engine (see the README's
# Prerequisites). The two layers are kept distinct on purpose: `make stop` stops
# the agent, `make down` also shuts the engine down -- which stops every other
# container with it.
#
# LINE is used only by `deploy`; pass it inline:
#   make deploy LINE=ln_abc123

.DEFAULT_GOAL := help
LINE ?=

# --- lifecycle ---------------------------------------------------------------

.PHONY: help
help: ## List these targets
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-9s\033[0m %s\n", $$1, $$2}'

.PHONY: start
start: ## Boot the engine (if needed), build, and start the agent
	colima status >/dev/null 2>&1 || colima start
	docker compose up -d --build

.PHONY: up
up: ## Start the agent — no build; the engine is assumed up
	docker compose up -d

.PHONY: stop
stop: ## Stop and remove the agent (keeps the engine and the /data volume)
	docker compose down

.PHONY: down
down: ## Stop the agent AND shut the engine down
	docker compose down
	colima stop

.PHONY: restart
restart: ## Restart the agent container (no rebuild)
	docker compose restart

.PHONY: build
build: ## Build the image only
	docker compose build

# --- watching ----------------------------------------------------------------

.PHONY: logs
logs: ## Follow the agent's output (Ctrl-C stops watching, not the agent)
	docker compose logs -f

.PHONY: ps
ps: ## Show what is running
	docker compose ps

.PHONY: shell
shell: ## Open a shell in the agent container
	docker compose exec agent sh

# --- first run / teardown ----------------------------------------------------

.PHONY: deploy
deploy: ## Mint a credential, then build and start — make deploy LINE=ln_xxx
	@test -n "$(LINE)" || { echo "set LINE, e.g. make deploy LINE=ln_abc123"; exit 1; }
	plow-agents deploy --local --line $(LINE)

.PHONY: clean
clean: ## Stop everything and delete the /data volume (fresh checkpoint next boot)
	docker compose down -v
