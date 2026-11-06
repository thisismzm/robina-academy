"""
Database model and schemas for course-purchase discount codes.

A discount code belongs to an organization and gives a buyer either a percentage
or a fixed IRR amount off a course price at checkout. It may be restricted to a
single course (``course_id``) or apply to every course in the organization
(``course_id`` is NULL).

Exactly one of ``percent_off`` / ``amount_off`` is set. The discounted amount is
computed server-side at checkout and stored on the payment row, so the gateway
is always charged (and verifies) the discounted amount.
"""

from typing import Optional

from pydantic import BaseModel, Field as PydanticField
from sqlalchemy import (
    Boolean,
    Column,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlmodel import Field, SQLModel


class DiscountCode(SQLModel, table=True):
    __tablename__ = "discount_code"
    __table_args__ = (
        UniqueConstraint("org_id", "code", name="uq_discount_code_org_code"),
        Index("ix_discount_code_org_id", "org_id"),
        Index("ix_discount_code_course_id", "course_id"),
        {"extend_existing": True},
    )

    id: Optional[int] = Field(default=None, primary_key=True)

    org_id: int = Field(
        sa_column=Column(
            Integer, ForeignKey("organization.id", ondelete="CASCADE"), nullable=False
        )
    )
    # NULL = valid for every course in the organization.
    course_id: Optional[int] = Field(
        default=None,
        sa_column=Column(Integer, ForeignKey("course.id", ondelete="CASCADE")),
    )

    # Stored upper-cased and trimmed; matched case-insensitively.
    code: str = Field(sa_column=Column(String(64), nullable=False))

    # Exactly one of these is set.
    percent_off: Optional[int] = Field(default=None, sa_column=Column(Integer))
    amount_off: Optional[int] = Field(default=None, sa_column=Column(Integer))

    # NULL = unlimited. ``redemption_count`` is bumped when a payment COMPLETES.
    max_redemptions: Optional[int] = Field(default=None, sa_column=Column(Integer))
    redemption_count: int = Field(
        default=0, sa_column=Column(Integer, nullable=False, server_default="0")
    )

    # True (default): each buyer may redeem this code once. False: the same buyer
    # may use it on several purchases (still bounded by ``max_redemptions``).
    single_use_per_user: bool = Field(
        default=True,
        sa_column=Column(Boolean, nullable=False, server_default="true"),
    )

    # ISO-8601 timestamp string (same string-date convention as the rest of the
    # codebase). NULL = never expires.
    expires_at: Optional[str] = Field(default=None, sa_column=Column(String(40)))

    is_active: bool = Field(
        default=True, sa_column=Column(Boolean, nullable=False, server_default="true")
    )

    creation_date: str = ""
    update_date: str = ""


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------


class DiscountCodeCreate(BaseModel):
    code: str = PydanticField(min_length=3, max_length=64)
    course_id: Optional[int] = None
    percent_off: Optional[int] = PydanticField(default=None, ge=1, le=99)
    amount_off: Optional[int] = PydanticField(default=None, ge=1)
    max_redemptions: Optional[int] = PydanticField(default=None, ge=1)
    expires_at: Optional[str] = None
    single_use_per_user: bool = True


class DiscountCodeUpdate(BaseModel):
    is_active: Optional[bool] = None
    single_use_per_user: Optional[bool] = None
    max_redemptions: Optional[int] = PydanticField(default=None, ge=1)
    expires_at: Optional[str] = None
    # Set to true to clear the corresponding limit (None alone means "unchanged").
    clear_max_redemptions: bool = False
    clear_expires_at: bool = False


class DiscountCodeRead(BaseModel):
    id: int
    org_id: int
    course_id: Optional[int] = None
    code: str
    percent_off: Optional[int] = None
    amount_off: Optional[int] = None
    max_redemptions: Optional[int] = None
    redemption_count: int
    expires_at: Optional[str] = None
    single_use_per_user: bool = True
    is_active: bool
    creation_date: str
    update_date: str


class DiscountValidateRequest(BaseModel):
    code: str


class DiscountValidateResponse(BaseModel):
    valid: bool
    code: Optional[str] = None
    original_amount: Optional[int] = None
    discount_amount: Optional[int] = None
    final_amount: Optional[int] = None
    detail: Optional[str] = None
