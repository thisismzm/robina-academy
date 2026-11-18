import json
import logging
import re
import secrets
import string
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

import redis
from fastapi import HTTPException, Request
from pydantic import EmailStr
from sqlalchemy import func
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from config.config import get_learnhouse_config
from src.db.invite_code_usage import InviteCodeUsage
from src.db.organization_config import OrganizationConfig
from src.db.organizations import Organization, OrganizationRead
from src.db.usergroups import UserGroup
from src.db.users import AnonymousUser, PublicUser, UserRead
from src.services.orgs.orgs import (
    rbac_check,
)
from src.services.users.emails import send_invitation_email

logger = logging.getLogger(__name__)

MIN_CUSTOM_CODE_LENGTH = 4
MAX_CUSTOM_CODE_LENGTH = 32

_redis_pool: Optional[redis.ConnectionPool] = None

def _get_redis(redis_conn_string: str) -> redis.Redis:
    global _redis_pool
    if _redis_pool is None:
        _redis_pool = redis.ConnectionPool.from_url(redis_conn_string, max_connections=10)
    return redis.Redis(connection_pool=_redis_pool)


# Lua script: atomically count existing org invite codes and add a new one if
# the per-org limit has not been reached.  Runs as a single atomic unit on the
# Redis server, eliminating the check-then-set race condition.
#
# KEYS[1] = glob pattern for existing codes   (e.g. "org_invite_code_*:org:<uuid>:code:*")
# KEYS[2] = the new key to write
# ARGV[1] = JSON value to store
# ARGV[2] = TTL in seconds
# ARGV[3] = maximum number of codes allowed
#
# Returns 1 on success, 0 when the limit is already reached.
_LUA_ATOMIC_INVITE = (
    "local pattern = KEYS[1]\n"
    "local new_key = KEYS[2]\n"
    "local new_value = ARGV[1]\n"
    "local ttl = tonumber(ARGV[2])\n"
    "local max_codes = tonumber(ARGV[3])\n"
    "local existing = redis.call('KEYS', pattern)\n"
    "if #existing >= max_codes then\n"
    "    return 0\n"
    "end\n"
    "redis.call('SET', new_key, new_value, 'EX', ttl)\n"
    "return 1\n"
)


