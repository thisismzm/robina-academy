"""
Tests for the shared, gateway-agnostic course pricing and the active-gateways /
course-options logic.

These cover the two guarantees of the shared-pricing feature:
  1. A course sells for ONE price across every gateway (set once, read the same
     value everywhere), with a fallback to the legacy per-gateway keys.
  2. The payment-options endpoint reports the shared price plus only the active
     (configured) gateways a buyer may choose from at payment time.

The DB is the in-memory SQLite from conftest; gateway "active" state is faked by
patching the two ``is_*_enabled`` probes.
"""

from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import HTTPException

from src.routers.payments import gateways as gw
from src.services.payments.pricing import (
    COURSE_PRICE_METADATA_KEY,
    read_course_price,
    set_course_price,
)


# ---------------------------------------------------------------------------
# read_course_price: shared key + legacy fallback
# ---------------------------------------------------------------------------


def test_read_course_price_uses_shared_key():
    c = SimpleNamespace(extra_metadata={COURSE_PRICE_METADATA_KEY: 150000})
    assert read_course_price(c) == 150000


def test_read_course_price_falls_back_to_legacy_keys():
    # A course priced under the old per-gateway scheme is still readable.
    for legacy in ("zarinpal_price_irr", "irankish_price_irr"):
        c = SimpleNamespace(extra_metadata={legacy: 90000})
        assert read_course_price(c) == 90000


def test_read_course_price_prefers_shared_over_legacy():
    c = SimpleNamespace(
        extra_metadata={
            COURSE_PRICE_METADATA_KEY: 120000,
            "zarinpal_price_irr": 5,
            "irankish_price_irr": 7,
        }
    )
    assert read_course_price(c) == 120000


@pytest.mark.parametrize("bad", [None, 0, -1, "abc", {}])
def test_read_course_price_rejects_non_positive_or_invalid(bad):
    c = SimpleNamespace(extra_metadata={COURSE_PRICE_METADATA_KEY: bad})
    assert read_course_price(c) is None


def test_read_course_price_none_when_no_metadata():
    assert read_course_price(SimpleNamespace(extra_metadata=None)) is None


# ---------------------------------------------------------------------------
# set_course_price: one value for all gateways, clears legacy keys
# ---------------------------------------------------------------------------


async def test_set_course_price_writes_shared_key_and_clears_legacy(
    db, org, admin_user, course, mock_request
):
    # Seed the course with divergent legacy per-gateway prices.
    course.extra_metadata = {"zarinpal_price_irr": 10, "irankish_price_irr": 20}
    db.add(course)
    await db.commit()

    await set_course_price(mock_request, db, org.id, course.id, 300000, admin_user)
    await db.refresh(course)

    meta = course.extra_metadata or {}
    # Shared key holds the single price; the legacy keys are gone so the two
    # gateways can never disagree again.
    assert meta[COURSE_PRICE_METADATA_KEY] == 300000
    assert "zarinpal_price_irr" not in meta
    assert "irankish_price_irr" not in meta
    # And both gateways read the same amount.
    assert read_course_price(course) == 300000


async def test_set_course_price_clear_removes_all_price_keys(
    db, org, admin_user, course, mock_request
):
    await set_course_price(mock_request, db, org.id, course.id, 100000, admin_user)
    await set_course_price(mock_request, db, org.id, course.id, None, admin_user)
    await db.refresh(course)

    meta = course.extra_metadata or {}
    assert COURSE_PRICE_METADATA_KEY not in meta
    assert read_course_price(course) is None


async def test_set_course_price_rejects_non_positive(
    db, org, admin_user, course, mock_request
):
    with pytest.raises(HTTPException) as ei:
        await set_course_price(mock_request, db, org.id, course.id, 0, admin_user)
    assert ei.value.status_code == 400


# ---------------------------------------------------------------------------
# Active gateways + course options
# ---------------------------------------------------------------------------


def _patch_gateways(zarinpal: bool, irankish: bool):
    return (
        patch.object(gw, "is_zarinpal_enabled", return_value=zarinpal),
        patch.object(gw, "is_irankish_enabled", return_value=irankish),
    )


def test_active_gateways_lists_only_enabled():
    p1, p2 = _patch_gateways(zarinpal=True, irankish=False)
    with p1, p2:
        res = gw._active_gateways()
    assert res.zarinpal is True
    assert res.irankish is False
    assert res.active == ["zarinpal"]


def test_active_gateways_empty_when_none_configured():
    p1, p2 = _patch_gateways(zarinpal=False, irankish=False)
    with p1, p2:
        res = gw._active_gateways()
    assert res.active == []


async def test_course_options_reports_price_and_active_gateways(
    db, org, admin_user, course, mock_request
):
    await set_course_price(mock_request, db, org.id, course.id, 250000, admin_user)

    p1, p2 = _patch_gateways(zarinpal=True, irankish=True)
    with p1, p2:
        opts = await gw.api_course_payment_options(mock_request, org.id, course.id, db)

    assert opts.price_irr == 250000
    assert sorted(opts.gateways) == ["irankish", "zarinpal"]
    assert opts.purchasable is True


async def test_course_options_not_purchasable_without_price(
    db, org, course, mock_request
):
    p1, p2 = _patch_gateways(zarinpal=True, irankish=True)
    with p1, p2:
        opts = await gw.api_course_payment_options(mock_request, org.id, course.id, db)

    assert opts.price_irr is None
    assert opts.purchasable is False


async def test_course_options_not_purchasable_without_active_gateway(
    db, org, admin_user, course, mock_request
):
    await set_course_price(mock_request, db, org.id, course.id, 250000, admin_user)

    p1, p2 = _patch_gateways(zarinpal=False, irankish=False)
    with p1, p2:
        opts = await gw.api_course_payment_options(mock_request, org.id, course.id, db)

    # Price is set, but with no active gateway the course cannot be bought.
    assert opts.price_irr == 250000
    assert opts.gateways == []
    assert opts.purchasable is False


async def test_course_options_404_for_missing_course(db, org, mock_request):
    with pytest.raises(HTTPException) as ei:
        await gw.api_course_payment_options(mock_request, org.id, 999999, db)
    assert ei.value.status_code == 404
