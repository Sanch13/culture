import random
from django.contrib.auth import get_user_model

User = get_user_model()
PASSWORD = ""

# Списки имен и фамилий
first_names = [
    "Александр", "Сергей", "Дмитрий", "Андрей", "Алексей", "Максим", "Евгений", "Владимир",
    "Иван", "Михаил", "Кирилл", "Николай", "Егор", "Илья", "Артем", "Руслан", "Олег",
    "Вадим", "Юрий", "Никита", "Павел", "Виктор", "Игорь", "Роман", "Денис"
]

last_names = [
    "Смирнов", "Иванов", "Кузнецов", "Попов", "Соколов", "Лебедев", "Козлов", "Новиков",
    "Морозов", "Петров", "Волков", "Соловьев", "Васильев", "Зайцев", "Павлов", "Семенов",
    "Голубев", "Виноградов", "Богданов", "Воробьев", "Федоров", "Михайлов", "Беляев",
    "Тарасов", "Белов", "Комаров", "Орлов", "Киселев", "Макаров", "Андреев"
]

# Простая функция транслитерации для email
def translit(text):
    mapping = {
        'а': 'a', 'б': 'b', 'в': 'v', 'г': 'g', 'д': 'd', 'е': 'e', 'ё': 'yo', 'ж': 'zh',
        'з': 'z', 'и': 'i', 'й': 'y', 'к': 'k', 'л': 'l', 'м': 'm', 'н': 'n', 'о': 'o',
        'п': 'p', 'р': 'r', 'с': 's', 'т': 't', 'у': 'u', 'ф': 'f', 'х': 'h', 'ц': 'ts',
        'ч': 'ch', 'ш': 'sh', 'щ': 'sch', 'ъ': '', 'ы': 'y', 'ь': '', 'э': 'e', 'ю': 'yu',
        'я': 'ya'
    }
    return ''.join([mapping.get(char.lower(), '') for char in text])

print("Начинаю создание пользователей...")

count = 0
for i in range(1, 51): # Создаем 50 человек
    fn = random.choice(first_names)
    ln = random.choice(last_names)

    # Добавляем цифру i, чтобы email точно был уникальным (ivanov.ivan5@...)
    email = f"{translit(ln)}.{translit(fn)}{i}@example.com"

    if not User.objects.filter(email=email).exists():
        User.objects.create_user(
            email=email,
            password=PASSWORD,
            first_name=fn,
            last_name=ln,
            role='worker',
            is_active=True,
            can_perform_inspections=True # ВАЖНО: Разрешаем проверки
        )
        count += 1
        print(f"[{i}/50] Создан: {ln} {fn} ({email})")

print(f"\nГотово! Всего добавлено: {count} пользователей.")