async def create_invite_code(
    request: Request,
    org_id: int,
    current_user: PublicUser | AnonymousUser,
    db_session: AsyncSession,
    usergroup_id: Optional[int] = None,
    custom_code: Optional[str] = None,
):
    # Every visitor to the shared demo holds admin on it, so without this an
    # invite code minted there is a way for anyone to have the platform email
    # arbitrary strangers on our domain. This is the abuse vector the demo
    # would otherwise open, and it is blocked at the service rather than in
    # the UI for that reason.
    from src.services.demo.guards import require_not_demo_org

    await require_not_demo_org(org_id, db_session)

    # Redis init
    LH_CONFIG = get_learnhouse_config()
    redis_conn_string = LH_CONFIG.redis_config.redis_connection_string

    if not redis_conn_string:
        raise HTTPException(
            status_code=500,
            detail="Redis connection string not found",
        )

    statement = select(Organization).where(Organization.id == org_id)
    org = (await db_session.execute(statement)).scalars().first()

    if not org:
        raise HTTPException(
            status_code=404,
            detail="Organization not found",
        )

    # RBAC check
    await rbac_check(request, org.org_uuid, current_user, "update", db_session)

    # Free-tier abuse gate: shareable invite links are a self-service member
    # onboarding primitive, so a free org (and the acting user) must be at
    # least FREE_TIER_MIN_AGE_DAYS old before minting one. Paid orgs exempt.
    from src.security.auth import resolve_acting_user_id
    from src.services.security.account_age import enforce_free_tier_age_gate

    await enforce_free_tier_age_gate(
        org_id, resolve_acting_user_id(current_user), db_session,
        action="create invite links",
    )

    # Connect to Redis
    r = _get_redis(redis_conn_string)

    if not r:
        raise HTTPException(
            status_code=500,
            detail="Could not connect to Redis",
        )

    # Validate usergroup exists if provided
    if usergroup_id is not None:
        statement = select(UserGroup).where(
            UserGroup.id == usergroup_id,
            UserGroup.org_id == org_id,
        )
        usergroup = (await db_session.execute(statement)).scalars().first()
        if not usergroup:
            raise HTTPException(
                status_code=404,
                detail="UserGroup not found or does not belong to this organization",
            )

    # Generate invite code using cryptographically secure random
    def generate_code(length=8):
        alphabet = string.ascii_letters + string.digits
        return "".join(secrets.choice(alphabet) for _ in range(length))

    if custom_code is not None and custom_code.strip():
        generated_invite_code = custom_code.strip()
        # Letters and digits only: get_invite_code rejects anything else, and
        # the code is interpolated into a Redis key/glob pattern.
        if not (
            generated_invite_code.isascii()
            and generated_invite_code.isalnum()
            and MIN_CUSTOM_CODE_LENGTH <= len(generated_invite_code) <= MAX_CUSTOM_CODE_LENGTH
        ):
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Invite code must be {MIN_CUSTOM_CODE_LENGTH}-{MAX_CUSTOM_CODE_LENGTH} "
                    "characters, letters and numbers only"
                ),
            )
        # Codes are looked up by value, so they must be unique within the org.
        for _ in r.scan_iter(
            match=f"org_invite_code_*:org:{org.org_uuid}:code:{generated_invite_code}",
            count=100,
        ):
            raise HTTPException(
                status_code=409,
                detail="This invite code already exists",
            )
    else:
        generated_invite_code = generate_code()
    invite_code_uuid = f"org_invite_code_{uuid.uuid4()}"

    # time to live in days to seconds
    ttl = int(timedelta(days=365).total_seconds())

    inviteCodeObject = {
        "invite_code": generated_invite_code,
        "invite_code_uuid": invite_code_uuid,
        "invite_code_expires": ttl,
        "invite_code_type": "signup",
        "created_at": datetime.now().isoformat(),
        "created_by": current_user.user_uuid,
    }

    if usergroup_id is not None:
        inviteCodeObject["usergroup_id"] = usergroup_id

    new_invite_key = f"{invite_code_uuid}:org:{org.org_uuid}:code:{generated_invite_code}"
    invite_value = json.dumps(inviteCodeObject)

    # Atomically check the per-org code limit and write the new key.
    # _LUA_ATOMIC_INVITE returns 0 when the limit is reached, 1 on success.
    result = r.eval(  # type: ignore[attr-defined]
        _LUA_ATOMIC_INVITE,
        2,
        f"org_invite_code_*:org:{org.org_uuid}:code:*",
        new_invite_key,
        invite_value,
        str(ttl),
        "6",
    )

    if result == 0:
        raise HTTPException(
            status_code=400,
            detail="Maximum number of invite codes reached",
        )

    return inviteCodeObject


async def get_invite_codes(
    request: Request,
    org_id: int,
    current_user: PublicUser | AnonymousUser,
    db_session: AsyncSession,
):
    # Redis init
    LH_CONFIG = get_learnhouse_config()
    redis_conn_string = LH_CONFIG.redis_config.redis_connection_string

    if not redis_conn_string:
        raise HTTPException(
            status_code=500,
            detail="Redis connection string not found",
        )

    statement = select(Organization).where(Organization.id == org_id)
    org = (await db_session.execute(statement)).scalars().first()

    if not org:
        raise HTTPException(
            status_code=404,
            detail="Organization not found",
        )

    # RBAC check
    await rbac_check(request, org.org_uuid, current_user, "update", db_session)

    # Connect to Redis
    r = _get_redis(redis_conn_string)

    if not r:
        raise HTTPException(
            status_code=500,
            detail="Could not connect to Redis",
        )

    # Get invite codes (use scan_iter to avoid blocking Redis)
    invite_codes = list(r.scan_iter(match=f"org_invite_code_*:org:{org.org_uuid}:code:*", count=100))

    invite_codes_list = []

    for invite_code in invite_codes:  # type: ignore
        invite_code = r.get(invite_code)
        # The key may have expired between scan_iter() and get() (codes carry a
        # TTL). r.get() then returns None and json.loads(None) raises TypeError,
        # crashing the whole listing with a 500. Skip vanished keys instead.
        if invite_code is None:
            continue
        invite_code = json.loads(invite_code)  # type: ignore

        # Enrich with usergroup name if linked
        if invite_code.get("usergroup_id"):
            statement = select(UserGroup).where(
                UserGroup.id == invite_code["usergroup_id"]
            )
            usergroup = (await db_session.execute(statement)).scalars().first()
            invite_code["usergroup_name"] = usergroup.name if usergroup else None

        invite_codes_list.append(invite_code)

    return invite_codes_list


