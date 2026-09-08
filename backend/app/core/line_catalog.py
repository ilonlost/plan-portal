from __future__ import annotations


# Canonical production lines from the FK capacity workbook.  CSB codes are
# maintained here so seed/init logic and imports use one authoritative list.
LINE_DEFINITIONS = (
    ("PC", "ПЦ", "Булка", "5810"),
    ("PC", "ПЦ", "Слойка", "5800"),
    ("PC", "ПЦ", "Хлеба", "5820"),
    ("PC", "ПЦ", "Ручная зона ПЦ", "5830"),
    ("PC", "ПЦ", "Сухари", "5850"),
    ("PC", "ПЦ", "НАЧИНКА", "5600"),
    ("KC", "КЦ", "Сэндвичи", "5480"),
    ("KC", "КЦ", "Жареные блюда", "5410"),
    ("KC", "КЦ", "Лазанья", "5440"),
    ("KC", "КЦ", "Миквак", "5400"),
    ("KC", "КЦ", "Бургеры", "5460"),
    ("KC", "КЦ", "Супы", "5430"),
    ("KC", "КЦ", "Салаты", "5420"),
    ("KC", "КЦ", "Напитки", "5470"),
    ("KC", "КЦ", "Ручная зона КЦ", "5410"),
)


def line_key(value: str) -> str:
    return " ".join(value.lower().replace("ё", "е").replace("линия ", "").split())


LINE_BY_KEY = {line_key(name): (workshop_code, workshop_name, name, csb_code) for workshop_code, workshop_name, name, csb_code in LINE_DEFINITIONS}


def workshop_for_line(line_name: str) -> tuple[str, str]:
    normalized = line_key(line_name)
    exact = LINE_BY_KEY.get(normalized)
    if exact:
        return exact[0], exact[1]
    for key, (code, name, _, _) in LINE_BY_KEY.items():
        if key in normalized:
            return code, name
    return "UNASSIGNED", "Не распределено"
