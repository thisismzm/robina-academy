"""End-to-end flow tests for the IranKish course-payment service.

Covers: pricing a course, starting a checkout (token obtained, PENDING row
created), a successful callback (confirm -> COMPLETED -> course access granted
via the payment ledger plus a trail enrolment), and a failed callback (FAILED,
no access). The remote gateway (token/confirm) and the digital-envelope crypto
are mocked; the DB is the in-memory SQLite from conftest, so the ledger and
enrolment wiring is exercised for real.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException
from sqlmodel import select

from src.db.irankish import IranKishPayment, IranKishPaymentStatusEnum
from src.db.trail_runs import TrailRun
from src.services.payments.irankish import irankish as svc
from src.services.payments.pricing import (
    COURSE_PRICE_METADATA_KEY,
    set_course_price,
    user_has_paid_course_access,
)


def _fake_config():
    """A fully-populated IranKish config object."""
    return SimpleNamespace(
        irankish_config=SimpleNamespace(
            base_url="https://ikc.shaparak.ir",
            terminal_id="02000001",
            acceptor_id="992000000000523",
            pass_phrase="AAFF124578127138",
            public_key="-----BEGIN PUBLIC KEY-----\nMII...\n-----END PUBLIC KEY-----",
        )
    )


@pytest.fixture
def patch_config():
    with patch(
        "src.services.payments.irankish.client.get_learnhouse_config",
        return_value=_fake_config(),
    ):
        yield


@pytest.fixture
def patch_envelope():
    # The crypto is unit-tested separately; here we only need a valid-looking
    # envelope so the service proceeds to the token call.
    fake = SimpleNamespace(to_request=lambda: {"data": "AA", "iv": "BB"})
    with patch(
        "src.services.payments.irankish.irankish.make_authentication_envelope",
        return_value=fake,
    ):
        yield


# ---------------------------------------------------------------------------
# Pricing
# ---------------------------------------------------------------------------


async def test_set_course_price_sets_and_clears(db, org, admin_user, course, mock_request):
    # Price is stored under the shared, gateway-agnostic key so ZarinPal and
    # IranKish always charge the same amount.
    await set_course_price(mock_request, db, org.id, course.id, 150000, admin_user)
    await db.refresh(course)
    assert (course.extra_metadata or {})[COURSE_PRICE_METADATA_KEY] == 150000

    await set_course_price(mock_request, db, org.id, course.id, None, admin_user)
    await db.refresh(course)
    assert COURSE_PRICE_METADATA_KEY not in (course.extra_metadata or {})


async def test_set_course_price_rejects_non_positive(db, org, admin_user, course, mock_request):
    with pytest.raises(HTTPException) as ei:
        await set_course_price(mock_request, db, org.id, course.id, 0, admin_user)
    assert ei.value.status_code == 400


# ---------------------------------------------------------------------------
# Checkout
# ---------------------------------------------------------------------------


async def test_checkout_requires_a_price(db, org, regular_user, course, mock_request, patch_config):
    with pytest.raises(HTTPException) as ei:
        await svc.create_checkout(
            mock_request, db, org.id, course.id, "https://api/callback", regular_user
        )
    assert ei.value.status_code == 400


async def test_checkout_rejects_non_member(db, org, other_org, admin_user, course, mock_request, patch_config):
    # admin_user belongs to `org`; buying a course in `other_org` must 403.
    # Put the course under other_org for this check.
    course.org_id = other_org.id
    db.add(course)
    await db.commit()
    with pytest.raises(HTTPException) as ei:
        await svc.create_checkout(
            mock_request, db, other_org.id, course.id, "https://api/callback", admin_user
        )
    assert ei.value.status_code == 403


async def test_checkout_creates_pending_payment_and_returns_token(
    db, org, admin_user, course, mock_request, patch_config, patch_envelope
):
    # Price the course first.
    await set_course_price(mock_request, db, org.id, course.id, 250000, admin_user)

    with patch(
        "src.services.payments.irankish.irankish.irankish_client.make_token",
        new=AsyncMock(return_value="TOKEN-XYZ"),
    ):
        result = await svc.create_checkout(
            mock_request, db, org.id, course.id, "https://api/callback", admin_user
        )

    assert result["form_fields"] == {"tokenIdentity": "TOKEN-XYZ"}
    assert result["amount"] == 250000
    assert result["payment_url"].endswith("/iuiv3/IPG/Index/")

    payment = (
        await db.execute(select(IranKishPayment).where(IranKishPayment.token == "TOKEN-XYZ"))
    ).scalars().first()
    assert payment is not None
    assert payment.status == IranKishPaymentStatusEnum.PENDING
    assert payment.amount == 250000
    assert payment.user_id == admin_user.id


async def test_checkout_marks_failed_when_token_call_fails(
    db, org, admin_user, course, mock_request, patch_config, patch_envelope
):
    await set_course_price(mock_request, db, org.id, course.id, 250000, admin_user)

    from src.services.payments.irankish.client import IranKishError

    with patch(
        "src.services.payments.irankish.irankish.irankish_client.make_token",
        new=AsyncMock(side_effect=IranKishError("907", "bad revert")),
    ):
        with pytest.raises(HTTPException) as ei:
            await svc.create_checkout(
                mock_request, db, org.id, course.id, "https://api/callback", admin_user
            )
    assert ei.value.status_code == 400

    payment = (
        await db.execute(select(IranKishPayment).where(IranKishPayment.org_id == org.id))
    ).scalars().first()
    assert payment.status == IranKishPaymentStatusEnum.FAILED


# ---------------------------------------------------------------------------
# Callback / access granting
# ---------------------------------------------------------------------------


async def _start_payment(db, org, user, course, mock_request):
    await set_course_price(mock_request, db, org.id, course.id, 100000, user)
    with patch(
        "src.services.payments.irankish.irankish.irankish_client.make_token",
        new=AsyncMock(return_value="TOKEN-CB"),
    ):
        await svc.create_checkout(
            mock_request, db, org.id, course.id, "https://api/callback", user
        )
    return "TOKEN-CB"


async def test_successful_callback_confirms_and_grants_access(
    db, org, admin_user, course, mock_request, patch_config, patch_envelope
):
    token = await _start_payment(db, org, admin_user, course, mock_request)

    callback = {
        "token": token,
        "responseCode": "00",
        "retrievalReferenceNumber": "123456789012",
        "systemTraceAuditNumber": "123456",
        "maskedPan": "6037********1234",
        "amount": "100000",
    }

    with patch(
        "src.services.payments.irankish.irankish.irankish_client.confirm",
        new=AsyncMock(return_value={"responseCode": "00", "amount": "100000"}),
    ):
        payment = await svc.handle_callback(mock_request, db, callback)

    assert payment.status == IranKishPaymentStatusEnum.COMPLETED
    assert payment.retrieval_reference_number == "123456789012"

    # Access granted: the COMPLETED ledger row IS the entitlement (no UserGroup).
    assert await user_has_paid_course_access(db, admin_user.id, course) is True

    # And the buyer is enrolled: a TrailRun exists for them on this course.
    run = (
        await db.execute(
            select(TrailRun).where(
                TrailRun.course_id == course.id,
                TrailRun.user_id == admin_user.id,
            )
        )
    ).scalars().first()
    assert run is not None


async def test_failed_callback_marks_failed_and_grants_no_access(
    db, org, admin_user, course, mock_request, patch_config, patch_envelope
):
    token = await _start_payment(db, org, admin_user, course, mock_request)

    callback = {"token": token, "responseCode": "51"}  # insufficient funds
    payment = await svc.handle_callback(mock_request, db, callback)

    assert payment.status == IranKishPaymentStatusEnum.FAILED
    # No access: the ledger has no COMPLETED row and no enrolment was created.
    assert await user_has_paid_course_access(db, admin_user.id, course) is False
    run = (
        await db.execute(
            select(TrailRun).where(TrailRun.user_id == admin_user.id)
        )
    ).scalars().first()
    assert run is None


async def test_callback_is_idempotent_on_completed(
    db, org, admin_user, course, mock_request, patch_config, patch_envelope
):
    token = await _start_payment(db, org, admin_user, course, mock_request)
    callback = {
        "token": token,
        "responseCode": "00",
        "retrievalReferenceNumber": "1",
        "systemTraceAuditNumber": "2",
    }
    with patch(
        "src.services.payments.irankish.irankish.irankish_client.confirm",
        new=AsyncMock(return_value={"responseCode": "00"}),
    ) as confirm_mock:
        await svc.handle_callback(mock_request, db, callback)
        # Second delivery: must not call confirm again, must stay COMPLETED.
        payment = await svc.handle_callback(mock_request, db, callback)
    assert payment.status == IranKishPaymentStatusEnum.COMPLETED
    assert confirm_mock.await_count == 1


async def test_callback_unknown_token_404(db, mock_request, patch_config):
    with pytest.raises(HTTPException) as ei:
        await svc.handle_callback(mock_request, db, {"token": "nope", "responseCode": "00"})
    assert ei.value.status_code == 404


async def test_checkout_conflict_if_already_has_access(
    db, org, admin_user, course, mock_request, patch_config, patch_envelope
):
    # Complete one purchase to grant access...
    token = await _start_payment(db, org, admin_user, course, mock_request)
    with patch(
        "src.services.payments.irankish.irankish.irankish_client.confirm",
        new=AsyncMock(return_value={"responseCode": "00"}),
    ):
        await svc.handle_callback(
            mock_request, db,
            {"token": token, "responseCode": "00", "retrievalReferenceNumber": "1", "systemTraceAuditNumber": "2"},
        )

    # ...then a second checkout must be refused with 409.
    with pytest.raises(HTTPException) as ei:
        await svc.create_checkout(
            mock_request, db, org.id, course.id, "https://api/callback", admin_user
        )
    assert ei.value.status_code == 409
