"""
REST endpoints for course-purchase discount codes.

Mounted in ``src/router.py`` under ``/payments/discounts`` so the full paths are
``/api/v1/payments/discounts/...``.

Endpoints:
  GET    /{org_id}                                   admin: list codes
  GET    /{org_id}/report                            admin: per-code usage totals
  GET    /{org_id}/redemptions                       admin: payments that used a code
  POST   /{org_id}                                   admin: create a code
  PUT    /{org_id}/{code_id}                         admin: activate / limit a code
  DELETE /{org_id}/{code_id}                         admin: delete a code
  POST   /{org_id}/courses/{course_id}/validate      buyer: preview a discounted price
"""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from src.core.events.database import get_db_session
from src.db.courses.courses import Course
from src.db.discount_codes import (
    DiscountCodeCreate,
    DiscountCodeRead,
    DiscountCodeUpdate,
    DiscountRedemption,
    DiscountReport,
    DiscountValidateRequest,
    DiscountValidateResponse,
)
from src.db.users import PublicUser
from src.security.auth import get_current_user, resolve_acting_user_id
from src.security.rbac.rbac import authorization_verify_if_user_is_anon
from src.services.payments.discounts import (
    create_discount_code,
    delete_discount_code,
    get_discount_report,
    list_discount_codes,
    list_redemptions,
    resolve_discount,
    update_discount_code,
)
from src.services.payments.pricing import read_course_price

router = APIRouter()


@router.get("/{org_id}", response_model=list[DiscountCodeRead], summary="List discount codes")
async def api_list_discount_codes(
    request: Request,
    org_id: int,
    current_user: PublicUser = Depends(get_current_user),
    db_session: AsyncSession = Depends(get_db_session),
) -> list[DiscountCodeRead]:
    return await list_discount_codes(request, db_session, org_id, current_user)


@router.get("/{org_id}/report", response_model=DiscountReport, summary="Discount code usage report")
async def api_discount_report(
    request: Request,
    org_id: int,
    current_user: PublicUser = Depends(get_current_user),
    db_session: AsyncSession = Depends(get_db_session),
) -> DiscountReport:
    return await get_discount_report(request, db_session, org_id, current_user)


@router.get(
    "/{org_id}/redemptions",
    response_model=list[DiscountRedemption],
    summary="Payments that used a discount code",
)
async def api_discount_redemptions(
    request: Request,
    org_id: int,
    code_id: Optional[int] = None,
    include_incomplete: bool = False,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    current_user: PublicUser = Depends(get_current_user),
    db_session: AsyncSession = Depends(get_db_session),
) -> list[DiscountRedemption]:
    return await list_redemptions(
        request, db_session, org_id, current_user,
        code_id=code_id, include_incomplete=include_incomplete,
        limit=limit, offset=offset,
    )


@router.post("/{org_id}", response_model=DiscountCodeRead, summary="Create a discount code")
async def api_create_discount_code(
    request: Request,
    org_id: int,
    body: DiscountCodeCreate,
    current_user: PublicUser = Depends(get_current_user),
    db_session: AsyncSession = Depends(get_db_session),
) -> DiscountCodeRead:
    return await create_discount_code(request, db_session, org_id, body, current_user)


@router.put("/{org_id}/{code_id}", response_model=DiscountCodeRead, summary="Update a discount code")
async def api_update_discount_code(
    request: Request,
    org_id: int,
    code_id: int,
    body: DiscountCodeUpdate,
    current_user: PublicUser = Depends(get_current_user),
    db_session: AsyncSession = Depends(get_db_session),
) -> DiscountCodeRead:
    return await update_discount_code(
        request, db_session, org_id, code_id, body, current_user
    )


@router.delete("/{org_id}/{code_id}", summary="Delete a discount code")
async def api_delete_discount_code(
    request: Request,
    org_id: int,
    code_id: int,
    current_user: PublicUser = Depends(get_current_user),
    db_session: AsyncSession = Depends(get_db_session),
) -> dict:
    return await delete_discount_code(request, db_session, org_id, code_id, current_user)


@router.post(
    "/{org_id}/courses/{course_id}/validate",
    response_model=DiscountValidateResponse,
    summary="Preview the price of a course with a discount code applied",
    description=(
        "Authenticated (to limit code guessing). Returns valid=false with a "
        "generic message for any unusable code. Checkout re-validates the code, "
        "so this is a preview only."
    ),
)
async def api_validate_discount(
    request: Request,
    org_id: int,
    course_id: int,
    body: DiscountValidateRequest,
    current_user: PublicUser = Depends(get_current_user),
    db_session: AsyncSession = Depends(get_db_session),
) -> DiscountValidateResponse:
    user_id = resolve_acting_user_id(current_user)
    await authorization_verify_if_user_is_anon(user_id)

    course = (
        await db_session.execute(
            select(Course).where(Course.id == course_id, Course.org_id == org_id)
        )
    ).scalars().first()
    if not course:
        raise HTTPException(status_code=404, detail="Course not found")
    price = read_course_price(course)
    if price is None:
        raise HTTPException(status_code=400, detail="This course is not available for purchase.")

    try:
        resolved = await resolve_discount(
            db_session,
            org_id=org_id,
            course=course,
            price=price,
            raw_code=body.code,
            user_id=user_id,
        )
    except HTTPException as exc:
        if exc.status_code == 400:
            return DiscountValidateResponse(valid=False, detail=str(exc.detail))
        raise
    return DiscountValidateResponse(
        valid=True,
        code=resolved.discount.code,
        original_amount=resolved.original_amount,
        discount_amount=resolved.discount_amount,
        final_amount=resolved.final_amount,
    )
