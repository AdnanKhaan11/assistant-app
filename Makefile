.PHONY: run test obs-up obs-down console

run:            ## run the API with OpenTelemetry -> collector
	uvicorn backend.main:app --reload --port 8000

console:        ## run the API printing telemetry to the terminal (no docker needed)
	TELEMETRY_EXPORTER=console LOG_FORMAT=text uvicorn backend.main:app --port 8000

test:
	pytest -v

obs-up:         ## start collector, jaeger, prometheus, loki, grafana
	docker compose -f deploy/docker-compose.yml up -d

obs-down:
	docker compose -f deploy/docker-compose.yml down
