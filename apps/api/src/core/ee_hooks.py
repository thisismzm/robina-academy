import importlib.util
import logging
import os

logger = logging.getLogger(__name__)

def is_ee_available():
    """
    Check if the Enterprise Edition code is present on disk and not disabled.

    NOTE: This does NOT indicate the deployment mode is 'ee'.
    SaaS deployments also ship with the EE folder present.
    Use get_deployment_mode() from src.core.deployment_mode to determine the actual mode.
    """
    if os.environ.get("LEARNHOUSE_DISABLE_EE") == "1":
        return False
    # Require the hooks module itself, not merely a directory named "ee":
    # a directory without it imports nothing, so it is not an EE install.
    return os.path.isdir("ee") and os.path.isfile(os.path.join("ee", "hooks.py"))

def get_ee_hooks():
    """Safely import and return the EE hooks module if available."""
    if not is_ee_available():
        return None
    
    try:
        # We use importlib to avoid hardcoded top-level imports that might 
        # fail during linting or when the folder is missing.
        spec = importlib.util.find_spec("ee.hooks")
        if spec is None:
            return None
        
        module = importlib.import_module("ee.hooks")
        return module
    except ImportError as e:
        logger.error(f"Failed to import EE hooks: {e}")
        return None
    except Exception as e:
        logger.error(f"Unexpected error loading EE hooks: {e}")
        return None

def register_ee_middlewares(app):
    """Call EE to register its middlewares."""
    from config.config import get_learnhouse_config
    if get_learnhouse_config().general_config.saas_mode:
        return
    hooks = get_ee_hooks()
    if hooks and hasattr(hooks, "register_middlewares"):
        hooks.register_middlewares(app)

def register_ee_routers(v1_router):
    """Call EE to register its routers."""
    hooks = get_ee_hooks()
    if hooks and hasattr(hooks, "register_routers"):
        hooks.register_routers(v1_router)

def run_ee_startup(app):
    """Call EE to run its startup tasks."""
    hooks = get_ee_hooks()
    if hooks and hasattr(hooks, "on_startup"):
        hooks.on_startup(app)

def register_ee_rag_sources():
    """Let EE register extractors for its own content types (e.g. SCORM) with
    the AI search source registry."""
    hooks = get_ee_hooks()
    if hooks and hasattr(hooks, "register_rag_sources"):
        try:
            hooks.register_rag_sources()
        except Exception as e:
            logger.error(f"Failed to register EE AI search sources: {e}")

def is_multi_org_allowed() -> bool:
    """Check if multi-org mode is allowed (requires EE or SaaS)."""
    from src.core.deployment_mode import get_deployment_mode
    mode = get_deployment_mode()
    return mode in ('ee', 'saas')


async def check_ee_activity_paid_access(request, activity_id, user, db_session) -> bool:
    """
    Check if a user has paid access to an activity's course.

    Returns True when access is granted, False when the course is paid and the
    user has not purchased it (so the caller strips the activity content).

    When the EE package is present it owns this decision. Otherwise the
    community edition enforces its own gateway paywall: an activity whose course
    carries a gateway price (ZarinPal / IranKish) is only readable by buyers,
    the course author, and org admins — the exact same rule the RBAC layer
    applies to the course itself, reused here so the served content tree cannot
    leak a paid course's activities to a non-payer.
    """
    hooks = get_ee_hooks()
    if hooks and hasattr(hooks, "check_activity_paid_access"):
        return await hooks.check_activity_paid_access(request, activity_id, user, db_session)

    return await _check_community_activity_paid_access(
        request, activity_id, user, db_session
    )


async def _check_community_activity_paid_access(
    request, activity_id, user, db_session
) -> bool:
    """Community-edition paywall for an activity's parent course.

    A course priced through a payment gateway (its ``extra_metadata`` carries a
    ``course_price_irr``) must not hand its activity content to anyone who has
    not bought it. Buyers hold the course through its per-course purchasers
    UserGroup; the course author and org admins keep access so they can review
    their own material. Everyone else loses the content (the caller then blanks
    it to ``{"paid_access": False}``).

    Returns True (grant) for a free course, and also on any resolution failure,
    so this never accidentally strips a free course's content.
    """
    from sqlmodel import select

    from src.db.courses.activities import Activity
    from src.db.courses.courses import Course
    from src.db.users import AnonymousUser
    from src.security.auth import resolve_acting_user_id
    from src.services.courses.chapters import is_org_admin
    from src.services.payments.pricing import (
        course_is_paid,
        user_has_paid_course_access,
    )

    try:
        activity = (
            await db_session.execute(
                select(Activity).where(Activity.id == activity_id)
            )
        ).scalars().first()
        if activity is None:
            return True

        course = (
            await db_session.execute(
                select(Course).where(Course.id == activity.course_id)
            )
        ).scalars().first()
        if course is None:
            return True

        # Free course: nothing to gate.
        if not course_is_paid(course):
            return True

        # Anonymous visitors never have paid access.
        if isinstance(user, AnonymousUser):
            return False

        user_id = resolve_acting_user_id(user)
        if not user_id:
            return False

        # Course author / org admin keep access to their own material.
        if await is_org_admin(user_id, course.org_id, db_session):
            return True
        if await _is_course_author(db_session, course.course_uuid, user_id):
            return True

        # Buyers hold the course via a completed row in the payment ledger.
        # Whether the course is public or restricted to a UserGroup is a
        # separate, independent concern enforced by the access layer, not here.
        return await user_has_paid_course_access(db_session, user_id, course)
    except Exception as e:  # noqa: BLE001 - never hard-fail the content tree
        logger.error(f"Community paid-access check failed for activity {activity_id}: {e}")
        return True


async def _is_course_author(db_session, course_uuid: str, user_id: int) -> bool:
    """Whether ``user_id`` is an active author/maintainer/contributor of the course."""
    from sqlmodel import select

    from src.db.resource_authors import (
        ResourceAuthor,
        ResourceAuthorshipEnum,
        ResourceAuthorshipStatusEnum,
    )

    row = (
        await db_session.execute(
            select(ResourceAuthor).where(
                ResourceAuthor.resource_uuid == course_uuid,
                ResourceAuthor.user_id == user_id,
            )
        )
    ).scalars().first()
    if not row:
        return False
    return (
        row.authorship
        in (
            ResourceAuthorshipEnum.CREATOR,
            ResourceAuthorshipEnum.MAINTAINER,
            ResourceAuthorshipEnum.CONTRIBUTOR,
        )
        and row.authorship_status == ResourceAuthorshipStatusEnum.ACTIVE
    )

