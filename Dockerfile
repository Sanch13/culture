# ==============================================================================
# Стадия 1: Строитель (Builder)
# Здесь мы устанавливаем "тяжелые" зависимости для сборки
# ==============================================================================
FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim AS builder

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libvips-dev \
    libheif-dev \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

ENV UV_COMPILE_BYTECODE=1

WORKDIR /app

COPY pyproject.toml uv.lock /app/

RUN --mount=type=cache,target=/root/.cache/uv \
    UV_HTTP_TIMEOUT=60 \
    uv sync --frozen --no-dev --no-install-project

COPY . .

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

# ==============================================================================
# Стадия 2: Финальный образ
# Здесь мы собираем легкий образ для запуска приложения
# ==============================================================================
FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim

# Установка системных библиотек ДЛЯ РАБОТЫ приложения
# Здесь ставим обычные пакеты (без -dev), чтобы не тянуть в прод компиляторы
RUN apt-get update && apt-get install -y --no-install-recommends \
    libvips42 \
    libvips-tools \
    libheif1 \
    libpq5 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Копируем готовое виртуальное окружение из стадии "Строителя"
COPY --from=builder /app /app

RUN groupadd -g 1000 miran && \
    useradd -u 1000 -g 1000 -m miran && \
    chown -R miran:miran /app

USER miran

ENV PATH="/app/.venv/bin:$PATH"
