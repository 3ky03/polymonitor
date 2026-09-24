.PHONY: dev api web-build status services-install services-start services-start-data services-restart services-restart-data services-stop services-status services-logs services-doctor

API_HOST ?= 127.0.0.1
API_PORT ?= 18500

dev:
	bash scripts/start_dashboard.sh

api:
	bash scripts/start_dashboard.sh

web-build:
	cd webpage && npm run build

status:
	@echo "API health:"
	@curl -fsS "http://$(API_HOST):$(API_PORT)/health"
	@echo
	@echo "System health:"
	@curl -fsS "http://$(API_HOST):$(API_PORT)/system/health"
	@echo

services-install:
	bash scripts/ops/polydata_services.sh install

services-start:
	bash scripts/ops/polydata_services.sh start

services-start-data:
	bash scripts/ops/polydata_services.sh start-data

services-restart:
	bash scripts/ops/polydata_services.sh restart

services-restart-data:
	bash scripts/ops/polydata_services.sh restart-data

services-stop:
	bash scripts/ops/polydata_services.sh stop

services-status:
	bash scripts/ops/polydata_services.sh status

services-logs:
	bash scripts/ops/polydata_services.sh logs $(SERVICE)

services-doctor:
	bash scripts/ops/polydata_services.sh doctor
