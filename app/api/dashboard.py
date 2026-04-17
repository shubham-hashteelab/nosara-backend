import uuid
from datetime import datetime, timedelta, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db
from app.models.building import Building
from app.models.flat import Flat
from app.models.floor import Floor
from app.models.inspection import InspectionEntry
from app.models.project import Project
from app.models.user import User
from app.schemas.dashboard import (
    BuildingStats,
    InspectorActivity,
    ProjectBuildingStats,
    ProjectStats,
)


async def _ensure_project_exists(
    db: AsyncSession, project_id: uuid.UUID
) -> None:
    result = await db.execute(select(Project.id).where(Project.id == project_id))
    if result.scalar() is None:
        raise HTTPException(status_code=404, detail="Project not found")

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


@router.get("/projects/{project_id}/stats", response_model=ProjectStats)
async def project_stats(
    project_id: uuid.UUID,
    _user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ProjectStats:
    # Validate project
    proj_result = await db.execute(select(Project).where(Project.id == project_id))
    project = proj_result.scalars().first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    # Count buildings
    bld_count = await db.execute(
        select(func.count(Building.id)).where(Building.project_id == project_id)
    )
    total_buildings = bld_count.scalar() or 0

    # Flat status counts (from inspection_status column)
    flat_stats = await db.execute(
        select(
            func.count(Flat.id).label("total"),
            func.count(
                case((Flat.inspection_status == "COMPLETED", Flat.id))
            ).label("completed"),
            func.count(
                case((Flat.inspection_status == "IN_PROGRESS", Flat.id))
            ).label("in_progress"),
            func.count(
                case((Flat.inspection_status == "NOT_STARTED", Flat.id))
            ).label("not_started"),
        )
        .join(Floor, Floor.id == Flat.floor_id)
        .join(Building, Building.id == Floor.building_id)
        .where(Building.project_id == project_id)
    )
    flat_row = flat_stats.one()

    # Snag stats: fix status, severity, and category breakdown
    snag_stats = await db.execute(
        select(
            func.count(InspectionEntry.id).label("total_snags"),
            func.count(
                case(
                    (
                        InspectionEntry.snag_fix_status == "OPEN",
                        InspectionEntry.id,
                    )
                )
            ).label("open_snags"),
            func.count(
                case(
                    (
                        InspectionEntry.snag_fix_status == "FIXED",
                        InspectionEntry.id,
                    )
                )
            ).label("fixed_snags"),
            func.count(
                case(
                    (
                        InspectionEntry.snag_fix_status == "VERIFIED",
                        InspectionEntry.id,
                    )
                )
            ).label("verified_snags"),
            func.count(
                case((InspectionEntry.severity == "CRITICAL", InspectionEntry.id))
            ).label("critical_snags"),
            func.count(
                case((InspectionEntry.severity == "MAJOR", InspectionEntry.id))
            ).label("major_snags"),
            func.count(
                case((InspectionEntry.severity == "MINOR", InspectionEntry.id))
            ).label("minor_snags"),
        )
        .join(Flat, Flat.id == InspectionEntry.flat_id)
        .join(Floor, Floor.id == Flat.floor_id)
        .join(Building, Building.id == Floor.building_id)
        .where(
            Building.project_id == project_id,
            InspectionEntry.status == "SNAG",
        )
    )
    snag_row = snag_stats.one()

    # Snags by category (room_label as category)
    cat_result = await db.execute(
        select(
            InspectionEntry.room_label,
            func.count(InspectionEntry.id),
        )
        .join(Flat, Flat.id == InspectionEntry.flat_id)
        .join(Floor, Floor.id == Flat.floor_id)
        .join(Building, Building.id == Floor.building_id)
        .where(
            Building.project_id == project_id,
            InspectionEntry.status == "SNAG",
        )
        .group_by(InspectionEntry.room_label)
    )
    snags_by_category = {
        row[0]: row[1] for row in cat_result.all() if row[0]
    }

    return ProjectStats(
        project_id=project.id,
        project_name=project.name,
        total_buildings=total_buildings,
        total_flats=flat_row.total,
        inspected_flats=flat_row.completed,
        in_progress_flats=flat_row.in_progress,
        not_started_flats=flat_row.not_started,
        total_snags=snag_row.total_snags,
        open_snags=snag_row.open_snags,
        fixed_snags=snag_row.fixed_snags,
        verified_snags=snag_row.verified_snags,
        critical_snags=snag_row.critical_snags,
        major_snags=snag_row.major_snags,
        minor_snags=snag_row.minor_snags,
        snags_by_category=snags_by_category,
    )


@router.get("/buildings/{building_id}/stats", response_model=BuildingStats)
async def building_stats(
    building_id: uuid.UUID,
    _user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> BuildingStats:
    bld_result = await db.execute(
        select(Building).where(Building.id == building_id)
    )
    building = bld_result.scalars().first()
    if not building:
        raise HTTPException(status_code=404, detail="Building not found")

    floor_count = await db.execute(
        select(func.count(Floor.id)).where(Floor.building_id == building_id)
    )
    total_floors = floor_count.scalar() or 0

    flat_count = await db.execute(
        select(func.count(Flat.id))
        .join(Floor, Floor.id == Flat.floor_id)
        .where(Floor.building_id == building_id)
    )
    total_flats = flat_count.scalar() or 0

    entry_stats = await db.execute(
        select(
            func.count(InspectionEntry.id).label("total"),
            func.count(
                case((InspectionEntry.status == "SNAG", InspectionEntry.id))
            ).label("snag_count"),
            func.count(
                case((InspectionEntry.status == "OK", InspectionEntry.id))
            ).label("ok_count"),
            func.count(
                case((InspectionEntry.status == "NA", InspectionEntry.id))
            ).label("na_count"),
        )
        .join(Flat, Flat.id == InspectionEntry.flat_id)
        .join(Floor, Floor.id == Flat.floor_id)
        .where(Floor.building_id == building_id)
    )
    row = entry_stats.one()

    return BuildingStats(
        building_id=building.id,
        building_name=building.name,
        total_floors=total_floors,
        total_flats=total_flats,
        total_entries=row.total,
        snag_count=row.snag_count,
        ok_count=row.ok_count,
        na_count=row.na_count,
    )


@router.get(
    "/projects/{project_id}/inspector-activity",
    response_model=list[InspectorActivity],
)
async def inspector_activity(
    project_id: uuid.UUID,
    _user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    days: Annotated[int, Query(ge=1, le=90)] = 7,
) -> list[InspectorActivity]:
    await _ensure_project_exists(db, project_id)

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    day_bucket = func.date(InspectionEntry.updated_at).label("date")

    result = await db.execute(
        select(
            User.id,
            User.full_name,
            day_bucket,
            func.count(InspectionEntry.id).label("entries_checked"),
            func.count(
                case((InspectionEntry.status == "SNAG", InspectionEntry.id))
            ).label("snags_found"),
        )
        .join(InspectionEntry, InspectionEntry.inspector_id == User.id)
        .join(Flat, Flat.id == InspectionEntry.flat_id)
        .join(Floor, Floor.id == Flat.floor_id)
        .join(Building, Building.id == Floor.building_id)
        .where(
            Building.project_id == project_id,
            User.role == "INSPECTOR",
            InspectionEntry.updated_at >= cutoff,
        )
        .group_by(User.id, User.full_name, day_bucket)
        .order_by(day_bucket)
    )

    return [
        InspectorActivity(
            inspector_id=row.id,
            inspector_name=row.full_name,
            date=row.date,
            entries_checked=row.entries_checked,
            snags_found=row.snags_found,
        )
        for row in result.all()
    ]


@router.get(
    "/projects/{project_id}/building-stats",
    response_model=list[ProjectBuildingStats],
)
async def project_building_stats(
    project_id: uuid.UUID,
    _user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> list[ProjectBuildingStats]:
    await _ensure_project_exists(db, project_id)

    # Two queries, merged in Python: joining flats AND entries in one query
    # would multiply row counts (a flat with N entries gets counted N times),
    # breaking the flat-status aggregates.
    flat_result = await db.execute(
        select(
            Building.id.label("building_id"),
            Building.name.label("building_name"),
            func.count(Flat.id).label("total_flats"),
            func.count(
                case((Flat.inspection_status == "COMPLETED", Flat.id))
            ).label("inspected_flats"),
            func.count(
                case((Flat.inspection_status == "IN_PROGRESS", Flat.id))
            ).label("in_progress_flats"),
        )
        .outerjoin(Floor, Floor.building_id == Building.id)
        .outerjoin(Flat, Flat.floor_id == Floor.id)
        .where(Building.project_id == project_id)
        .group_by(Building.id, Building.name)
        .order_by(Building.name)
    )
    flat_rows = flat_result.all()

    snag_result = await db.execute(
        select(
            Building.id.label("building_id"),
            func.count(InspectionEntry.id).label("total_snags"),
            func.count(
                case(
                    (
                        InspectionEntry.snag_fix_status == "OPEN",
                        InspectionEntry.id,
                    )
                )
            ).label("open_snags"),
        )
        .join(Floor, Floor.building_id == Building.id)
        .join(Flat, Flat.floor_id == Floor.id)
        .join(InspectionEntry, InspectionEntry.flat_id == Flat.id)
        .where(
            Building.project_id == project_id,
            InspectionEntry.status == "SNAG",
        )
        .group_by(Building.id)
    )
    snags_by_building = {
        row.building_id: (row.total_snags, row.open_snags)
        for row in snag_result.all()
    }

    return [
        ProjectBuildingStats(
            building_id=row.building_id,
            building_name=row.building_name,
            total_flats=row.total_flats,
            inspected_flats=row.inspected_flats,
            in_progress_flats=row.in_progress_flats,
            total_snags=snags_by_building.get(row.building_id, (0, 0))[0],
            open_snags=snags_by_building.get(row.building_id, (0, 0))[1],
        )
        for row in flat_rows
    ]