async def get_invite_code(
    request: Request,
    org_id: int,
    invite_code: str,
    current_user: PublicUser | AnonymousUser,
    db_session: AsyncSession,
):
    # Redis init
    LH_CONFIG = get_learnhouse_config()
    redis_conn_string = LH_CONFIG.redis_config.redis_connection_string

    if not redis_conn_string:
        raise HTTPException(
            status_code=500,
            detail="Redis connection string not found",
        )

    statement = select(Organization).where(Organization.id == org_id)
    org = (await db_session.execute(statement)).scalars().first()

    if not org:
        raise HTTPException(
            status_code=404,
            detail="Organization not found",
        )

    # RBAC check - verify user has permission to view invite codes for this org
    await rbac_check(request, org.org_uuid, current_user, "read", db_session)

    # Connect to Redis
    r = _get_redis(redis_conn_string)

    if not r:
        raise HTTPException(
            status_code=500,
            detail="Could not connect to Redis",
        )

    # SECURITY: Validate invite code is alphanumeric to prevent Redis wildcard injection
    if not invite_code.isalnum():
        raise HTTPException(
            status_code=404,
            detail="Invite code not found",
        )

    # Get invite code (use scan_iter to avoid blocking Redis)
    matched_key = None
    for key in r.scan_iter(match=f"org_invite_code_*:org:{org.org_uuid}:code:{invite_code}", count=10):
        matched_key = key
        break

    if not matched_key:
        raise HTTPException(
            status_code=404,
            detail="Invite code not found",
        )

    invite_code_value = r.get(matched_key)
    invite_code_data = json.loads(invite_code_value)

    return invite_code_data


async def delete_invite_code(
    request: Request,
    org_id: int,
    invite_code_uuid: str,
    current_user: PublicUser | AnonymousUser,
    db_session: AsyncSession,
):
    # Redis init
    LH_CONFIG = get_learnhouse_config()
    redis_conn_string = LH_CONFIG.redis_config.redis_connection_string

    if not redis_conn_string:
        raise HTTPException(
            status_code=500,
            detail="Redis connection string not found",
        )

    statement = select(Organization).where(Organization.id == org_id)
    org = (await db_session.execute(statement)).scalars().first()

    if not org:
        raise HTTPException(
            status_code=404,
            detail="Organization not found",
        )

    # RBAC check
    await rbac_check(request, org.org_uuid, current_user, "update", db_session)

    # Connect to Redis
    r = _get_redis(redis_conn_string)

    if not r:
        raise HTTPException(
            status_code=500,
            detail="Could not connect to Redis",
        )

    # SECURITY: the UUID is interpolated into a Redis SCAN glob pattern. Without
    # validation an admin could pass wildcard characters (e.g. "*") to match,
    # and delete, every invite code key for the org in a single call, instead
    # of the one resource the endpoint is meant to address. Restrict to the
    # exact "org_invite_code_<uuid4>" shape this codebase generates.
    if not re.fullmatch(r"org_invite_code_[0-9a-fA-F-]{36}", invite_code_uuid):
        raise HTTPException(
            status_code=404,
            detail="Invite code not found",
        )

    # Delete invite code (use scan_iter to avoid blocking Redis)
    keys = list(r.scan_iter(match=f"{invite_code_uuid}:org:{org.org_uuid}:code:*", count=10))
    if keys:
        r.delete(*keys)

    if not keys:
        raise HTTPException(
            status_code=404,
            detail="Invite code not found",
        )

    return keys


