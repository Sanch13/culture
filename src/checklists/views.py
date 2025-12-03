from django.shortcuts import render, get_object_or_404
from django.contrib.admin.views.decorators import staff_member_required
from checklists.models import ChecklistTemplate


# Декоратор проверяет, что пользователь имеет статус "Staff" (Администратор)
@staff_member_required
def admin_cabinet(request):
    """
    Кабинет администратора.
    Здесь выводится список шаблонов с кнопками управления (Редактировать, Предпросмотр).
    В будущем сюда добавим аналитику и графики.
    """
    templates = ChecklistTemplate.objects.all().select_related("location")

    context = {"templates": templates}
    return render(request, "checklists/admin_cabinet.html", context)


@staff_member_required
def template_preview(request, template_id):
    """
    Предварительный просмотр чек-листа.
    Мы НЕ создаем запись Inspection в базе.
    Мы просто берем структуру (Шаблон -> Разделы -> Вопросы) и рисуем её.
    """
    template = get_object_or_404(ChecklistTemplate, pk=template_id)

    # Получаем разделы и сразу подгружаем связанные критерии (prefetch_related),
    # чтобы не делать 100 запросов к базе.
    sections = template.sections.all().order_by("order").prefetch_related("criteria")

    context = {
        "template": template,
        "sections": sections,
    }
    return render(request, "checklists/template_preview.html", context)
