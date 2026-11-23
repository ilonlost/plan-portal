from decimal import Decimal
from sqlalchemy import select
from test_audit_regressions import db, client
from test_pf_expansion import setup_gp, bom_for_existing_chain_tests
from app.models.entities import LineCapability
from app.services.pf_service import preview_expansion


def test_catalog_pf_line_and_speed_feed_expansion(client, db):
    _, values, line = setup_gp(db)
    created = client.post('/catalog/products', json={
        'sku': '4510000047', 'product_name': 'ПФ из справочника', 'state': 'ПФ',
        'line_id': line.id, 'units_per_hour': 75.5,
    })
    assert created.status_code == 200, created.text
    ids = created.json()
    assert preview_expansion(db, values)['items'][0]['speed_kg_hour'] == Decimal('75.5')
    updated = client.patch(f"/catalog/products/{ids['product_id']}", json={
        'capability_id': ids['capability_id'], 'line_id': line.id, 'units_per_hour': 80,
    })
    assert updated.status_code == 200, updated.text
    assert preview_expansion(db, values)['items'][0]['speed_kg_hour'] == Decimal(80)
    assert len(list(db.scalars(select(LineCapability).where(LineCapability.product_id == ids['product_id'])))) == 1
