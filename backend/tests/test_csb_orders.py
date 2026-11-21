from datetime import date

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db.base import Base
from app.models.entities import ProductionPlan, ProductionScheduleItem
from app.services.csb_orders import bom_query, record_order_confirmation, verify_bom_reply


def test_bom_preflight():
    assert bom_query("1010023614", "4", "5420", "6.3") == 'DT0061+MAIN;?:T1+1010023614:T2+4:F810+"T14+,T17+"'
    verify_bom_reply(1, ":L2+0+0+0:", ["", "DT0061:T14+5420:T17+0"], "5420")
    for weight in ("0", "NaN", "5.1234567", "-2"):
        with pytest.raises(ValueError):
            bom_query("1010023614", "4", "5420", weight)


@pytest.mark.parametrize("transport,status,rows", [
    (0, ":L2+0+0+0:", ["T14+5420:T17+0"]),
    (1, ":L2+1+0+0:", ["T14+5420:T17+0"]),
    (1, ":L2+0+0+0:", []),
    (1, ":L2+0+0+0:", ["T14+5420:T17+0"] * 2),
    (1, ":L2+0+0+0:", ["T14+5420:T17+1"]),
    (1, ":L2+0+0+0:", ["T14+5410:T17+0"]),
    (1, ":L2+0+0+0:", ["T14+5420"]),
])
def test_reject_unverified_bom(transport, status, rows):
    with pytest.raises(ValueError):
        verify_bom_reply(transport, status, rows, "5420")


def test_confirmation_requires_matching_t63_and_preserves_external_number():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        day = date(2026, 11, 19)
        plan = ProductionPlan(name="CSB", horizon_start=day, horizon_end=day)
        first, second = [ProductionScheduleItem(plan=plan, quantity=1, csb_task_number=n) for n in (9, 12)]
        db.add_all([first, second])
        db.commit()
        for expected, returned, number in ((9, 12, "00123"), (9, 9, " "), (22, 22, "00123")):
            with pytest.raises(ValueError):
                record_order_confirmation(db, expected, returned, number)
        assert first.csb_order_number is None and second.csb_order_number is None
        for _ in range(2):
            record_order_confirmation(db, 12, 12, "00123")
            db.commit()
        with pytest.raises(ValueError):
            record_order_confirmation(db, 12, 12, "00124")
        assert first.csb_order_number is None
        assert second.csb_order_number == "00123"
