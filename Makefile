# makefile
DC = docker compose
D = docker
EXEC = docker exec -it
LOGS = docker logs
ENV = --env-file .env
LOCAL_FILE = docker-compose.local.yml
OBS_LOCAL_FILE = docker-compose.obs.yaml
STORAGES_FILE = docker_compose/storages.yaml
APP_CONTAINER = web-culture
SERVICE_APP_NAME = web
SERVICE_NGINX_NAME = nginx
SERVICE_CELERY_NAME = celery
IMAGE = miran2025/culture:0.2.3

.PHONY: app-logs
app-logs:
	@$(MAKE) app-sync
	@${DC} -f ${LOCAL_FILE} up --build

.PHONY: app-logs-down
app-logs-down:
	@${DC} -f ${LOCAL_FILE} down

.PHONY: restart
restart:
	@${DC} -f ${LOCAL_FILE} restart ${SERVICE_APP_NAME} ${SERVICE_NGINX_NAME} ${SERVICE_CELERY_NAME}

.PHONY: obs
obs:
	@${DC} -f ${OBS_LOCAL_FILE} up -d

.PHONY: obs-down
obs-down:
	@${DC} -f ${OBS_LOCAL_FILE} down

# Создать фикстуры
.PHONY: app-dump # make app-load path="fixtures/all_checklists_data_$(date +'%Y-%m-%d_%H:%M:%S').json"
app-dump:
	@${DC} -f ${LOCAL_FILE} exec ${APP_CONTAINER} sh -c "python manage.py dumpdata checklists --indent 4 --output $(path)"

.PHONY: app-load # make app-load path="fixtures/all_checklists_data.json"
app-load:
	@${DC} -f ${LOCAL_FILE} exec ${APP_CONTAINER} sh -c "python manage.py loaddata $(path)"

# Создать миграции
.PHONY: local-migrations # make migrations app="users"
local-migrations:
ifeq ($(strip $(app)),)
	@${DC} -f ${LOCAL_FILE} exec ${SERVICE_APP_NAME} python manage.py makemigrations
else
	@${DC} -f ${LOCAL_FILE} exec ${SERVICE_APP_NAME} python manage.py makemigrations $(app)
endif

# Применить миграции
.PHONY: local-migrate
local-migrate:
	@${DC} -f ${LOCAL_FILE} exec ${SERVICE_APP_NAME} python manage.py migrate

.PHONY: app-sync
app-sync:
	@uv sync


.PHONY: app-build
app-build:
	@${D} build -t ${IMAGE} .

.PHONY: app-push
app-push:
	@${D} push ${IMAGE}

.PHONY: app-test-rebuild-push
app-test-rebuild-push:
	@$(MAKE) test
	@$(MAKE) app-del
	@$(MAKE) cash
	@$(MAKE) app-build
	@$(MAKE) app-push

.PHONY: app-del
app-del:
	@if ${D} image inspect ${IMAGE} >/dev/null 2>&1; then \
		${D} rmi ${IMAGE}; \
	fi

.PHONY: cash
cash:
	@${D} system prune -f

.PHONY: app-rebuild-new-image
app-rebuild-new-image:
	@$(MAKE) app-down
	@$(MAKE) app-del
	@$(MAKE) cash
	@$(MAKE) app
	@$(MAKE) wait-for-web
	@$(MAKE) migrations
	@$(MAKE) migrate

.PHONY: migrations
migrations:
	@${DC} -f ${PROD_FILE} exec ${SERVICE_APP_NAME} python manage.py makemigrations

.PHONY: migrate
migrate:
	@${DC} -f ${PROD_FILE} exec ${SERVICE_APP_NAME} python manage.py migrate

.PHONY: wait-for-web
wait-for-web:
	@echo "Waiting for web container to be ready..."
	@while ! ${DC} -f ${PROD_FILE} exec ${SERVICE_APP_NAME} echo "ready" 2>/dev/null; do \
		sleep 1; \
	done
	@sleep 2


.PHONY: test
test:
	@${DC} -f ${LOCAL_FILE} exec ${SERVICE_APP_NAME} uv run pytest tests

# docker compose -f docker-compose.local.yml exec web uv run pytest
# .PHONY: size
# size:
# 	@${D} system df
#
