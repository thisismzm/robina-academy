"""
Discount codes for course purchases (ZarinPal / IranKish).

Responsibilities:
  * admin CRUD for an organization's discount codes
  * ``resolve_discount``: validate a code for a buyer + course and compute the
    discounted amount (used by both the preview endpoint and gateway checkout)
  * ``record_redemption``: bump a code's usage when a payment COMPLETES

The discounted amount is always computed server-side from the course price; the
client never supplies an amount. A discount may not bring the price to zero or
below, because the gateways reject zero-amount payments.
"""

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from fastapi import HTTPException, Request, status
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from src.db.courses.courses import Course
from src.db.discount_codes import (
    DiscountCode,
    DiscountCodeCreate,
    DiscountCodeRead,
    DiscountCodeUpdate,
)
from src.db.irankish import IranKishPayment, IranKishPaymentStatusEnum
from src.db.organizations import Organization
from src.db.users import AnonymousUser, APITokenUser, PublicUser
from src.db.zarinpal import ZarinPalPayment, ZarinPalPaymentStatusEnum
from src.security.auth import resolve_acting_user_id
from src.security.org_auth import require_org_role_permission
from src.security.rbac.rbac import authorization_verify_if_user_is_anon

logger = logging.getLogger(__name__)

# One generic message for every "code can't be used" outcome, so the endpoint
# cannot be used to probe which codes exist or why they are unusable.
INVALID_CODE_DETAIL = "This discount code is not valid."


def normalize_code(code: Optional[str]) -> str:
    return (code or "").strip().upper()


@dataclass
class ResolvedDiscount:
    discount: DiscountCode
    original_amount: int
    discount_amount: int
    final_amount: int


def compute_discount(code: DiscountCode, price: int) -> int:
    """The amount (IRR) taken off ``price``; never negative."""
    if code.percent_off:
        return (price * code.percent_off) // 100
    if code.amount_off:
        return code.amount_off
    return 0


def _parse_expiry(value: str) -> datetime:
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="expires_at must be an ISO-8601 date/time.",
        )


def _is_expired(code: DiscountCode) -> bool:
    if not code.expires_at:
        return False
    try:
        expiry = datetime.fromisoformat(code.expires_at)
    except ValueError:
        return True  # unreadable expiry: fail closed
    now = datetime.now(expiry.tzinfo) if expiry.tzinfo else datetime.now()
    return now >= expiry


async def _user_already_used_code(
    db_session: AsyncSession, user_id: int, code_id: int
) -> bool:
    """A buyer may redeem a given code once (a completed payment counts)."""
    zp = (
        await db_session.execute(
            select(ZarinPalPayment.id).where(
                ZarinPalPayment.user_id == user_id,
                ZarinPalPayment.discount_code_id == code_id,
                ZarinPalPayment.status == ZarinPalPaymentStatusEnum.COMPLETED,
            )
        )
    ).scalars().first()
    if zp is not None:
        return True
    ik = (
        await db_session.execute(
            select(IranKishPayment.id).where(
                IranKishPayment.user_id == user_id,
                IranKishPayment.discount_code_id == code_id,
                IranKishPayment.status == IranKishPaymentStatusEnum.COMPLETED,
            )
        )
    ).scalars().first()
    return ik is not None


async def resolve_discount(
    db_session: AsyncSession,
    *,
    org_id: int,
    course: Course,
    price: int,
    raw_code: str,
    user_id: int,
) -> ResolvedDiscount:
    """Validate ``raw_code`` for this buyer + course and compute the new price.

    Raises 400 with a generic message for any unusable code.
    """
    code_str = normalize_code(raw_code)
    invalid = HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST, detail=INVALID_CODE_DETAIL
    )
    if not code_str:
        raise invalid

    code = (
        await db_session.execute(
            select(DiscountCode).where(
                DiscountCode.org_id == org_id, DiscountCode.code == code_str
            )
        )
    ).scalars().first()
    if (
        code is None
        or not code.is_active
        or _is_expired(code)
        or (code.course_id is not None and code.course_id != course.id)
        or (
            code.max_redemptions is not None
            and code.redemption_count >= code.max_redemptions
        )
        or (
            code.single_use_per_user
            and await _user_already_used_code(db_session, user_id, code.id)  # type: ignore[arg-type]
        )
    ):
        raise invalid

    discount_amount = compute_discount(code, price)
    final_amount = price - discount_amount
    if discount_amount <= 0 or final_amount <= 0:
        # Discount would make the course free (or is a no-op); not supported
        # because the gateways reject zero-amount payments.
        raise invalid

    return ResolvedDiscount(
        discount=code,
        original_amount=price,
        discount_amount=discount_amount,
        final_amount=final_amount,
    )


async def record_redemption(
    db_session: AsyncSession, discount_code_id: Optional[int]
) -> None:
    """Increment a code's usage counter. Flush-only; the caller commits.

    Called once per payment, when it first becomes COMPLETED (callbacks that
    arrive again for a COMPLETED payment return early and never reach here).
    """
    if not discount_code_id:
        return
    await db_session.execute(
        update(DiscountCode)
        .where(DiscountCode.id == discount_code_id)
        .values(redemption_count=DiscountCode.redemption_count + 1)
    )


