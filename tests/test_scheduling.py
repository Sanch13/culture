from checklists.services.services import _determine_start_index


def test_determine_start_index_normal(inspectors):
    """Обычный случай: предыдущий был ID=2, значит следующий должен быть ID=3 (индекс 2)"""
    # inspectors = [ID:1, ID:2, ID:3, ID:4, ID:5]
    last_user_id = 2
    index = _determine_start_index(last_user_id, inspectors)
    assert index == 2  # Индекс 2 соответствует ID=3


def test_determine_start_index_deleted_middle(inspectors):
    """Крайний случай 1: Пользователь ID=3 уволен. Был последним ID=2"""
    # Удаляем ID=3
    inspectors.pop(2)
    # Теперь список: [ID:1, ID:2, ID:4, ID:5]
    # Если last_user был ID=3 (уволен), алгоритм должен взять следующего выжившего, то есть ID=4 (индекс 2)
    last_user_id = 3
    index = _determine_start_index(last_user_id, inspectors)
    assert index == 2
    assert inspectors[index].id == 4


def test_determine_start_index_deleted_last(inspectors):
    """Крайний случай 2: Уволен самый последний в списке (ID=5)"""
    inspectors.pop(4)
    # Список: [ID:1, ID:2, ID:3, ID:4]
    # Если last_user был 5, алгоритм должен вернуться в начало (индекс 0)
    last_user_id = 5
    index = _determine_start_index(last_user_id, inspectors)
    assert index == 0
    assert inspectors[index].id == 1


def test_determine_start_index_zero():
    """Случай самого первого запуска"""
    index = _determine_start_index(0, [])
    assert index == 0
