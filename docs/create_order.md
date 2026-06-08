```shell
docker exec -it web bash
python manage.py shell
```

################################################################################

from checklists.models import Inspection, ChecklistTemplate
from checklists.tasks import task_calculate_score
from checklists.utils import create_inspection_from_template  # если такая функция есть
from checklists.models import ChecklistCriteria, InspectionItem
from django.contrib.auth import get_user_model
from django.utils import timezone
from datetime import datetime

User = get_user_model()

# Смотрим инспектора
inspector = User.objects.get(id=49)
print(inspector)

# Смотрим доступные шаблоны
ChecklistTemplate.objects.values('id', 'name')


################################################################################

inspector = User.objects.get(id=49)
template = ChecklistTemplate.objects.get(id=3)

# Создаём отчёт
inspection = Inspection.objects.create(
    inspector=inspector,
    template=template,
    date_check=datetime(2026, 6, 5).date(),
    completed_at=timezone.make_aware(datetime(2026, 6, 5, 12, 0)),
    location_snapshot=template.location.name,
    is_completed=True,
)
print(f"Создан отчёт id={inspection.id}")

# Получаем все критерии шаблона через секции
criteria = ChecklistCriteria.objects.filter(section__template=template)

for c in criteria:
    InspectionItem.objects.create(
        inspection=inspection,
        criteria_origin=c,
        section_name=c.section.title,
        criteria_text=c.text,
        criteria_order=c.order,
        section_type=c.section.section_type,
        is_compliant=True,
    )

print(f"Создано пунктов: {InspectionItem.objects.filter(inspection=inspection).count()}")

task_calculate_score.delay(inspection.id)
print(f"Задача отправлена для отчёта id={inspection.id}")

inspection.refresh_from_db()
print(f"Итоговый балл: {inspection.final_score}")
