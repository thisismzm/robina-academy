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

import logging
from datetime import datetime
from typing import Optional
from uuid import uuid4

from fastapi import HTTPException, Request, status
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from src.db.courses.courses import Course
from src.db.irankish import IranKishPayment, IranKishPaymentStatusEnum
from src.db.organizations import Organization
from src.db.trail_runs import TrailRun
from src.db.trails import Trail
from src.db.users import AnonymousUser, APITokenUser, PublicUser
from src.db.zarinpal import ZarinPalPayment, ZarinPalPaymentStatusEnum
from src.security.auth import resolve_acting_user_id
from src.security.org_auth import require_org_role_permission
from src.security.rbac.rbac import authorization_verify_if_user_is_anon

logger = logging.getLogger(__name__)

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


# ---------------------------------------------------------------------------
# Paid-course access gate
# ---------------------------------------------------------------------------


async def user_has_paid_course_access(
    db_session: AsyncSession, user_id: int, course: Course
) -> bool:
    """Whether ``user_id`` has a completed purchase of ``course`` (any gateway).

    The payment ledger is the single source of truth for "who paid": a
    ``COMPLETED`` row in ``ZarinPalPayment`` or ``IranKishPayment`` for this
    user + course means the purchase settled. We no longer infer payment from
    UserGroup membership, because a UserGroup linked to a course is a *access
    restriction* (a cohort gate), not a record of purchase — conflating the two
    let a buyer's membership satisfy, or be satisfied by, an unrelated
    restriction group. Keeping payment in the ledger keeps the two concerns
    separate.

    Whether the course is viewable (public, or restricted to a UserGroup) is an
    independent concern enforced by the access layer; this function is only
    about payment and never consults UserGroups.

    A ``user_id`` of 0 (anonymous / API token) never has paid access.
    """
    if not user_id:
        return False

    zarinpal = (
        await db_session.execute(
            select(ZarinPalPayment.id).where(
                ZarinPalPayment.course_id == course.id,
                ZarinPalPayment.user_id == user_id,
                ZarinPalPayment.status == ZarinPalPaymentStatusEnum.COMPLETED,
            )
        )
    ).scalars().first()
    if zarinpal is not None:
        return True

    irankish = (
        await db_session.execute(
            select(IranKishPayment.id).where(
                IranKishPayment.course_id == course.id,
                IranKishPayment.user_id == user_id,
                IranKishPayment.status == IranKishPaymentStatusEnum.COMPLETED,
            )
        )
    ).scalars().first()
    return irankish is not None


def course_is_paid(course: Course) -> bool:
    """Whether the course has a gateway price set (i.e. it must be purchased)."""
    return read_course_price(course) is not None


async def enrol_user_in_course(
    db_session: AsyncSession, user_id: int, course: Course
) -> None:
    """Enrol ``user_id`` in ``course`` (idempotent), mirroring the trail path.

    A completed purchase enrols the buyer so the course shows up on their trail.
    The gateway callback is anonymous (no ``PublicUser``), so this creates the
    per-user/per-org ``Trail`` and the per-course ``TrailRun`` directly by id
    rather than going through the user-object trail service.

    Enrolment is idempotent: an existing ``TrailRun`` for this user+course is
    left untouched. The row is added to the session but not committed here; the
    caller commits it alongside the payment status flip.
    """
    if not user_id or course.id is None:
        return

    now = str(datetime.now())

    trail = (
        await db_session.execute(
            select(Trail).where(
                Trail.org_id == course.org_id, Trail.user_id == user_id
            )
        )
    ).scalars().first()
    if trail is None:
        trail = Trail(
            org_id=course.org_id,
            user_id=user_id,
            trail_uuid=f"trail_{uuid4()}",
            creation_date=now,
            update_date=now,
        )
        db_session.add(trail)
        await db_session.flush()  # assign trail.id

    existing_run = (
        await db_session.execute(
            select(TrailRun.id).where(
                TrailRun.trail_id == trail.id,
                TrailRun.course_id == course.id,
                TrailRun.user_id == user_id,
            )
        )
    ).scalars().first()
    if existing_run is not None:
        return

    db_session.add(
        TrailRun(
            trail_id=trail.id if trail.id is not None else 0,
            course_id=course.id,
            org_id=course.org_id,
            user_id=user_id,
            creation_date=now,
            update_date=now,
        )
    )
    await db_session.flush()
