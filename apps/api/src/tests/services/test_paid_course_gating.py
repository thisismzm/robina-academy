"""
Community-edition paywall enforcement.

A course priced through a payment gateway (ZarinPal / IranKish) stores an IRR
amount in ``course.extra_metadata`` and must not hand its activity content to
anyone who has not bought it. These tests exercise the real (non-EE)
``check_ee_activity_paid_access`` path and the shared ``pricing`` helpers that
back it:

  - a free course is never gated;
  - a priced course denies an anonymous visitor and a plain signed-in user;
  - a buyer (a COMPLETED row in the payment ledger) is granted;
  - the course author and an org admin keep access to their own material.

Payment access is decided solely by the ledger here; whether a course is public
or restricted to a UserGroup is an independent concern enforced by the access
layer, not by this paywall.
"""

from datetime import datetime
from unittest.mock import patch

import pytest

from src.core import ee_hooks
from src.db.resource_authors import (
    ResourceAuthor,
    ResourceAuthorshipEnum,
    ResourceAuthorshipStatusEnum,
)
from src.db.usergroup_user import UserGroupUser
from src.db.usergroups import UserGroup
from src.db.users import AnonymousUser
from src.db.zarinpal import ZarinPalPayment, ZarinPalPaymentStatusEnum
from src.services.payments.pricing import (
    COURSE_PRICE_METADATA_KEY,
    course_is_paid,
    user_has_paid_course_access,
)

pytestmark = pytest.mark.asyncio


def _now() -> str:
    return str(datetime.now())


async def _set_price(db, course, price_irr):
    course.extra_metadata = {COURSE_PRICE_METADATA_KEY: price_irr}
    db.add(course)
    await db.commit()
    await db.refresh(course)


async def _add_purchaser(db, org, course, user):
    """Reproduce a completed purchase: a COMPLETED row in the payment ledger.

    Access now derives from the ledger, not from a per-course "purchasers"
    UserGroup, so a settled ``ZarinPalPayment`` is what marks the buyer as
    having paid.
    """
    db.add(ZarinPalPayment(
        payment_uuid="zppay_test_purchaser",
        org_id=org.id,
        user_id=user.id,
        course_id=course.id,
        amount=250000,
        status=ZarinPalPaymentStatusEnum.COMPLETED,
        creation_date=_now(),
        update_date=_now(),
    ))
    await db.commit()


async def _make_author(db, course, user):
    db.add(ResourceAuthor(
        resource_uuid=course.course_uuid,
        user_id=user.id,
        authorship=ResourceAuthorshipEnum.CREATOR,
        authorship_status=ResourceAuthorshipStatusEnum.ACTIVE,
        creation_date=_now(),
        update_date=_now(),
    ))
    await db.commit()


def _no_ee():
    """Force the free-edition code path even if an EE folder is present."""
    return patch.object(ee_hooks, "get_ee_hooks", lambda: None)


# ---------------------------------------------------------------------------
# pricing helpers
# ---------------------------------------------------------------------------


async def test_course_is_paid_reflects_price(db, course):
    assert course_is_paid(course) is False
    await _set_price(db, course, 250000)
    assert course_is_paid(course) is True


async def test_user_has_paid_course_access_requires_completed_payment(
    db, org, course, regular_user
):
    assert await user_has_paid_course_access(db, regular_user.id, course) is False
    await _add_purchaser(db, org, course, regular_user)
    assert await user_has_paid_course_access(db, regular_user.id, course) is True


async def test_anonymous_never_has_paid_access(db, course):
    assert await user_has_paid_course_access(db, 0, course) is False


# ---------------------------------------------------------------------------
# check_ee_activity_paid_access (community path)
# ---------------------------------------------------------------------------


async def test_free_course_grants_everyone(db, activity, regular_user, mock_request):
    with _no_ee():
        granted = await ee_hooks.check_ee_activity_paid_access(
            mock_request, activity.id, regular_user, db
        )
    assert granted is True


async def test_paid_course_denies_plain_user(
    db, course, activity, regular_user, mock_request
):
    await _set_price(db, course, 250000)
    with _no_ee():
        granted = await ee_hooks.check_ee_activity_paid_access(
            mock_request, activity.id, regular_user, db
        )
    assert granted is False


async def test_paid_course_denies_anonymous(db, course, activity, mock_request):
    await _set_price(db, course, 250000)
    with _no_ee():
        granted = await ee_hooks.check_ee_activity_paid_access(
            mock_request, activity.id, AnonymousUser(), db
        )
    assert granted is False


async def test_paid_course_grants_purchaser(
    db, org, course, activity, regular_user, mock_request
):
    await _set_price(db, course, 250000)
    await _add_purchaser(db, org, course, regular_user)
    with _no_ee():
        granted = await ee_hooks.check_ee_activity_paid_access(
            mock_request, activity.id, regular_user, db
        )
    assert granted is True


async def test_paid_course_grants_author(
    db, course, activity, regular_user, mock_request
):
    await _set_price(db, course, 250000)
    await _make_author(db, course, regular_user)
    with _no_ee():
        granted = await ee_hooks.check_ee_activity_paid_access(
            mock_request, activity.id, regular_user, db
        )
    assert granted is True


async def test_paid_course_grants_org_admin(
    db, course, activity, admin_user, mock_request
):
    await _set_price(db, course, 250000)
    with _no_ee():
        granted = await ee_hooks.check_ee_activity_paid_access(
            mock_request, activity.id, admin_user, db
        )
    assert granted is True


async def test_missing_activity_grants_access(db, regular_user, mock_request):
    with _no_ee():
        granted = await ee_hooks.check_ee_activity_paid_access(
            mock_request, 999999, regular_user, db
        )
    assert granted is True


async def test_paid_access_ignores_usergroup_membership(
    db, org, course, activity, regular_user, mock_request
):
    """Payment access is ledger-only. A plain UserGroup membership (not a
    purchase) does NOT grant content on a priced course, and a purchase grants
    it regardless of any group — the two concerns never gate each other."""
    await _set_price(db, course, 250000)

    # In a group but no purchase -> still denied the paid content.
    group = UserGroup(
        name=f"Group: {course.name}",
        description="a group",
        org_id=org.id,
        usergroup_uuid="usergroup_plain",
        creation_date=_now(),
        update_date=_now(),
    )
    db.add(group)
    await db.commit()
    await db.refresh(group)
    db.add(UserGroupUser(
        usergroup_id=group.id,
        user_id=regular_user.id,
        org_id=org.id,
        creation_date=_now(),
        update_date=_now(),
    ))
    await db.commit()
    with _no_ee():
        granted = await ee_hooks.check_ee_activity_paid_access(
            mock_request, activity.id, regular_user, db
        )
    assert granted is False

    # Now purchase -> granted, independent of group membership.
    await _add_purchaser(db, org, course, regular_user)
    with _no_ee():
        granted = await ee_hooks.check_ee_activity_paid_access(
            mock_request, activity.id, regular_user, db
        )
    assert granted is True
