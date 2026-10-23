"""
Course-purchase flow over the ZarinPal payment gateway (REST API v4).

Module-level ``async def`` functions following the IranKish service conventions:
each takes ``(request, db_session, ..., current_user)``, enforces authorization,
mutates through the session, and returns a Pydantic read schema. Remote calls go
through ``client``; its three exception classes are translated to HTTPExceptions.

Pricing model
-------------
Since the full offers/enrollments subsystem is an Enterprise-Edition feature not
present in this edition, a course's IRR price is stored on the course itself, in
``course.extra_metadata['course_price_irr']`` (a positive integer in Rial). The
price is shared across gateways, so ZarinPal and IranKish always charge the same
amount; both read and write it through ``services.payments.pricing``. A course
with no price set is not purchasable. An org admin sets the price with
``set_course_price`` (re-exported here from the shared pricing module).

Granting access
----------------
On a verified payment the buyer's access comes from the payment ledger itself:
the ``COMPLETED`` ``ZarinPalPayment`` row IS the entitlement, consulted by
``services.payments.pricing.user_has_paid_course_access``. We do NOT create a
per-course "purchasers" UserGroup — a UserGroup linked to a course means the
course is *restricted to that cohort*, a separate concept from "has paid", and
conflating them let outsiders buy their way past a cohort restriction. On a
completed payment we additionally enrol the buyer in the course (a ``TrailRun``
via the normal trail path) so the purchase shows up as an enrolment.

Purchase and access-restriction (public vs. UserGroup) are independent concerns
and the checkout here deliberately knows nothing about UserGroups: whether a
course is viewable is decided entirely by the access layer, and whether it is
paid is decided entirely by this ledger.

Flow
----
``create_checkout`` requests a payment from ZarinPal, persists a PENDING row
keyed by the returned ``authority``, and returns the StartPay redirect URL. The
buyer's browser is sent to ZarinPal, pays, and is redirected back to our
``/callback`` with ``Authority`` and ``Status`` query params. ``handle_callback``
locates the PENDING row by authority, and on ``Status=OK`` calls the verify
service; a 100/101 verify flips the row to COMPLETED and grants access.
"""

import logging
from datetime import datetime
from typing import Optional
from uuid import uuid4

from fastapi import HTTPException, Request, status
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from config.config import get_learnhouse_config
from src.db.courses.courses import Course
from src.db.organizations import Organization
from src.db.user_organizations import UserOrganization
from src.db.users import AnonymousUser, APITokenUser, PublicUser
from src.db.zarinpal import (
    ZarinPalPayment,
    ZarinPalPaymentRead,
    ZarinPalPaymentStatusEnum,
)
from src.security.auth import resolve_acting_user_id
from src.security.org_auth import require_org_role_permission
from src.security.rbac.rbac import authorization_verify_if_user_is_anon
from src.services.payments.pricing import (
    enrol_user_in_course,
    read_course_price,
    user_has_paid_course_access,
)
from src.services.payments.zarinpal import client as zarinpal_client
from src.services.payments.zarinpal.client import (
    ZarinPalError,
    ZarinPalNotConfigured,
    ZarinPalTransportError,
)

logger = logging.getLogger(__name__)

# The value ZarinPal sends in the callback ``Status`` param on a paid return.
STATUS_OK = "OK"


# ---------------------------------------------------------------------------
# Error translation
# ---------------------------------------------------------------------------


def _translate_zarinpal_error(exc: Exception) -> HTTPException:
    """Map a ZarinPal client exception to an HTTPException."""
    if isinstance(exc, ZarinPalNotConfigured):
        return HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="ZarinPal payment gateway is not configured for this instance.",
        )
    if isinstance(exc, ZarinPalTransportError):
        return HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Could not reach the ZarinPal payment gateway. Please try again later.",
        )
    if isinstance(exc, ZarinPalError):
        return HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Payment gateway rejected the request (code {exc.code}).",
        )
    return HTTPException(
        status_code=status.HTTP_502_BAD_GATEWAY,
        detail="Unexpected error talking to the ZarinPal payment gateway.",
    )


