# makefile
DC = docker compose
EXEC = docker exec -it
LOGS = docker logs
ENV = --env-file .env
LOCAL_FILE = docker-compose.local.yml
STORAGES_FILE = docker_compose/storages.yaml
APP_CONTAINER = app
SERVICE_NAME = fastapi_app

.PHONY: app-logs
app-logs:
	@${DC} -f ${LOCAL_FILE} up --build

.PHONY: app-logs-down
app-logs-down:
	@${DC} -f ${LOCAL_FILE} down
# .PHONY: app-local-down
# app-local-down:
# 	@${DC} -f ${APP_FILE_LOCAL} down
#
# .PHONY: app
# app:
# 	@${DC} -f ${APP_FILE} up --build -d
#
# .PHONY: app-down
# app-down:
# 	@${DC} -f ${APP_FILE} down
#
# .PHONY: build
# build:
# 	@${D} build -t ${IMAGE_NAME} .
#
# .PHONY: app-del
# app-del:
# 	@${D} rmi ${IMAGE_NAME}
#
# .PHONY: push
# push:
# 	@${D} push ${IMAGE_NAME}
#
# .PHONY: size
# size:
# 	@${D} system df
#
# .PHONY: cash
# cash:
# 	@${D} system prune -f
#
#
# .PHONY: app-sync
# app-sync:  #
# 	@cd backend && uv sync && cd ..
#
# .PHONY: app-logs
# app-logs:  # запускает приложение с логами в консоли
# 	@$(MAKE) app-sync
# 	@${DC} -f ${LOCAL_FILE} up --build
#
# .PHONY: app
# app:  # запускает приложение и применяет все миграции
# 	@${DC} -f ${LOCAL_FILE} up --build -d
# 	@$(MAKE) migrate-up
#
# .PHONY: app-down
# app-down:
# 	@${DC} -f ${LOCAL_FILE} down
#
# # Создать миграцию
# .PHONY: migrate # make migrate m="add users table"
# migrate:
# 	@${DC} -f ${LOCAL_FILE} exec ${SERVICE_NAME} alembic revision --autogenerate -m "$(m)"
#
# # Применить миграции
# .PHONY: migrate-up  # make migrate-up
# migrate-up:
# 	@${DC} -f ${LOCAL_FILE} exec ${SERVICE_NAME} alembic upgrade head
#
# # Откатить миграцию
# .PHONY: migrate-down  # make migrate-down
# migrate-down:
# 	@${DC} -f ${LOCAL_FILE} exec ${SERVICE_NAME} alembic downgrade -1
#
# .PHONY: test
# test:  #  Запускает тесты только в папке tests
# 	@cd backend && uv run pytest tests && cd ..
