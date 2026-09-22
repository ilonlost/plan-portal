from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_CEILING


DESTINATION_CODES = {
    "ДМД": "8030000024",
    "DMD": "8030000024",
    "СВР": "8030000025",
    "SVR": "8030000025",
    "СПБ": "8030000038",
    "SPB": "8030000038",
    "СГП ДЖ": "7099",
    "СГП ДЗ": "7099",
    "SGP DZ": "7099",
}


def _integer(value) -> str:
    try:
        number = Decimal(str(value).strip().replace(",", "."))
    except (InvalidOperation, ValueError):
        raise ValueError("Количество и время в файле CSB должны быть числами") from None
    if not number.is_finite() or number < 0:
        raise ValueError("Количество и время в файле CSB должны быть неотрицательными числами")
    return format(number.to_integral_value(rounding=ROUND_CEILING), "f")


def build_csb_text(items: list, destination: str = "ДМД") -> tuple[str, list[int]]:
    """Build the DT0133 records produced by the legacy Excel ExportCSB macro."""
    destination_code = DESTINATION_CODES.get(destination.strip().upper(), destination.strip())
    lines: list[str] = []
    exported_ids: list[int] = []
    for item in items:
        if item.schedule_kind != "production" or item.excluded or not item.product or not item.line:
            continue
        quantity = item.source_quantity if item.source_unit == "шт" and item.source_quantity else None
        if quantity is None:
            weight = Decimal(item.product.unit_weight_kg or 0)
            if weight > 0:
                quantity = Decimal(item.quantity_kg or item.quantity or 0) / weight
            else:
                quantity = Decimal(item.quantity_kg or item.quantity or 0)
        if not quantity or Decimal(quantity) <= 0 or not item.line.csb_line_code:
            continue
        marking_date = item.marking_date or item.production_date
        production_date = item.production_date
        if not marking_date or not production_date:
            continue
        shift_prefix = "2" if item.shift == "night" else "1"
        sequence = f"{int(item.sequence or 0):03d}"
        fields = [
            f"L1+{_integer(quantity)}",
            f"T1+{item.line.csb_line_code}",
            f"T2+{marking_date.strftime('%Y%m%d')}",
            f"T4+{item.product.sku}",
            f"T5+{item.line.csb_t5 or '4'}",
            f"T34+{destination_code}",
            f"T3+{shift_prefix}{sequence}",
            f"T55+{_integer((item.line.csb_t55 or '').strip() or item.required_hours)}",
            f"L8+{production_date.strftime('%Y%m%d')}",
        ]
        lines.append("DT0133+PROD-ORDER:" + ":".join(fields))
        exported_ids.append(item.id)
    return "\r\n".join(lines) + ("\r\n" if lines else ""), exported_ids