# ---------------------------------------------------------------------------
# Lookup helpers
# ---------------------------------------------------------------------------


async def _get_org_or_404(db_session: AsyncSession, org_id: int) -> Organization:
    org = (
        await db_session.execute(select(Organization).where(Organization.id == org_id))
    ).scalars().first()
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")
    return org


async def _get_course_or_404(
    db_session: AsyncSession, org_id: int, course_id: int
) -> Course:
    course = (
        await db_session.execute(
            select(Course).where(Course.id == course_id, Course.org_id == org_id)
        )
    ).scalars().first()
    if not course:
        raise HTTPException(status_code=404, detail="Course not found")
    return course


async def _is_org_member(db_session: AsyncSession, user_id: int, org_id: int) -> bool:
    row = (
        await db_session.execute(
            select(UserOrganization.id).where(
                UserOrganization.user_id == user_id,
                UserOrganization.org_id == org_id,
            )
        )
    ).scalars().first()
    return row is not None


# ---------------------------------------------------------------------------
# Pricing admin
# ---------------------------------------------------------------------------
#
# Pricing is shared across gateways: the admin price-setter and the price
# reader both live in ``services.payments.pricing`` so a course always sells for
# the same amount on ZarinPal and IranKish. ``set_course_price`` is re-exported
# here unchanged so the router import keeps working.


# ---------------------------------------------------------------------------
# Checkout
# ---------------------------------------------------------------------------


async def create_checkout(
    request: Request,
    db_session: AsyncSession,
    org_id: int,
    course_id: int,
    callback_url: str,
    current_user: PublicUser | AnonymousUser | APITokenUser,
) -> dict:
    """Start a purchase: request a payment and return the redirect URL.

    ``callback_url`` is the absolute URL ZarinPal redirects the buyer's browser
    back to after payment (our callback). The caller (router) supplies it so the
    backend host is not hard-coded here.
    """
    acting_user_id = resolve_acting_user_id(current_user)
    await authorization_verify_if_user_is_anon(acting_user_id)
    await _get_org_or_404(db_session, org_id)

    # A buyer must belong to the org whose course they are buying; access is
    # granted inside that org, so a non-member could never use what they bought.
    if not await _is_org_member(db_session, acting_user_id, org_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You must be a member of this organization to buy its courses.",
        )

    cfg = get_learnhouse_config().zarinpal_config
    if cfg is None:
        raise _translate_zarinpal_error(ZarinPalNotConfigured())

    course = await _get_course_or_404(db_session, org_id, course_id)
    price = read_course_price(course)
    if price is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This course is not available for purchase.",
        )

    # Short-circuit if the buyer already has access (already purchased).
    if await _user_has_course_access(db_session, acting_user_id, course):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="You already have access to this course.",
        )

    # Persist a PENDING row before calling the gateway so a successful request
    # whose response is lost can still be reconciled. The authority is filled in
    # once ZarinPal returns it.
    now = str(datetime.now())
    payment = ZarinPalPayment(
        payment_uuid=f"zppay_{uuid4()}",
        org_id=org_id,
        user_id=acting_user_id,
        course_id=course_id,
        amount=price,
        status=ZarinPalPaymentStatusEnum.PENDING,
        creation_date=now,
        update_date=now,
    )
    db_session.add(payment)
    await db_session.commit()
    await db_session.refresh(payment)

    description = f"Course purchase: {course.name}"[:255]
    try:
        authority = await zarinpal_client.request_payment(
            amount=price,
            callback_url=callback_url,
            description=description,
        )
    except Exception as exc:  # noqa: BLE001 - translated below
        # Mark the attempt failed so it is not left dangling as PENDING.
        payment.status = ZarinPalPaymentStatusEnum.FAILED
        payment.update_date = str(datetime.now())
        db_session.add(payment)
        await db_session.commit()
        raise _translate_zarinpal_error(exc)

    payment.authority = authority
    payment.update_date = str(datetime.now())
    db_session.add(payment)
    await db_session.commit()
    await db_session.refresh(payment)

    return {
        "payment_uuid": payment.payment_uuid,
        "payment_url": zarinpal_client.get_start_pay_url(authority),
        "authority": authority,
        "amount": price,
    }


