"""
Shared course pricing for the Iranian payment gateways (ZarinPal, IranKish).

A course sells for a single IRR amount regardless of which gateway the buyer
pays through, so the price lives under ONE key in ``course.extra_metadata`` and
both gateways read/write it here. Storing one value makes it impossible for the
two gateways to disagree on a course's price.

    course.extra_metadata["course_price_irr"]  # positive int, Rial

A course with no price set is not purchasable through any gateway.

Backward compatibility
----------------------
Earlier versions stored the price under two independent, gateway-specific keys
(``zarinpal_price_irr`` / ``irankish_price_irr``). ``read_course_price`` falls
back to those legacy keys when the shared key is absent so courses priced under
the old scheme keep working; ``set_course_price`` writes the shared key and
removes the legacy keys so the shared value becomes the single source of truth.
"""

from datetime import datetime
from typing import Optional

from fastapi import HTTPException, Request, status
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from src.db.courses.courses import Course
from src.db.organizations import Organization
from src.db.users import AnonymousUser, APITokenUser, PublicUser
from src.security.auth import resolve_acting_user_id
from src.security.org_auth import require_org_role_permission
from src.security.rbac.rbac import authorization_verify_if_user_is_anon

# The single key a course's IRR price is stored under, shared by all gateways.
COURSE_PRICE_METADATA_KEY = "course_price_irr"

# Legacy per-gateway keys, read as a fallback and cleared on the next write so
# courses priced under the old scheme migrate to the shared key transparently.
_LEGACY_PRICE_KEYS = ("zarinpal_price_irr", "irankish_price_irr")


def _coerce_price(raw: object) -> Optional[int]:
    """Return a positive int price, or None if the value is missing/invalid."""
    if raw is None:
        return None
    try:
        price = int(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return price if price > 0 else None


def read_course_price(course: Course) -> Optional[int]:
    """Return the course's shared IRR price, or None if it is not set for sale.

    Falls back to the legacy per-gateway keys so courses priced before the
    shared key existed remain purchasable.
    """
    meta = course.extra_metadata or {}
    price = _coerce_price(meta.get(COURSE_PRICE_METADATA_KEY))
    if price is not None:
        return price
    # Fall back to whichever legacy key still carries a usable value.
    for key in _LEGACY_PRICE_KEYS:
        price = _coerce_price(meta.get(key))
        if price is not None:
            return price
    return None


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


async def set_course_price(
    request: Request,
    db_session: AsyncSession,
    org_id: int,
    course_id: int,
    price_irr: Optional[int],
    current_user: PublicUser | AnonymousUser | APITokenUser,
) -> dict:
    """Set (or clear, with ``None``) a course's shared gateway price, admin-only.

    The price applies to every gateway equally. A non-positive price is
    rejected; passing ``None`` removes the price and unlists the course from all
    gateways. Any legacy per-gateway keys are cleared so the shared value is the
    single source of truth going forward.
    """
    acting_user_id = resolve_acting_user_id(current_user)
    await authorization_verify_if_user_is_anon(acting_user_id)
    await _get_org_or_404(db_session, org_id)
    await require_org_role_permission(
        acting_user_id, org_id, db_session, "courses", "action_update"
    )

    if price_irr is not None and price_irr <= 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="price_irr must be a positive integer (amount in Rial).",
        )

    course = await _get_course_or_404(db_session, org_id, course_id)
    meta = dict(course.extra_metadata or {})
    # Drop the legacy per-gateway keys so they can never diverge from the shared
    # price; the shared key below is the one value every gateway reads.
    for key in _LEGACY_PRICE_KEYS:
        meta.pop(key, None)
    if price_irr is None:
        meta.pop(COURSE_PRICE_METADATA_KEY, None)
    else:
        meta[COURSE_PRICE_METADATA_KEY] = int(price_irr)
    course.extra_metadata = meta
    course.update_date = str(datetime.now())
    db_session.add(course)
    await db_session.commit()
    return {"course_id": course_id, "price_irr": price_irr}
