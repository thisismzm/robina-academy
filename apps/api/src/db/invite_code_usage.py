from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import BigInteger, Column, DateTime, ForeignKey, Index, Integer, UniqueConstraint
from sqlmodel import Field, SQLModel


class InviteCodeUsage(SQLModel, table=True):
    """One row per user who joined an organization with an invite code.

    Invite codes themselves live in Redis and vanish when deleted or expired,
    so the code text is copied here at signup. That keeps usage stats intact
    after a code is gone. The unique (org_id, user_id) constraint makes
    recording idempotent: a user joins an org once, with one code.
    """

    __tablename__ = "invite_code_usage"
    __table_args__ = (
        UniqueConstraint("org_id", "user_id", name="uq_invite_code_usage_org_user"),
        Index("ix_invite_code_usage_org_code", "org_id", "invite_code_uuid"),
        Index("ix_invite_code_usage_org_date", "org_id", "signed_up_at"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    org_id: int = Field(
        sa_column=Column(
            BigInteger,
            ForeignKey("organization.id", ondelete="CASCADE"),
            nullable=False,
        )
    )
    user_id: int = Field(
        sa_column=Column(
            Integer,
            ForeignKey("user.id", ondelete="CASCADE"),
            nullable=False,
        )
    )
    invite_code_uuid: str = Field(max_length=128)
    code: str = Field(max_length=64)
    # No foreign key: the usergroup may be deleted later; this is history.
    usergroup_id: Optional[int] = Field(default=None)
    signed_up_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