# ---------------------------------------------------------------------------
# Callback / verification
# ---------------------------------------------------------------------------


async def handle_callback(
    request: Request,
    db_session: AsyncSession,
    authority: Optional[str],
    status_param: Optional[str],
) -> ZarinPalPayment:
    """Process the buyer's return from ZarinPal to our callback URL.

    The caller is anonymous (the buyer's browser, redirected by ZarinPal, not an
    authenticated API client), so this does NOT take a ``current_user``. The
    payment is located by its ``authority``; the buyer recorded on the PENDING
    row is the one granted access, so a forged callback cannot grant access to a
    different user.

    On ``Status=OK`` we call verify; only a 100/101 verify flips the payment to
    COMPLETED and grants access. The amount verified is the amount stored on the
    row (set at checkout), so a tampered callback cannot change what was paid.
    """
    if not authority:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing payment authority in callback.",
        )

    payment = (
        await db_session.execute(
            select(ZarinPalPayment).where(ZarinPalPayment.authority == authority)
        )
    ).scalars().first()
    if not payment:
        raise HTTPException(status_code=404, detail="Payment not found")

    # Idempotency: a repeated callback for an already-completed payment is a
    # no-op success (the buyer may refresh the return page).
    if payment.status == ZarinPalPaymentStatusEnum.COMPLETED:
        return payment

    # A non-OK status means the buyer cancelled or the card was declined.
    if (status_param or "").upper() != STATUS_OK:
        payment.status = ZarinPalPaymentStatusEnum.FAILED
        payment.update_date = str(datetime.now())
        db_session.add(payment)
        await db_session.commit()
        await db_session.refresh(payment)
        return payment

    cfg = get_learnhouse_config().zarinpal_config
    if cfg is None:
        raise _translate_zarinpal_error(ZarinPalNotConfigured())

    try:
        result = await zarinpal_client.verify(
            amount=payment.amount, authority=authority
        )
    except Exception as exc:  # noqa: BLE001 - translated below
        # Record the failure code when available, mark FAILED, and surface it.
        if isinstance(exc, ZarinPalError):
            try:
                payment.code = int(exc.code)
            except (TypeError, ValueError):
                payment.code = None
        payment.status = ZarinPalPaymentStatusEnum.FAILED
        payment.update_date = str(datetime.now())
        db_session.add(payment)
        await db_session.commit()
        # A known gateway failure (declined, unreachable, not configured) is an
        # expected outcome, so log it at warning without a traceback. Anything
        # else is a bug in our handling and gets a full traceback at error so a
        # 500/502 the buyer sees is diagnosable from the server log.
        if isinstance(
            exc, (ZarinPalError, ZarinPalTransportError, ZarinPalNotConfigured)
        ):
            logger.warning(
                "ZarinPal verify failed for payment %s (authority=%s): %s",
                payment.payment_uuid,
                authority,
                exc,
            )
        else:
            logger.exception(
                "Unexpected error verifying ZarinPal payment %s (authority=%s)",
                payment.payment_uuid,
                authority,
            )
        raise _translate_zarinpal_error(exc)

    # Verified and settled: record receipt fields, grant access, mark completed.
    payment.code = result.get("code")
    ref_id = result.get("ref_id")
    if ref_id is not None:
        payment.ref_id = str(ref_id)
    card_pan = result.get("card_pan")
    if card_pan:
        payment.card_pan = str(card_pan)

    # The gateway has confirmed the money moved; from here any failure is on our
    # side (granting access / persisting). Log it with a full traceback so a 500
    # the buyer sees is diagnosable, and re-raise rather than silently swallow.
    try:
        await _grant_course_access(db_session, payment)
        payment.status = ZarinPalPaymentStatusEnum.COMPLETED
        payment.update_date = str(datetime.now())
        db_session.add(payment)
        await db_session.commit()
        await db_session.refresh(payment)
    except Exception:
        logger.exception(
            "ZarinPal payment %s verified at the gateway (ref_id=%s) but granting "
            "course access failed; the row is left non-COMPLETED for reconciliation",
            payment.payment_uuid,
            payment.ref_id,
        )
        raise
    return payment


