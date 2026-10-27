COMPOSE ?= docker compose

.PHONY: image run migrate

image:
	$(COMPOSE) build ai-library

run:
	$(COMPOSE) up -d --wait --wait-timeout 60 ai-library
	$(COMPOSE) ps ai-library

# Maintenance operation: stop API writers before upgrading and backing up the database.
migrate:
	$(COMPOSE) stop ai-library
	$(COMPOSE) run --rm --no-deps ai-library ai-library migrate
