from __future__ import annotations

import re


def mono_group(product_name: str, explicit: str | None = None) -> str:
    """Return a stable setup group; equal groups can run without an intermediate wash."""
    if explicit and explicit.strip():
        return explicit.strip()
    value = product_name.lower().replace("ё", "е")
    if "бул" in value and "бург" in value:
        return "Булочка для бургера"
    if "бриош" in value:
        return "Булочка бриошь"
    if ("хот" in value and "дог" in value) or "хот-дог" in value:
        return "Булочка хот-дог"
    value = re.sub(r"\([^)]*(?:кг|гр?|шт|сут)[^)]*\)", " ", value)
    value = re.sub(r"\b\d+(?:[.,]\d+)?\s*(?:кг|гр?|шт|сут(?:ок)?|дн(?:ей|я)?)\b", " ", value)
    value = re.sub(r"\b\d+\s*[xх*]\s*\d+\b", " ", value)
    value = re.sub(r"\b(?:fp|охл|зам)\b", " ", value)
    value = re.sub(r"[^а-яa-z]+", " ", value)
    return " ".join(value.split())[:160] or product_name[:160]
