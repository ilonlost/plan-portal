"""Live, read-only CSB attributes. Never persist source values in the catalog."""
import re
from collections import defaultdict
from datetime import datetime, timezone

from sqlalchemy import bindparam, create_engine, text

from app.core.config import settings


def identifier(value: str) -> str:
    if not re.fullmatch(r"[\w ]{1,128}", value):
        raise ValueError("Invalid SQL identifier")
    return f"[{value}]"


def read_rows(query: str, skus: list[str]) -> list[dict]:
    url = settings.catalog_attributes_database_url.strip() or settings.production_fact_database_url.strip()
    if not url.startswith("mssql+pymssql://"):
        raise ValueError("Invalid source URL")
    engine = create_engine(url, pool_pre_ping=True, connect_args={"login_timeout": 5, "timeout": 10})
    try:
        rows = []
        statement = text(query).bindparams(bindparam("skus", expanding=True))
        with engine.connect() as connection:
            for offset in range(0, len(skus), 500):
                rows.extend(dict(row) for row in connection.execute(statement, {"skus": skus[offset:offset + 500]}).mappings())
        return rows
    finally:
        engine.dispose()


def load_attributes(skus: list[str]) -> dict:
    skus = list(dict.fromkeys(sku.strip() for sku in skus if sku.strip()))
    values = {sku: {"mercury": None, "chz": None, "shelf_life": None, "unit": None} for sku in skus}
    sources = {}
    configured = bool(settings.catalog_attributes_database_url.strip() or settings.production_fact_database_url.strip())
    for field, table, column in (
        ("mercury", "mercury_nav", settings.catalog_mercury_sku_column.strip()),
        ("chz", "chz_nav", settings.catalog_chz_sku_column.strip()),
    ):
        if not configured or not column:
            sources[field] = "not_configured"
            continue
        try:
            key = f"LTRIM(RTRIM(CONVERT(nvarchar(80), {identifier(column)})))"
            rows = read_rows(f"SELECT DISTINCT {key} AS sku FROM [11-VM-CSBSQL].[CSB-EDI].[dbo].[{table}] WHERE {key} IN :skus", skus) if skus else []
            found = {str(row["sku"]).strip() for row in rows}
            for sku in skus:
                values[sku][field] = sku in found
            sources[field] = "ok"
        except Exception:
            # Do not expose driver messages, SQL or connection credentials.
            sources[field] = "unavailable"
    if not configured:
        sources["details"] = "not_configured"
    else:
        try:
            schema = identifier(settings.production_fact_db_schema)
            query = f"""SELECT DISTINCT LTRIM(RTRIM(CONVERT(nvarchar(80), SY0012_NR))) AS sku,
                SY0012_HBK_ZEIT AS shelf_life, SY0012_EK_ME AS unit
                FROM {schema}.[cp_DWH_SY0012_SY8212_SY9014_SY9118]
                WHERE LTRIM(RTRIM(CONVERT(nvarchar(80), SY0012_NR))) IN :skus"""
            rows = read_rows(query, skus) if skus else []
            grouped = defaultdict(lambda: defaultdict(set))
            for row in rows:
                sku = str(row["sku"]).strip()
                if sku not in values:
                    continue
                for field in ("shelf_life", "unit"):
                    value = row.get(field)
                    grouped[sku][field].add(str(value).strip() if value is not None and str(value).strip() else None)
            for sku, fields in grouped.items():
                for field, candidates in fields.items():
                    values[sku][field] = next(iter(candidates)) if len(candidates) == 1 else None
                    if len(candidates) > 1:
                        values[sku][f"{field}_conflict"] = True
            sources["details"] = "ok"
        except Exception:
            sources["details"] = "unavailable"
    return {"values": values, "sources": sources, "checked_at": datetime.now(timezone.utc).isoformat()}