# ---------------------------------------------------------------------------
# Access granting via the payment ledger
# ---------------------------------------------------------------------------


async def _user_has_course_access(
    db_session: AsyncSession, user_id: int, course: Course
) -> bool:
    """Whether the user already holds a completed purchase of this course.

    Reads the shared payment ledger (both gateways), so a buyer who paid through
    IranKish is recognised here too and is not charged twice.
    """
    return await user_has_paid_course_access(db_session, user_id, course)


async def _grant_course_access(
    db_session: AsyncSession, payment: ZarinPalPayment
) -> None:
    """Record the buyer's access for a settled payment.

    The ``COMPLETED`` payment row itself is the entitlement (see
    ``user_has_paid_course_access``), so there is nothing to insert for access.
    We additionally enrol the buyer in the course so the purchase shows up as an
    enrolment. Enrolment is best-effort: a failure to enrol must not undo a
    settled payment, so it is logged and swallowed.
    """
    course = (
        await db_session.execute(
            select(Course).where(Course.id == payment.course_id)
        )
    ).scalars().first()
    if not course:
        logger.error(
            "Cannot grant access: course %s for payment %s is gone",
            payment.course_id,
            payment.payment_uuid,
        )
        return

    try:
        await enrol_user_in_course(db_session, payment.user_id, course)
    except Exception:
        logger.exception(
            "ZarinPal payment %s settled but enrolling the buyer in course %s "
            "failed; the purchase still grants access via the payment ledger",
            payment.payment_uuid,
            payment.course_id,
        )


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------


def to_read(payment: ZarinPalPayment) -> ZarinPalPaymentRead:
    return ZarinPalPaymentRead(
        id=payment.id,  # type: ignore[arg-type]
        payment_uuid=payment.payment_uuid,
        org_id=payment.org_id,
        user_id=payment.user_id,
        course_id=payment.course_id,
        amount=payment.amount,
        authority=payment.authority,
        ref_id=payment.ref_id,
        card_pan=payment.card_pan,
        code=payment.code,
        status=payment.status,
        creation_date=payment.creation_date,
        update_date=payment.update_date,
    )


async def get_payment(
    request: Request,
    db_session: AsyncSession,
    org_id: int,
    payment_uuid: str,
    current_user: PublicUser | AnonymousUser | APITokenUser,
) -> ZarinPalPaymentRead:
    """Fetch a payment. The owning buyer or an org admin may read it."""
    acting_user_id = resolve_acting_user_id(current_user)
    await authorization_verify_if_user_is_anon(acting_user_id)
    await _get_org_or_404(db_session, org_id)

    payment = (
        await db_session.execute(
            select(ZarinPalPayment).where(
                ZarinPalPayment.payment_uuid == payment_uuid,
                ZarinPalPayment.org_id == org_id,
            )
        )
    ).scalars().first()
    if not payment:
        raise HTTPException(status_code=404, detail="Payment not found")

    if payment.user_id != acting_user_id:
        # Not the buyer: require org admin rights to view someone else's payment.
        await require_org_role_permission(
            acting_user_id, org_id, db_session, "organizations", "action_read"
        )

    return to_read(payment)
