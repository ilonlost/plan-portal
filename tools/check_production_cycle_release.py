"""Run in the new backend image against production: all mutations roll back."""
import hashlib
import json
from collections import defaultdict
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import select, text
from sqlalchemy.orm import Session
from app.db.session import engine
from app.models.entities import ProductionLine, ProductionScheduleItem, DemandItem, LineCapability
from app.services.plan_service import PlanService, schedule_item_dict
from app.services.production_cycle_service import production_cycle
from app.api.routes import lines as routes
from app.core.security import UserContext


def fingerprint(connection):
    tables = ['production_schedule_items', 'products', 'demand_items', 'imported_orders',
              'production_plans', 'portal_settings', 'production_lines', 'line_capacities',
              'production_plan_versions', 'audit_events']
    return {table: connection.execute(text(f"SELECT count(*), md5(COALESCE(string_agg(row_to_json(t)::text, '' ORDER BY t.id), '')) FROM {table} t")).one() for table in tables}


def cards(plan, line_id):
    return {row.id: {column.name: str(getattr(row, column.name)) for column in ProductionScheduleItem.__table__.columns}
            for row in plan.schedule_items if row.line_id is not None and row.line_id != line_id}


def volumes(plan, line_id):
    total = defaultdict(Decimal)
    for row in plan.schedule_items:
        if row.line_id == line_id and row.schedule_kind == 'production' and not row.excluded:
            total[row.source_kind] += Decimal(row.quantity_kg or row.quantity)
    return dict(total)


routes.send_notification = lambda *args, **kwargs: None
with engine.connect() as connection:
    before_database = fingerprint(connection)
    connection.rollback()
    outer = connection.begin()
    db = Session(bind=connection, join_transaction_mode='create_savepoint', expire_on_commit=False)
    try:
        service = PlanService(db)
        plan = service.active_plan()
        line = db.scalar(select(ProductionLine).where(ProductionLine.name == 'Сэндвичи', ProductionLine.workshop_code == 'KC'))
        before_cards = cards(plan, line.id)
        before_volume = volumes(plan, line.id)
        old_revision = plan.revision
        applied = service.apply_production_cycle_upgrade()
        # Re-save through the same API implementation the web form uses.
        data = routes.line_schedule(line.id, start=datetime(2026, 10, 19).date(), db=db, user=UserContext('release-check', 'Release check', 'planner'))
        payload = routes.LineScheduleUpdate(**{**data, 'slots': [], 'production_cycle': {'enabled': True, 'start_time': '18:00', 'duration_hours': 24}, 'plan_version': plan.revision})
        routes.update_line_schedule(line.id, payload, db=db, user=UserContext('release-check', 'Release check', 'planner'))
        assert cards(plan, line.id) == before_cards, 'Other lines changed'
        full_volume = volumes(plan, line.id)
        assert full_volume == before_volume, (before_volume, full_volume)
        checked = 0
        for row in plan.schedule_items:
            if row.line_id != line.id or row.schedule_kind != 'production' or row.excluded or not row.production_date:
                continue
            serialized = schedule_item_dict(row)
            assert serialized['production_window_end'] == datetime.combine(row.production_date, datetime.min.time()).replace(hour=18)
            assert serialized['production_window_start'] == serialized['production_window_end'] - timedelta(days=1)
            if row.source == 'auto' and row.source_kind == 'ohl' and row.marking_date:
                assert row.production_date == row.marking_date
            checked += 1
        enabled_dates = {row.demand_item_id: row.production_date for row in plan.schedule_items if row.line_id == line.id and row.source == 'auto' and row.source_kind == 'ohl'}
        payload.plan_version = plan.revision
        payload.production_cycle = {'enabled': False, 'start_time': '18:00', 'duration_hours': 24}
        routes.update_line_schedule(line.id, payload, db=db, user=UserContext('release-check', 'Release check', 'planner'))
        assert cards(plan, line.id) == before_cards
        assert volumes(plan, line.id) == before_volume
        assert not production_cycle(line)['enabled']
        restored = 0
        for row in plan.schedule_items:
            if row.line_id == line.id and row.source == 'auto' and row.source_kind == 'ohl' and not row.excluded and row.production_date:
                assert row.production_date == row.demand_item.requested_date
                assert schedule_item_dict(row)['production_window_start'] is None
                restored += 1
        assert not service.apply_production_cycle_upgrade(), 'Upgrade repeated'
        assert not production_cycle(line)['enabled'], 'Restart re-enabled a disabled cycle'
        print(json.dumps({'revision_before': old_revision, 'upgrade_applied': applied, 'other_line_cards_preserved': len(before_cards), 'sandwich_volume_kg': {k: str(v) for k,v in before_volume.items()}, 'windows_verified': checked, 'legacy_cards_verified_after_disable': restored, 'disabled_survives_restart': True}, ensure_ascii=False))
    finally:
        db.close()
        outer.rollback()
    assert fingerprint(connection) == before_database, 'Rollback changed database'
    print('ROLLBACK_VERIFIED_ALL_DATA_UNCHANGED')