async def send_invite_email(
    org: OrganizationRead,
    invite_code_uuid: str | None,
    user: UserRead,
    email: EmailStr,
    request: Request,
    db_session=None,
):
    invite_code = None

    # Look up the invite code from Redis if a UUID was provided
    if invite_code_uuid:
        LH_CONFIG = get_learnhouse_config()
        redis_conn_string = LH_CONFIG.redis_config.redis_connection_string

        if redis_conn_string:
            r = _get_redis(redis_conn_string)
            matched = list(r.scan_iter(match=f"{invite_code_uuid}:org:{org.org_uuid}:code:*", count=10))  # type: ignore
            if matched:
                data = r.get(matched[0])
                if data:
                    invite_code = json.loads(data).get("invite_code")

    # Build signup URL rooted at the org's own frontend subdomain (or primary
    # verified custom domain if one is configured; passing db_session opts in).
    from src.services.email.utils import get_org_signup_base_url
    org_base_url = await get_org_signup_base_url(
        org.slug, request, db_session=db_session, org_id=org.id
    )

    if invite_code:
        signup_url = f"{org_base_url}/signup?inviteCode={invite_code}"
    else:
        signup_url = f"{org_base_url}/signup"

    from src.services.email.branding import resolve_org_email_branding

    org_config = None
    if db_session is not None:
        try:
            org_config_stmt = select(OrganizationConfig).where(OrganizationConfig.org_id == org.id)
            org_config = (await db_session.execute(org_config_stmt)).scalars().first()
        except Exception:
            pass
    branding = resolve_org_email_branding(org, org_config, request)

    try:
        # Defense in depth: scrub any link out of the inviter's display name
        # before it is relayed to recipients, even if it slipped past
        # write-time validation (e.g. imported via OAuth).
        from src.services.security.profile_validation import sanitize_display_name

        result = send_invitation_email(
            email=email,
            org_name=sanitize_display_name(org.name, fallback="A LearnHouse organization"),
            inviter_username=sanitize_display_name(user.username),
            invite_code=invite_code,
            signup_url=signup_url,
            **branding.as_kwargs(),
        )
        return result is not None
    except Exception:
        logger.exception("Failed to send invite email to %s", email)
        return False


async def record_invite_code_usage(
    db_session: AsyncSession,
    org_id: int,
    user_id: int,
    invite_code_data: dict,
) -> None:
    """Remember which invite code a user joined with.

    Never raises: stats are secondary, and a failure here must not undo a
    signup that already succeeded. Idempotent per (org, user).
    """
    try:
        existing = (
            await db_session.execute(
                select(InviteCodeUsage.id).where(
                    InviteCodeUsage.org_id == org_id,
                    InviteCodeUsage.user_id == user_id,
                )
            )
        ).first()
        if existing:
            return
        usergroup_id = invite_code_data.get("usergroup_id")
        db_session.add(
            InviteCodeUsage(
                org_id=org_id,
                user_id=user_id,
                invite_code_uuid=str(invite_code_data.get("invite_code_uuid", "")),
                code=str(invite_code_data.get("invite_code", "")),
                usergroup_id=int(usergroup_id) if usergroup_id else None,
            )
        )
        await db_session.commit()
    except Exception:
        await db_session.rollback()
        logger.warning("Could not record invite code usage", exc_info=True)


