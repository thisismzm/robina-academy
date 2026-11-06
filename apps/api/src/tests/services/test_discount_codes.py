"""Tests for course-purchase discount codes (admin CRUD, resolution, checkout)."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException
from sqlmodel import select

from src.db.discount_codes import DiscountCode, DiscountCodeCreate, DiscountCodeUpdate
from src.db.irankish import IranKishPayment, IranKishPaymentStatusEnum
from src.services.payments import discounts as dsvc
from src.services.payments.irankish import irankish as ik
from src.services.payments.pricing import set_course_price


@pytest.fixture
def patch_ik():
    cfg = SimpleNamespace(
        irankish_config=SimpleNamespace(
            base_url="https://ikc.shaparak.ir",
            terminal_id="02000001",
            acceptor_id="992000000000523",
            pass_phrase="AAFF124578127138",
            public_key="pem",
        )
    )
    fake_env = SimpleNamespace(to_request=lambda: {"data": "AA", "iv": "BB"})
    with patch(
        "src.services.payments.irankish.client.get_learnhouse_config", return_value=cfg
    ), patch(
        "src.services.payments.irankish.irankish.make_authentication_envelope",
        return_value=fake_env,
    ):
        yield


async def _mk(db, mock_request, org, admin_user, **kw):
    data = DiscountCodeCreate(code=kw.pop("code", "Spring10"), description=kw.pop("description", "Spring campaign"), **kw)
    return await dsvc.create_discount_code(mock_request, db, org.id, data, admin_user)


async def test_create_normalizes_and_requires_exactly_one_kind(db, org, admin_user, mock_request):
    created = await _mk(db, mock_request, org, admin_user, percent_off=10)
    assert created.code == "SPRING10"

    with pytest.raises(HTTPException) as ei:
        await _mk(db, mock_request, org, admin_user, code="BOTH", percent_off=10, amount_off=5)
    assert ei.value.status_code == 400
    with pytest.raises(HTTPException) as ei:
        await _mk(db, mock_request, org, admin_user, code="NONE")
    assert ei.value.status_code == 400


async def test_duplicate_code_conflicts(db, org, admin_user, mock_request):
    await _mk(db, mock_request, org, admin_user, percent_off=10)
    with pytest.raises(HTTPException) as ei:
        await _mk(db, mock_request, org, admin_user, code="spring10", percent_off=20)
    assert ei.value.status_code == 409


async def test_non_admin_cannot_manage_codes(db, org, regular_user, mock_request, monkeypatch):
    async def deny(*a, **k):
        raise HTTPException(status_code=403, detail="no")

    monkeypatch.setattr(dsvc, "require_org_role_permission", deny)
    with pytest.raises(HTTPException) as ei:
        await dsvc.list_discount_codes(mock_request, db, org.id, regular_user)
    assert ei.value.status_code == 403


async def test_resolve_percent_and_fixed(db, org, admin_user, regular_user, course, mock_request):
    await _mk(db, mock_request, org, admin_user, code="PCT", percent_off=25)
    await _mk(db, mock_request, org, admin_user, code="FIX", amount_off=30000)

    r = await dsvc.resolve_discount(
        db, org_id=org.id, course=course, price=100000, raw_code=" pct ", user_id=regular_user.id
    )
    assert (r.discount_amount, r.final_amount) == (25000, 75000)

    r = await dsvc.resolve_discount(
        db, org_id=org.id, course=course, price=100000, raw_code="FIX", user_id=regular_user.id
    )
    assert (r.discount_amount, r.final_amount) == (30000, 70000)


@pytest.mark.parametrize(
    "setup",
    ["inactive", "expired", "exhausted", "other_course", "unknown", "free"],
)
async def test_resolve_rejects_unusable_codes(
    setup, db, org, admin_user, regular_user, course, mock_request
):
    kwargs = {"code": "BAD", "percent_off": 10}
    if setup == "other_course":
        kwargs["course_id"] = course.id + 999  # never matches
    if setup == "free":
        kwargs = {"code": "BAD", "amount_off": 100000}
    if setup == "expired":
        kwargs["expires_at"] = "2000-01-01T00:00:00"
    if setup != "unknown":
        if setup == "other_course":
            # SQLite doesn't enforce the FK; insert a code for a different course.
            db.add(DiscountCode(
                org_id=org.id, course_id=course.id + 999, code="BAD", percent_off=10,
                creation_date="x", update_date="x",
            ))
            await db.commit()
        else:
            created = await _mk(db, mock_request, org, admin_user, **kwargs)
            if setup == "inactive":
                await dsvc.update_discount_code(
                    mock_request, db, org.id, created.id, DiscountCodeUpdate(is_active=False), admin_user
                )
            if setup == "exhausted":
                await dsvc.update_discount_code(
                    mock_request, db, org.id, created.id, DiscountCodeUpdate(max_redemptions=1), admin_user
                )
                await dsvc.record_redemption(db, created.id)
                await db.commit()

    with pytest.raises(HTTPException) as ei:
        await dsvc.resolve_discount(
            db, org_id=org.id, course=course, price=100000, raw_code="BAD", user_id=regular_user.id
        )
    assert ei.value.status_code == 400
    assert ei.value.detail == dsvc.INVALID_CODE_DETAIL


async def test_checkout_charges_discounted_amount_and_records_redemption(
    db, org, admin_user, course, mock_request, patch_ik
):
    await set_course_price(mock_request, db, org.id, course.id, 200000, admin_user)
    created = await _mk(db, mock_request, org, admin_user, percent_off=50)

    sent = {}

    async def fake_token(body, envelope):
        sent["amount"] = body["amount"]
        return "TOK-1"

    with patch(
        "src.services.payments.irankish.irankish.irankish_client.make_token",
        new=AsyncMock(side_effect=fake_token),
    ):
        result = await ik.create_checkout(
            mock_request, db, org.id, course.id, "https://api/cb", admin_user,
            discount_code="spring10",
        )

    assert sent["amount"] == 100000
    assert result["amount"] == 100000
    assert result["original_amount"] == 200000
    assert result["discount_amount"] == 100000

    payment = (
        await db.execute(select(IranKishPayment).where(IranKishPayment.token == "TOK-1"))
    ).scalars().first()
    assert payment.amount == 100000
    assert payment.original_amount == 200000
    assert payment.discount_code_id == created.id

    # Completing the payment (callback) bumps the redemption counter.
    with patch(
        "src.services.payments.irankish.irankish.irankish_client.confirm",
        new=AsyncMock(return_value=None),
    ):
        done = await ik.handle_callback(
            mock_request, db,
            {"token": "TOK-1", "responseCode": "00",
             "retrievalReferenceNumber": "123456789012", "systemTraceAuditNumber": "123456"},
        )
    assert done.status == IranKishPaymentStatusEnum.COMPLETED
    code = (await db.execute(select(DiscountCode).where(DiscountCode.id == created.id))).scalars().first()
    await db.refresh(code)
    assert code.redemption_count == 1


async def test_checkout_with_invalid_code_does_not_create_payment(
    db, org, admin_user, course, mock_request, patch_ik
):
    await set_course_price(mock_request, db, org.id, course.id, 200000, admin_user)
    with pytest.raises(HTTPException) as ei:
        await ik.create_checkout(
            mock_request, db, org.id, course.id, "https://api/cb", admin_user,
            discount_code="NOPE",
        )
    assert ei.value.status_code == 400
    rows = (await db.execute(select(IranKishPayment))).scalars().all()
    assert rows == []


async def test_reusable_code_allows_same_buyer_again(db, org, admin_user, regular_user, course, mock_request, patch_ik):
    once = await _mk(db, mock_request, org, admin_user, code="ONCE", percent_off=10)
    many = await _mk(db, mock_request, org, admin_user, code="MANY", percent_off=10, single_use_per_user=False)
    for c in (once, many):
        db.add(IranKishPayment(
            payment_uuid=f"ikpay_{c.code}", org_id=org.id, user_id=regular_user.id,
            course_id=course.id, amount=90, request_id=c.code, discount_code_id=c.id,
            status=IranKishPaymentStatusEnum.COMPLETED, creation_date="x", update_date="x",
        ))
    await db.commit()

    kw = dict(org_id=org.id, course=course, price=1000, user_id=regular_user.id)
    with pytest.raises(HTTPException):
        await dsvc.resolve_discount(db, raw_code="ONCE", **kw)
    r = await dsvc.resolve_discount(db, raw_code="MANY", **kw)
    assert r.final_amount == 900


async def test_description_is_required_and_updatable(db, org, admin_user, mock_request):
    with pytest.raises(HTTPException) as ei:
        await _mk(db, mock_request, org, admin_user, code="NODESC", percent_off=10, description="   ")
    assert ei.value.status_code == 400

    created = await _mk(db, mock_request, org, admin_user, percent_off=10, description="Partner X")
    assert created.description == "Partner X"
    updated = await dsvc.update_discount_code(
        mock_request, db, org.id, created.id, DiscountCodeUpdate(description=" New note "), admin_user
    )
    assert updated.description == "New note"


async def test_report_and_redemptions_reflect_completed_payments(
    db, org, admin_user, regular_user, course, mock_request
):
    used = await _mk(db, mock_request, org, admin_user, code="USED", percent_off=10)
    idle = await _mk(db, mock_request, org, admin_user, code="IDLE", percent_off=10)
    for i, st in enumerate((IranKishPaymentStatusEnum.COMPLETED, IranKishPaymentStatusEnum.FAILED)):
        db.add(IranKishPayment(
            payment_uuid=f"ikpay_{i}", org_id=org.id, user_id=regular_user.id,
            course_id=course.id, amount=900, original_amount=1000, discount_amount=100,
            request_id=f"r{i}", discount_code_id=used.id, status=st,
            creation_date="2026-01-01", update_date=f"2026-01-0{i + 1}",
        ))
    await db.commit()

    report = await dsvc.get_discount_report(mock_request, db, org.id, admin_user)
    by_code = {c.code: c for c in report.codes}
    assert (by_code["USED"].redemptions, by_code["USED"].incomplete_attempts) == (1, 1)
    assert (by_code["USED"].discount_total, by_code["USED"].revenue_total) == (100, 900)
    assert by_code["USED"].unique_buyers == 1
    assert by_code["IDLE"].redemptions == 0
    assert (report.redemptions, report.discount_total) == (1, 100)

    rows = await dsvc.list_redemptions(mock_request, db, org.id, admin_user)
    assert [r.payment_uuid for r in rows] == ["ikpay_0"]
    assert rows[0].code == "USED" and rows[0].email == regular_user.email
    rows = await dsvc.list_redemptions(mock_request, db, org.id, admin_user, include_incomplete=True)
    assert len(rows) == 2
    rows = await dsvc.list_redemptions(mock_request, db, org.id, admin_user, code_id=idle.id)
    assert rows == []
