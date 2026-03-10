# makefile
DC = docker compose
D = docker
EXEC = docker exec -it
LOGS = docker logs
ENV = --env-file .env
LOCAL_FILE = docker-compose.local.yml
STORAGES_FILE = docker_compose/storages.yaml
APP_CONTAINER = web
SERVICE_NAME = web
IMAGE = miran2025/culture:0.0.5

.PHONY: app-logs
app-logs:
	@$(MAKE) app-sync
	@${DC} -f ${LOCAL_FILE} up --build

.PHONY: app-logs-down
app-logs-down:
	@${DC} -f ${LOCAL_FILE} down


# Создать фикстуры
.PHONY: app-dump # make app-load path="fixtures/all_checklists_data_$(date +'%Y-%m-%d_%H:%M:%S').json"
app-dump:
	@${DC} -f ${LOCAL_FILE} exec ${APP_CONTAINER} sh -c "python manage.py dumpdata checklists --indent 4 --output $(path)"

.PHONY: app-load # make app-load path="fixtures/all_checklists_data.json"
app-load:
	@${DC} -f ${LOCAL_FILE} exec ${APP_CONTAINER} sh -c "python manage.py loaddata $(path)"


# Создать миграции
.PHONY: migrations # make migrate app="users"
migrations:
	@${DC} -f ${LOCAL_FILE} exec ${SERVICE_NAME} python manage.py makemigrations "$(app)"

# Применить миграции
.PHONY: migrate
migrate:
	@${DC} -f ${LOCAL_FILE} exec ${SERVICE_NAME} python manage.py migrate

.PHONY: app-sync
app-sync:
	@uv sync


.PHONY: app-build
app-build:
	@${D} build -t ${IMAGE} .

.PHONY: app-push
app-push:
	@${D} push ${IMAGE}

.PHONY: app-rebuild-push
app-rebuild-push:
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

# .PHONY: test
# test:  #  Запускает тесты только в папке tests
# 	@cd backend && uv run pytest tests && cd ..
#
# .PHONY: size
# size:
# 	@${D} system df
#