async def get_invite_code_stats(
    request: Request,
    org_id: int,
    current_user: PublicUser | AnonymousUser,
    db_session: AsyncSession,
    days: int = 30,
):
    """Per-code signup statistics for an organization (admin only)."""
    days = max(1, min(days, 365))

    # Same permission as listing the codes; also returns the live codes.
    live_codes = await get_invite_codes(request, org_id, current_user, db_session)
    live_by_uuid = {c["invite_code_uuid"]: c for c in live_codes}

    since = datetime.now(timezone.utc) - timedelta(days=days)

    per_code_rows = (
        await db_session.execute(
            select(
                InviteCodeUsage.invite_code_uuid,
                func.max(InviteCodeUsage.code),
                func.count(InviteCodeUsage.id),
                func.max(InviteCodeUsage.signed_up_at),
                func.count(InviteCodeUsage.id).filter(InviteCodeUsage.signed_up_at >= since),
            )
            .where(InviteCodeUsage.org_id == org_id)
            .group_by(InviteCodeUsage.invite_code_uuid)
        )
    ).all()
    usage_by_uuid = {row[0]: row for row in per_code_rows}

    codes = []
    for code_uuid, live in live_by_uuid.items():
        row = usage_by_uuid.get(code_uuid)
        codes.append(
            {
                "invite_code_uuid": code_uuid,
                "code": live.get("invite_code"),
                "status": "active",
                "usergroup_id": live.get("usergroup_id"),
                "usergroup_name": live.get("usergroup_name"),
                "created_at": live.get("created_at"),
                "total_signups": row[2] if row else 0,
                "signups_in_period": row[4] if row else 0,
                "last_used": row[3].isoformat() if row and row[3] else None,
            }
        )
    # Codes that were deleted or expired but still have recorded signups.
    for code_uuid, row in usage_by_uuid.items():
        if code_uuid in live_by_uuid:
            continue
        codes.append(
            {
                "invite_code_uuid": code_uuid,
                "code": row[1],
                "status": "deleted",
                "usergroup_id": None,
                "usergroup_name": None,
                "created_at": None,
                "total_signups": row[2],
                "signups_in_period": row[4],
                "last_used": row[3].isoformat() if row[3] else None,
            }
        )
    codes.sort(key=lambda c: (c["total_signups"], c["code"] or ""), reverse=True)

    # Bucket by day in Python (portable across databases); rows are bounded by
    # the requested window.
    series_rows = (
        await db_session.execute(
            select(InviteCodeUsage.signed_up_at, InviteCodeUsage.invite_code_uuid)
            .where(
                InviteCodeUsage.org_id == org_id,
                InviteCodeUsage.signed_up_at >= since,
            )
            .order_by(InviteCodeUsage.signed_up_at)  # type: ignore[arg-type]
        )
    ).all()
    timeseries: dict[str, dict] = {}
    for signed_up_at, code_uuid in series_rows:
        key = signed_up_at.date().isoformat()
        entry = timeseries.setdefault(key, {"date": key, "total": 0, "by_code": {}})
        entry["total"] += 1
        entry["by_code"][code_uuid] = entry["by_code"].get(code_uuid, 0) + 1

    # Members who joined in the same window, to compare invite vs. other joins.
    from src.db.user_organizations import UserOrganization

    # creation_date is stored as str(datetime.now()), so compare as text.
    cutoff = str(datetime.now() - timedelta(days=days))
    new_members = (
        await db_session.execute(
            select(func.count(UserOrganization.id)).where(
                UserOrganization.org_id == org_id,
                UserOrganization.creation_date >= cutoff,
            )
        )
    ).scalar() or 0
    via_invite = sum(c["signups_in_period"] for c in codes)

    return {
        "period_days": days,
        "summary": {
            "signups_via_invite": via_invite,
            "new_members": max(new_members, via_invite),
            "active_codes": len(live_by_uuid),
            "total_signups_all_time": sum(c["total_signups"] for c in codes),
        },
        "codes": codes,
        "timeseries": list(timeseries.values()),
    }


async def get_invite_code_usage_users(
    request: Request,
    org_id: int,
    invite_code_uuid: str,
    current_user: PublicUser | AnonymousUser,
    db_session: AsyncSession,
    limit: int = 100,
):
    """Users who joined with one invite code (admin only)."""
    from src.db.users import User

    statement = select(Organization).where(Organization.id == org_id)
    org = (await db_session.execute(statement)).scalars().first()
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")
    await rbac_check(request, org.org_uuid, current_user, "update", db_session)

    limit = max(1, min(limit, 500))
    rows = (
        await db_session.execute(
            select(InviteCodeUsage, User)
            .join(User, User.id == InviteCodeUsage.user_id)  # type: ignore[arg-type]
            .where(
                InviteCodeUsage.org_id == org_id,
                InviteCodeUsage.invite_code_uuid == invite_code_uuid,
            )
            .order_by(InviteCodeUsage.signed_up_at.desc())  # type: ignore[attr-defined]
            .limit(limit)
        )
    ).all()
    return [
        {
            "user_uuid": user.user_uuid,
            "username": user.username,
            "first_name": user.first_name,
            "last_name": user.last_name,
            "email": user.email,
            "signed_up_at": usage.signed_up_at.isoformat(),
            "usergroup_id": usage.usergroup_id,
        }
        for usage, user in rows
    ]