# ---------------------------------------------------------------------------
# Admin CRUD
# ---------------------------------------------------------------------------


def _to_read(code: DiscountCode) -> DiscountCodeRead:
    return DiscountCodeRead(
        id=code.id,  # type: ignore[arg-type]
        org_id=code.org_id,
        course_id=code.course_id,
        code=code.code,
        percent_off=code.percent_off,
        amount_off=code.amount_off,
        max_redemptions=code.max_redemptions,
        redemption_count=code.redemption_count,
        expires_at=code.expires_at,
        single_use_per_user=code.single_use_per_user,
        is_active=code.is_active,
        creation_date=code.creation_date,
        update_date=code.update_date,
    )


async def _admin_gate(
    db_session: AsyncSession,
    org_id: int,
    current_user: PublicUser | AnonymousUser | APITokenUser,
) -> None:
    acting_user_id = resolve_acting_user_id(current_user)
    await authorization_verify_if_user_is_anon(acting_user_id)
    org = (
        await db_session.execute(select(Organization).where(Organization.id == org_id))
    ).scalars().first()
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")
    await require_org_role_permission(
        acting_user_id, org_id, db_session, "courses", "action_update"
    )


async def _get_code_or_404(
    db_session: AsyncSession, org_id: int, code_id: int
) -> DiscountCode:
    code = (
        await db_session.execute(
            select(DiscountCode).where(
                DiscountCode.id == code_id, DiscountCode.org_id == org_id
            )
        )
    ).scalars().first()
    if not code:
        raise HTTPException(status_code=404, detail="Discount code not found")
    return code


async def create_discount_code(
    request: Request,
    db_session: AsyncSession,
    org_id: int,
    data: DiscountCodeCreate,
    current_user: PublicUser | AnonymousUser | APITokenUser,
) -> DiscountCodeRead:
    await _admin_gate(db_session, org_id, current_user)

    if (data.percent_off is None) == (data.amount_off is None):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Provide exactly one of percent_off or amount_off.",
        )

    code_str = normalize_code(data.code)
    if len(code_str) < 3:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Code must be at least 3 characters.",
        )

    if data.course_id is not None:
        course = (
            await db_session.execute(
                select(Course).where(
                    Course.id == data.course_id, Course.org_id == org_id
                )
            )
        ).scalars().first()
        if not course:
            raise HTTPException(status_code=404, detail="Course not found")

    if data.expires_at:
        _parse_expiry(data.expires_at)

    now = str(datetime.now())
    code = DiscountCode(
        org_id=org_id,
        course_id=data.course_id,
        code=code_str,
        percent_off=data.percent_off,
        amount_off=data.amount_off,
        max_redemptions=data.max_redemptions,
        redemption_count=0,
        expires_at=data.expires_at or None,
        single_use_per_user=data.single_use_per_user,
        is_active=True,
        creation_date=now,
        update_date=now,
    )
    db_session.add(code)
    try:
        await db_session.commit()
    except IntegrityError:
        await db_session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A discount code with this name already exists.",
        )
    await db_session.refresh(code)
    return _to_read(code)


async def list_discount_codes(
    request: Request,
    db_session: AsyncSession,
    org_id: int,
    current_user: PublicUser | AnonymousUser | APITokenUser,
) -> list[DiscountCodeRead]:
    await _admin_gate(db_session, org_id, current_user)
    rows = (
        await db_session.execute(
            select(DiscountCode)
            .where(DiscountCode.org_id == org_id)
            .order_by(DiscountCode.id.desc())  # type: ignore[union-attr]
        )
    ).scalars().all()
    return [_to_read(r) for r in rows]


async def update_discount_code(
    request: Request,
    db_session: AsyncSession,
    org_id: int,
    code_id: int,
    data: DiscountCodeUpdate,
    current_user: PublicUser | AnonymousUser | APITokenUser,
) -> DiscountCodeRead:
    await _admin_gate(db_session, org_id, current_user)
    code = await _get_code_or_404(db_session, org_id, code_id)

    if data.is_active is not None:
        code.is_active = data.is_active
    if data.single_use_per_user is not None:
        code.single_use_per_user = data.single_use_per_user
    if data.clear_max_redemptions:
        code.max_redemptions = None
    elif data.max_redemptions is not None:
        code.max_redemptions = data.max_redemptions
    if data.clear_expires_at:
        code.expires_at = None
    elif data.expires_at:
        _parse_expiry(data.expires_at)
        code.expires_at = data.expires_at
    code.update_date = str(datetime.now())
    db_session.add(code)
    await db_session.commit()
    await db_session.refresh(code)
    return _to_read(code)


async def delete_discount_code(
    request: Request,
    db_session: AsyncSession,
    org_id: int,
    code_id: int,
    current_user: PublicUser | AnonymousUser | APITokenUser,
) -> dict:
    """Delete a code. Past payments keep their amounts (FK is SET NULL)."""
    await _admin_gate(db_session, org_id, current_user)
    code = await _get_code_or_404(db_session, org_id, code_id)
    await db_session.delete(code)
    await db_session.commit()
    return {"deleted": True, "id": code_id}
