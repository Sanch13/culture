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
