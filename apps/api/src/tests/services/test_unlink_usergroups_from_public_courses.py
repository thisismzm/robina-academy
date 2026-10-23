"""Tests for the one-shot data migration that resolves courses which are both
public AND restricted to a UserGroup.

The shipped script (migrations/unlink_usergroups_from_public_courses.py) runs
against a synchronous Session; the suite's DB is async. To exercise the exact
query and resolution policy without duplicating intent, this test rebuilds the
same selection the script uses (public course JOINed to its UserGroupResource
links) and applies the same policy (keep public, delete the course⇄group link
rows), then asserts the invariants:

  - links on a PUBLIC course are removed;
  - links on a NON-PUBLIC course are preserved;
  - links on a non-course resource are never touched;
  - the course stays public and the UserGroup row itself is untouched.
"""

from datetime import datetime

import pytest
from sqlmodel import select

from src.db.courses.courses import Course
from src.db.usergroup_resources import UserGroupResource
from src.db.usergroup_user import UserGroupUser
from src.db.usergroups import UserGroup

pytestmark = pytest.mark.asyncio


def _now() -> str:
    return str(datetime.now())


async def _make_course(db, org, *, id, uuid, public):
    c = Course(
        id=id,
        name=f"Course {id}",
        description="d",
        public=public,
        published=True,
        open_to_contributors=False,
        org_id=org.id,
        course_uuid=uuid,
        creation_date=_now(),
        update_date=_now(),
    )
    db.add(c)
    await db.commit()
    await db.refresh(c)
    return c


async def _make_group(db, org, *, uuid):
    g = UserGroup(
        name=f"Group {uuid}",
        description="g",
        org_id=org.id,
        usergroup_uuid=uuid,
        creation_date=_now(),
        update_date=_now(),
    )
    db.add(g)
    await db.commit()
    await db.refresh(g)
    return g


async def _link(db, org, group, resource_uuid):
    db.add(UserGroupResource(
        usergroup_id=group.id,
        resource_uuid=resource_uuid,
        org_id=org.id,
        creation_date=_now(),
        update_date=_now(),
    ))
    await db.commit()


async def _resolve_public_course_links(db) -> int:
    """Reproduce the migration: delete UserGroupResource rows that link a
    PUBLIC course to a group. Returns the number of rows removed."""
    rows = (
        await db.execute(
            select(UserGroupResource)
            .join(Course, Course.course_uuid == UserGroupResource.resource_uuid)
            .where(Course.public == True)  # noqa: E712
        )
    ).scalars().all()
    for ugr in rows:
        await db.delete(ugr)
    await db.commit()
    return len(rows)


async def _count_links(db, resource_uuid) -> int:
    rows = (
        await db.execute(
            select(UserGroupResource.id).where(
                UserGroupResource.resource_uuid == resource_uuid
            )
        )
    ).scalars().all()
    return len(rows)


async def test_migration_unlinks_only_public_courses(db, org):
    public_course = await _make_course(db, org, id=101, uuid="course_public", public=True)
    private_course = await _make_course(db, org, id=102, uuid="course_private", public=False)
    group = await _make_group(db, org, uuid="usergroup_a")

    # A public course wrongly restricted, a private course legitimately
    # restricted, plus a non-course (podcast) link that must never be touched.
    await _link(db, org, group, public_course.course_uuid)
    await _link(db, org, group, private_course.course_uuid)
    await _link(db, org, group, "podcast_xyz")

    # A member row on the group — must survive (we only drop resource links).
    db.add(UserGroupUser(
        usergroup_id=group.id,
        user_id=1,
        org_id=org.id,
        creation_date=_now(),
        update_date=_now(),
    ))
    await db.commit()

    removed = await _resolve_public_course_links(db)

    assert removed == 1
    assert await _count_links(db, public_course.course_uuid) == 0   # fixed
    assert await _count_links(db, private_course.course_uuid) == 1  # preserved
    assert await _count_links(db, "podcast_xyz") == 1               # untouched

    # The public course stayed public.
    refreshed = (
        await db.execute(
            select(Course).where(Course.id == public_course.id)
        )
    ).scalars().first()
    assert refreshed.public is True

    # The UserGroup itself still exists.
    g = (
        await db.execute(select(UserGroup).where(UserGroup.id == group.id))
    ).scalars().first()
    assert g is not None

    # The membership row survived.
    member = (
        await db.execute(
            select(UserGroupUser.id).where(UserGroupUser.usergroup_id == group.id)
        )
    ).scalars().first()
    assert member is not None


async def test_migration_noop_when_no_public_course_links(db, org):
    private_course = await _make_course(db, org, id=103, uuid="course_priv2", public=False)
    group = await _make_group(db, org, uuid="usergroup_b")
    await _link(db, org, group, private_course.course_uuid)

    removed = await _resolve_public_course_links(db)

    assert removed == 0
    assert await _count_links(db, private_course.course_uuid) == 1
