"""
Shared, gateway-agnostic course-payment endpoints.

Mounted in ``src/router.py`` under the prefix ``/payments`` so the full paths
are ``/api/v1/payments/...``.

Where ``/payments/zarinpal/...`` and ``/payments/irankish/...`` are each tied to
one gateway, this router exposes the information a buyer's UI needs to OFFER a
choice of gateway: a course sells for a single shared IRR price, and only the
gateways configured on this instance are usable. The client reads the active
gateways + the price from here, lets the buyer pick one, then calls that
gateway's existing ``/checkout`` endpoint.

Endpoints:
  GET /gateways                                 public: which gateways are active
  GET /{org_id}/courses/{course_id}/options     which active gateways + the price
"""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from src.core.events.database import get_db_session
from src.db.courses.courses import Course
from src.db.organizations import Organization
from src.services.payments.irankish.client import is_irankish_enabled
from src.services.payments.pricing import read_course_price
from src.services.payments.zarinpal.client import is_zarinpal_enabled

logger = logging.getLogger(__name__)

router = APIRouter()


class ActiveGateways(BaseModel):
    """Which payment gateways this instance has configured (is "active")."""

    zarinpal: bool
    irankish: bool
    # The list form is convenient for a UI that renders one choice per entry.
    active: list[str]


class CoursePaymentOptions(BaseModel):
    """What a buyer needs to choose a gateway and see what they'll pay.

    ``price_irr`` is the single shared price every active gateway charges; it is
    ``None`` when the course is not for sale. ``gateways`` lists the gateway ids
    the buyer may pick from (only active ones). ``purchasable`` is a convenience
    flag: true only when there is a price AND at least one active gateway.
    """

    course_id: int
    price_irr: Optional[int]
    gateways: list[str]
    purchasable: bool


def _active_gateways() -> ActiveGateways:
    zarinpal = is_zarinpal_enabled()
    irankish = is_irankish_enabled()
    active = [
        name
        for name, on in (("zarinpal", zarinpal), ("irankish", irankish))
        if on
    ]
    return ActiveGateways(zarinpal=zarinpal, irankish=irankish, active=active)


@router.get(
    "/gateways",
    response_model=ActiveGateways,
    summary="Which payment gateways are active on this instance",
    description=(
        "Public probe. Returns the gateways configured on this instance so a "
        "buyer's UI can offer only the usable ones. Reveals no credentials."
    ),
)
async def api_active_gateways() -> ActiveGateways:
    return _active_gateways()


@router.get(
    "/{org_id}/courses/{course_id}/options",
    response_model=CoursePaymentOptions,
    summary="A course's price and the gateways it can be bought through",
    description=(
        "Returns the single shared IRR price a course sells for and the active "
        "gateways a buyer may pay through. The price is the same for every "
        "gateway; the buyer picks one and calls that gateway's /checkout."
    ),
    responses={
        200: {"description": "Payment options", "model": CoursePaymentOptions},
        404: {"description": "Organization or course not found"},
    },
)
async def api_course_payment_options(
    request: Request,
    org_id: int,
    course_id: int,
    db_session: AsyncSession = Depends(get_db_session),
) -> CoursePaymentOptions:
    org = (
        await db_session.execute(
            select(Organization).where(Organization.id == org_id)
        )
    ).scalars().first()
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")

    course = (
        await db_session.execute(
            select(Course).where(Course.id == course_id, Course.org_id == org_id)
        )
    ).scalars().first()
    if not course:
        raise HTTPException(status_code=404, detail="Course not found")

    price = read_course_price(course)
    gateways = _active_gateways().active
    return CoursePaymentOptions(
        course_id=course_id,
        price_irr=price,
        gateways=gateways,
        purchasable=price is not None and len(gateways) > 0,
    )
