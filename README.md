В settings.py (или prod.py) указан STATIC_ROOT = BASE_DIR / 'static'.

```shell
docker compose -f docker-compose.local.yml exec web python manage.py collectstatic
docker compose -f docker-compose.local.yml exec web python manage.py migrate
docker compose -f docker-compose.local.yml exec web python manage.py createsuperuser
```

```shell
# create app
cd src
../manage.py startapp <appname>
```

```shell
# makemigrations
cd src
../manage.py makemigrations <appname>
../manage.py migrate
```






```python
python manage.py dumpdata checklists --indent 4 --output fixtures/all_checklists_data_$(date +'%Y-%m-%d_%H:%M:%S').json
```

Django фикстуры используются для загрузки и выгрузки данных из базы данных
в форматах JSON, XML или YAML. Вот как можно создать фикстуру и загрузить её обратно:

### Снятие фикстур

Чтобы снять фикстуру (экспортировать данные из базы данных в файл фикстуры),
используйте команду `dumpdata`:

```bash
python manage.py dumpdata <app_label> --output <fixture_name>.json
```

- `<app_label>`: Укажите имя приложения, данные которого вы хотите экспортировать.
- `<fixture_name>`: Укажите имя файла, в который будут сохранены данные (например, `data.json`).

Пример:

```bash
python manage.py dumpdata myapp --output data.json
```

Это создаст файл `data.json` в текущей директории с данными из приложения `myapp`.
--indent 4
python manage.py dumpdata catalog --indent 4 --output fixtures/"catalog_$(date +'%Y-%m-%d_%H:%M')
.json"

python manage.py dumpdata myapp --output "data_$(date +'%Y%m%d_%H%M%S').json"
Объяснение
date +'%Y%m%d_%H%M%S': Команда date форматирует текущую дату и время. Формат '%Y%m%d_%H%M%S'
означает:
%Y: Год (4 цифры)
%m: Месяц (2 цифры)
%d: День (2 цифры)
%H: Час (24-часовой формат, 2 цифры)
%M: Минуты (2 цифры)
%S: Секунды (2 цифры)
$(...): Подстановка команд, которая выполняет команду внутри скобок и возвращает результат. В данном
случае, это подстановка текущей даты и времени.

### Запись фикстур

Чтобы загрузить данные из фикстуры обратно в базу данных, используйте команду `loaddata`:

```bash
python manage.py loaddata <fixture_name>.json
```

Пример:

```bash
python manage.py loaddata data.json
```

### Полный процесс

1. **Снимите фикстуру**:

    ```bash
    python manage.py dumpdata myapp --output data.json
    ```

2. **(Опционально) Очистите базу данных**:

   Если вы хотите сначала очистить базу данных перед загрузкой данных из фикстуры,
3. можно использовать команду `flush`. Эта команда удаляет все данные из базы данных,
4. но не затрагивает структуру таблиц.

    ```bash
    python manage.py flush
    ```

   **Внимание**: Это удалит все данные из базы данных. Используйте с осторожностью.

3. **Загрузите фикстуру**:

    ```bash
    python manage.py loaddata data.json
    ```

### Примечания

- Убедитесь, что у вас есть резервная копия базы данных перед выполнением операций, которые могут
  привести к потере данных, таких как `flush`.
- Фикстуры могут содержать только данные, но не структуры таблиц. Поэтому команды `makemigrations`
  и `migrate` нужно выполнять отдельно для создания или обновления структуры таблиц.
- Фикстуры полезны для тестирования, заполнения базы данных начальными данными или переноса данных
  между различными средами (например, из разработки в тестирование или на производство).

Следуя этим шагам, вы сможете снять фикстуры с вашего приложения и снова записать их в базу данных
при необходимости.

Название баз данных в настройках Django: “default” и “psql”

Для работы с конкретной базой данных, вам нужно указать её с помощью флага --database

Экспорт данных из базы данных default (SQLite):
python manage.py dumpdata --database=default > fixtures/default_fixtures.json
ИЛИ
python manage.py dumpdata --database=default > fixtures/default_$(date +'%Y-%m-%d_%H:%M:%S').json

Экспорт данных из базы данных psql (PostgreSQL):
python manage.py dumpdata --database=psql > fixtures/psql_fixtures.json
ИЛИ
python manage.py dumpdata --database=psql >
fixtures/psql_$(date +'%Y-%m-%d_%H:%M:%S').json

Импорт данных в чистые базы данных
Когда вам нужно загрузить данные в другую базу данных (например, в пустую БД на другом сервере), вы
также будете использовать команду loaddata. Указав, в какую базу данных нужно загрузить данные, с
помощью флага --database.

Если вы хотите сначала очистить базу данных перед загрузкой данных из фикстуры, можно использовать
команду `flush`. Эта команда удаляет все данные из базы данных, но не затрагивает структуру таблиц.

python manage.py flush --database=default # для базы default
python manage.py flush --database=psql # для базы psql

Загрузка данных в базу default (SQLite):
python manage.py loaddata default_fixtures.json --database=default

Загрузка данных в базу psql (PostgreSQL):
python manage.py loaddata psql_fixtures.json --database=psql
