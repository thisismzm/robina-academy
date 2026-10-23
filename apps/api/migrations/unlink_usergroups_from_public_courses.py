"""
One-shot data migration: resolve courses that are BOTH public AND restricted to
a UserGroup — an invalid combination now rejected at write time.

Background
----------
"Public" and "restricted to a UserGroup" are mutually exclusive access modes: a
public course is viewable by everyone, so any UserGroup link on it is silently
ignored at view time (public wins). Before this was enforced, a course could end
up in both states at once, which is confusing and was the source of a
"restricted course is still viewable by others" report.

The write paths now refuse to create this state (you cannot link a group to a
public course, nor make a group-linked course public). This script fixes rows
that already exist in the bad state.

Resolution (chosen policy: KEEP PUBLIC, DROP THE LINKS)
-------------------------------------------------------
For every course with ``public = True`` that still has one or more
``UserGroupResource`` rows (resource_uuid = the course's uuid), the course is
left public and the UserGroupResource link rows are DELETED. Only links whose
resource is a *course* and whose course is *public* are touched; links on
non-public courses, and links on other resource types (podcasts, media, …), are
left completely alone. The UserGroups themselves are never modified or deleted —
only the course⇄group link rows.

Usage
-----
Run from the apps/api directory:

    python migrations/unlink_usergroups_from_public_courses.py            # apply
    python migrations/unlink_usergroups_from_public_courses.py --dry-run  # preview

The database URL is read from the LearnHouse config, the same way
``payments_to_offers_migration.py`` does.
"""

import argparse
import asyncio
import os
import sys

# Adjust path so we can import from the api package.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from sqlmodel import select  # noqa: E402
from sqlmodel.ext.asyncio.session import AsyncSession  # noqa: E402

from src.db.courses.courses import Course  # noqa: E402
from src.db.usergroup_resources import UserGroupResource  # noqa: E402


async def find_affected(db_session: AsyncSession):
    """Return (course, [UserGroupResource, ...]) for every public course that
    still carries one or more UserGroup links.

    The join on ``resource_uuid == course_uuid`` restricts the result to course
    links only, and ``Course.public == True`` restricts it to the bad state.
    """
    rows = (
        await db_session.execute(
            select(Course, UserGroupResource)
            .join(
                UserGroupResource,
                UserGroupResource.resource_uuid == Course.course_uuid,
            )
            .where(Course.public == True)  # noqa: E712 - SQL boolean, not Python `is`
            .order_by(Course.id)
        )
    ).all()

    grouped: dict[int, tuple[Course, list[UserGroupResource]]] = {}
    for course, ugr in rows:
        entry = grouped.setdefault(course.id, (course, []))
        entry[1].append(ugr)
    return list(grouped.values())


async def run_migration(db_session: AsyncSession, dry_run: bool = False) -> int:
    affected = await find_affected(db_session)

    if not affected:
        print("No public courses have UserGroup links. Nothing to fix.")
        return 0

    total_links = sum(len(links) for _, links in affected)
    verb = "Would unlink" if dry_run else "Unlinking"
    print(
        f"Found {len(affected)} public course(s) with {total_links} "
        f"UserGroup link(s) to remove.\n"
    )

    removed = 0
    for course, links in affected:
        group_ids = sorted({ugr.usergroup_id for ugr in links})
        print(
            f"  {verb} {len(links)} link(s) from public course "
            f"{course.course_uuid} ({course.name!r}, org_id={course.org_id}); "
            f"usergroup_ids={group_ids}"
        )
        if not dry_run:
            for ugr in links:
                await db_session.delete(ugr)
                removed += 1

    if dry_run:
        print("\nDry run: no changes were made.")
        return 0

    await db_session.commit()
    print(
        f"\nDone. Removed {removed} course-to-UserGroup link row(s). "
        f"The courses stayed public; the UserGroups were left untouched."
    )
    return removed


def _parse_args():
    parser = argparse.ArgumentParser(
        description=(
            "Unlink UserGroups from public courses (keep the course public, "
            "drop the link rows)."
        )
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be changed without modifying the database.",
    )
    return parser.parse_args()


async def _main(dry_run: bool) -> None:
    # Reuse the application's own async engine/session factory so this connects
    # to the database exactly as the running app does (asyncpg driver, URL
    # normalization, pool settings) rather than re-deriving the connection.
    from src.core.events.database import _async_session_factory

    async with _async_session_factory() as session:
        await run_migration(session, dry_run=dry_run)


if __name__ == "__main__":
    args = _parse_args()
    asyncio.run(_main(args.dry_run))
