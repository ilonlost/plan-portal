from datetime import date, datetime
from unittest.mock import MagicMock

import pytest

from app.core.config import settings
from app.services import tail_buffer_service as service
from tests.test_audit_regressions import client, db


def test_months_cross_year_and_cover_both_boundaries():
    assert service.month_tables(date(2026, 12, 31), date(2027, 2, 1)) == [
        "cp_DWH_LA0052_202612", "cp_DWH_LA0052_202701", "cp_DWH_LA0052_202702"]


def test_query_preserves_direction_and_first_record_order_without_join_multiplication():
    query = service.build_query("dbo", ["cp_DWH_LA0052_202609"])
    assert "IIF(L52_BW_TYP IN (2,4), L52_KST_NR_1, L52_KST_NR_2) AS source_center" in query
    assert "IIF(L52_BW_TYP IN (1,3), L52_KST_NR_1, L52_KST_NR_2) AS target_buffer" in query
    assert "ORDER BY m.L52_REC_NR ASC" in query
    assert "GROUP BY s.SY8581_NVE" in query
    assert "COUNT(DISTINCT s.SY8581_ART_NR)" in query
    assert "moved_at < :end_at" in query
    with pytest.raises(ValueError):
        service.build_query("dbo; DROP TABLE users", ["cp_DWH_LA0052_202609"])


def test_not_configured_has_all_twelve_centers_without_fake_rows(monkeypatch):
    monkeypatch.setattr(settings, "production_fact_database_url", "")
    result = service.load_tail_buffers(date(2026, 9, 1), date(2026, 9, 30))
    assert result["status"] == "not_configured"
    assert len(result["centers"]) == 12
    assert result["buffers"] == {"KC": "5498", "PC": "5898"}
    assert result["rows"] == []


def test_partial_months_ambiguous_sscc_and_limit_are_explicit(monkeypatch):
    engine = MagicMock()
    connection = engine.connect.return_value.__enter__.return_value
    present = MagicMock(); present.scalar.return_value = 1
    missing = MagicMock(); missing.scalar.return_value = None
    records = MagicMock()
    records.mappings.return_value.all.return_value = [{
        "source_month": "202609", "record_id": 7, "sscc": "00123456789012345678",
        "source_center": 5410, "target_buffer": 5498, "moved_at": datetime(2026, 9, 20, 8),
        "first_moved_at": datetime(2026, 9, 1, 1), "sku": "101001", "article_count": 2,
        "product_name": "Conflicting article", "created_date": date(2026, 8, 31), "card_buffer": 5498,
        "buffer_name": "Buffer", "total_count": 5001,
    }]
    connection.execute.side_effect = [missing, present, records]
    monkeypatch.setattr(settings, "production_fact_database_url", "mssql+pymssql://user:secret@server/db")
    monkeypatch.setattr(service, "create_engine", MagicMock(return_value=engine))
    result = service.load_tail_buffers(date(2026, 8, 31), date(2026, 9, 20))
    assert result["status"] == "partial"
    assert result["truncated"] and result["total_count"] == 5001
    assert "202608" in result["issues"][0]
    row = result["rows"][0]
    assert row["workshop_code"] == "KC" and row["line_name"] == "Жареные блюда"
    assert row["sku"] is None and row["warning"]
    assert row["sscc"].startswith("00")
    assert row["moved_at"].utcoffset().total_seconds() == 10800
    args = connection.execute.call_args.args[1]
    assert args["buffer_kc"] == 5498 and args["buffer_pc"] == 5898
    assert args["end_at"] == datetime(2026, 9, 21)
    engine.dispose.assert_called_once()


def test_connection_errors_do_not_expose_credentials(monkeypatch):
    monkeypatch.setattr(settings, "production_fact_database_url", "mssql+pymssql://user:secret@server/db")
    monkeypatch.setattr(service, "create_engine", MagicMock(side_effect=RuntimeError("secret")))
    result = service.load_tail_buffers(date(2026, 9, 20), date(2026, 9, 20))
    assert result["status"] == "unavailable"
    assert "secret" not in str(result)


def test_endpoint_range_and_section_access(client, db, monkeypatch):
    from app.models.entities import User
    from sqlalchemy import select
    monkeypatch.setattr(settings, "production_fact_database_url", "")
    response = client.get("/production-fact/tail-buffers?start=2026-09-20&end=2026-09-21")
    assert response.status_code == 200 and len(response.json()["centers"]) == 12
    assert client.get("/production-fact/tail-buffers?start=2026-09-21&end=2026-09-20").status_code == 422
    assert client.get("/production-fact/tail-buffers?start=2026-01-01&end=2026-09-20").status_code == 422
    user = db.scalar(select(User).where(User.username == "regular.admin"))
    user.role = "viewer"; user.section_permissions = {"fact": False}; db.commit()
    assert client.get("/production-fact/tail-buffers").status_code == 403
