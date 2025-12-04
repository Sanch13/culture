from django.shortcuts import render, get_object_or_404
from django.contrib.admin.views.decorators import staff_member_required
from checklists.models import ChecklistTemplate


@staff_member_required
def admin_dashboard(request):
    """
    Главная страница (Дашборд).
    Сюда мы позже выведем графики и цифры.
    """
    # Пока просто считаем общее количество, чтобы было не пусто
    total_templates = ChecklistTemplate.objects.count()
    # total_inspections = Inspection.objects.count() # Раскомментируем, когда будут проверки

    context = {
        "total_templates": total_templates,
        # 'total_inspections': total_inspections
    }
    return render(request, "checklists/admin_dashboard.html", context)


@staff_member_required
def admin_templates(request):
    """
    Отдельная страница: Библиотека шаблонов.
    """
    templates = ChecklistTemplate.objects.all().select_related("location")

    context = {"templates": templates}
    return render(request, "checklists/admin_templates.html", context)


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
