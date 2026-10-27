COMPOSE ?= docker compose

.PHONY: image run

image:
	$(COMPOSE) build ai-library

run:
	$(COMPOSE) up -d --wait --wait-timeout 60 ai-library
	$(COMPOSE) ps ai-library
