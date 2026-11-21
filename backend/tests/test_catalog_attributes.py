from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from app.core.config import settings
from app.services import catalog_attributes as service
from tests.test_audit_regressions import client, db


@pytest.fixture(autouse=True)
def configure(monkeypatch):
    monkeypatch.setattr(settings, "catalog_attributes_database_url", "mssql+pymssql://user:secret@server/CSB_FK_REP")
    monkeypatch.setattr(settings, "catalog_mercury_sku_column", "Art_NR")
    monkeypatch.setattr(settings, "catalog_chz_sku_column", "Art_NR")
    monkeypatch.setattr(settings, "production_fact_db_schema", "dbo")


def test_both_flags_duplicates_missing_details_and_source_refresh(monkeypatch):
    read = MagicMock(side_effect=[
        [{"sku": "1010011409"}, {"sku": "1010011409"}], [{"sku": "1010011409"}],
        [{"sku": "1010011409", "shelf_life": Decimal("14"), "unit": " ШТ "}],
        [], [], [],
    ])
    monkeypatch.setattr(service, "read_rows", read)
    result = service.load_attributes(["1010011409", " 1010011409 ", "0002"])
    assert result["values"]["1010011409"] == {"mercury": True, "chz": True, "shelf_life": "14", "unit": "ШТ"}
    assert result["values"]["0002"] == {"mercury": False, "chz": False, "shelf_life": None, "unit": None}
    assert "[Art_NR]" in read.call_args_list[0].args[0]
    assert read.call_args_list[0].args[1] == ["1010011409", "0002"]
    assert service.load_attributes(["1010011409"])["values"]["1010011409"]["mercury"] is False


def test_partial_failure_is_unknown_not_false_and_hides_credentials(monkeypatch):
    monkeypatch.setattr(service, "read_rows", MagicMock(side_effect=[RuntimeError("secret"), [], [
        {"sku": "1", "shelf_life": 0, "unit": "КГ"},
        {"sku": "1", "shelf_life": 0, "unit": "ШТ"},
    ]]))
    result = service.load_attributes(["1"])
    assert result["sources"] == {"mercury": "unavailable", "chz": "ok", "details": "ok"}
    assert result["values"]["1"] == {"mercury": None, "chz": False, "shelf_life": "0", "unit": None, "unit_conflict": True}
    assert "secret" not in str(result)


def test_no_configuration_does_not_query(monkeypatch):
    monkeypatch.setattr(settings, "catalog_attributes_database_url", "")
    monkeypatch.setattr(settings, "production_fact_database_url", "")
    read = MagicMock()
    monkeypatch.setattr(service, "read_rows", read)
    result = service.load_attributes(["1"])
    assert set(result["sources"].values()) == {"not_configured"}
    assert all(value is None for value in result["values"]["1"].values())
    read.assert_not_called()


def test_parameter_binding_batching_fallback_and_disposal(monkeypatch):
    monkeypatch.setattr(settings, "catalog_attributes_database_url", "")
    monkeypatch.setattr(settings, "production_fact_database_url", "mssql+pymssql://u:p@host/db")
    engine = MagicMock()
    conn = engine.connect.return_value.__enter__.return_value
    conn.execute.return_value.mappings.return_value = [{"sku": "0001"}]
    create = MagicMock(return_value=engine)
    monkeypatch.setattr(service, "create_engine", create)
    skus = ["'; DROP TABLE x--"] + [str(i) for i in range(500)]
    service.read_rows("SELECT sku FROM source WHERE sku IN :skus", skus)
    assert create.call_args.args[0] == settings.production_fact_database_url
    assert conn.execute.call_count == 2
    assert len(conn.execute.call_args_list[0].args[1]["skus"]) == 500
    assert skus[0] not in str(conn.execute.call_args_list[0].args[0])
    engine.dispose.assert_called_once()
    conn.execute.side_effect = RuntimeError("failure")
    with pytest.raises(RuntimeError):
        service.read_rows("SELECT sku FROM source WHERE sku IN :skus", ["1"])
    assert engine.dispose.call_count == 2


def test_invalid_identifier_is_not_executed(monkeypatch):
    monkeypatch.setattr(settings, "catalog_mercury_sku_column", "Art_NR]; DROP TABLE x--")
    read = MagicMock(return_value=[])
    monkeypatch.setattr(service, "read_rows", read)
    assert service.load_attributes(["1"])["sources"]["mercury"] == "unavailable"
    assert read.call_count == 2


def test_endpoint_validation_and_authentication(client, db, monkeypatch):
    monkeypatch.setattr(service, "read_rows", MagicMock(return_value=[]))
    result = client.post("/catalog/source-attributes", json={"skus": ["1010011409"]})
    assert result.status_code == 200
    assert result.json()["values"]["1010011409"]["mercury"] is False
    assert client.post("/catalog/source-attributes", json={"skus": ["x"] * 1001}).status_code == 422
    assert client.post("/catalog/source-attributes", json={"skus": ["x" * 81]}).status_code == 422
    client.cookies.clear()
    assert client.post("/catalog/source-attributes", json={"skus": ["1"]}).status_code == 401
