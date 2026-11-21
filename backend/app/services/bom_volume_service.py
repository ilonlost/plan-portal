"""BOM volumes are already expanded by the upstream procedure; never multiply levels."""
from decimal import Decimal, InvalidOperation, ROUND_CEILING
from urllib.parse import quote

import httpx

from app.core.config import settings


def unit_weight(product) -> Decimal | None:
    if product is None:
        return None
    weight = Decimal(product.unit_weight_kg or 0)
    if weight <= 0 and product.box_weight_kg and product.units_per_box:
        weight = Decimal(product.box_weight_kg) / Decimal(product.units_per_box)
    return weight if weight.is_finite() and weight > 0 else None


def gp_units(product, quantity_kg: Decimal) -> Decimal:
    weight = unit_weight(product)
    if weight is None:
        raise ValueError(f"ГП {product.sku}: укажите вес штуки или вес и количество штук в коробе для расчёта BOM")
    return (quantity_kg / weight).to_integral_value(rounding=ROUND_CEILING)


def fetch_bom_volume(sku: str, volume: Decimal) -> list[dict]:
    url = f"{settings.bom_volume_api_base_url.rstrip('/')}/{quote(sku, safe='')}"
    try:
        response = httpx.get(url, params={"volume": str(volume)}, timeout=settings.bom_timeout_seconds)
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise ValueError("Не удалось получить расчёт ПФ из BOM. Повторите расчёт или создайте только ГП, сняв галочку «Раскручивать ПФ»") from exc
    if not isinstance(payload, dict) or str(payload.get("bom_item")) != sku or not isinstance(payload.get("items"), list):
        raise ValueError("BOM вернул некорректный ответ для выбранного ГП")
    return payload["items"]


def component_volumes(items: list[dict], products: dict) -> dict[str, dict]:
    totals: dict[str, dict[str, Decimal]] = {sku: {} for sku in products}
    for row in items:
        if not isinstance(row, dict):
            raise ValueError("BOM вернул некорректную строку состава")
        sku = str(row.get("body_nr", "")).strip()
        if sku not in totals:
            continue
        unit = str(row.get("ek_me") or "").strip().lower().rstrip(".")
        try:
            quantity = Decimal(str(row.get("comp_qnt")))
        except InvalidOperation as exc:
            raise ValueError(f"ПФ {sku}: BOM вернул некорректное количество") from exc
        if not quantity.is_finite() or quantity < 0:
            raise ValueError(f"ПФ {sku}: BOM вернул некорректное количество")
        if unit not in {"шт", "кг"}:
            raise ValueError(f"ПФ {sku}: единица BOM «{unit}» не поддерживается, нужны шт или кг")
        totals[sku][unit] = totals[sku].get(unit, Decimal(0)) + quantity
    result = {}
    for sku, amounts in totals.items():
        if not amounts:
            raise ValueError(f"ПФ {sku} из связей ГП → ПФ отсутствует в расчёте BOM")
        if len(amounts) != 1:
            raise ValueError(f"ПФ {sku}: BOM вернул разные единицы для одного артикула")
        unit, amount = next(iter(amounts.items()))
        if amount <= 0:
            raise ValueError(f"ПФ {sku}: в BOM нет положительной потребности")
        weight = unit_weight(products[sku])
        if unit == "шт" and weight is None:
            raise ValueError(f"ПФ {sku}: BOM вернул штуки; укажите вес штуки в справочнике для перевода в кг")
        result[sku] = {"quantity_kg": amount * weight if unit == "шт" else amount,
                       "quantity_units": amount if unit == "шт" else amount / weight if weight else None,
                       "source_quantity": amount, "source_unit": unit}
    return result


def calculate_components(product, quantity_kg: Decimal, products: dict) -> tuple[Decimal, dict]:
    volume = gp_units(product, quantity_kg)
    return volume, component_volumes(fetch_bom_volume(product.sku, volume), products)
