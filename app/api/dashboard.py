import uuid
from datetime import datetime, timedelta, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db, require_manager
from app.models.building import Building
from app.models.flat import Flat
from app.models.floor import Floor
from app.models.inspection import InspectionEntry
from app.models.project import Project
from app.models.user import (
    User,
    UserBuildingAssignment,
    UserFlatAssignment,
    UserProjectAssignment,
)
from app.schemas.dashboard import (
    AssignmentCoverageResponse,
    BuildingCoverage,
    BuildingStats,
    FlatCoverage,
    FloorCoverage,
    FloorProgress,
    InspectorActivity,
    InspectorRef,
    ProjectBuildingStats,
    ProjectOverview,
    ProjectStats,
    ProjectsOverviewResponse,
    TowerMini,
    TowerProgress,
    TowerStatsResponse,
    UsersSummary,
)


def _pct(completed: int, total: int) -> float:
    return round((completed / total) * 100, 1) if total > 0 else 0.0


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


@router.get(
    "/projects/{project_id}/tower-stats",
    response_model=TowerStatsResponse,
)
async def tower_stats(
    project_id: uuid.UUID,
    _user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> TowerStatsResponse:
    """
    Per-tower rollup with nested per-floor progress for the manager dashboard.
    Aggregates are computed in SQL, then stitched in Python so that flat-status
    counts and snag counts never share a join graph (which would multiply rows).
    """
    proj_result = await db.execute(select(Project).where(Project.id == project_id))
    project = proj_result.scalars().first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    # Every building in the project, even those with zero floors/flats — we
    # want them to appear (empty) in the dashboard rather than vanish.
    bld_rows = (
        await db.execute(
            select(Building.id, Building.name)
            .where(Building.project_id == project_id)
            .order_by(Building.name)
        )
    ).all()

    # Per-floor flat-status counts (floors only; buildings with 0 floors
    # won't appear here and get an empty floors list below).
    floor_rows = (
        await db.execute(
            select(
                Building.id.label("building_id"),
                Floor.id.label("floor_id"),
                Floor.floor_number.label("floor_number"),
                func.count(Flat.id).label("total_flats"),
                func.count(
                    case((Flat.inspection_status == "COMPLETED", Flat.id))
                ).label("inspected"),
                func.count(
                    case((Flat.inspection_status == "IN_PROGRESS", Flat.id))
                ).label("in_progress"),
                func.count(
                    case((Flat.inspection_status == "NOT_STARTED", Flat.id))
                ).label("not_started"),
            )
            .join(Floor, Floor.building_id == Building.id)
            .outerjoin(Flat, Flat.floor_id == Floor.id)
            .where(Building.project_id == project_id)
            .group_by(Building.id, Floor.id, Floor.floor_number)
            .order_by(Building.id, Floor.floor_number)
        )
    ).all()

    # Per-floor open-snag counts.
    floor_snag_rows = (
        await db.execute(
            select(
                Floor.id.label("floor_id"),
                func.count(InspectionEntry.id).label("open_snags"),
            )
            .join(Flat, Flat.floor_id == Floor.id)
            .join(InspectionEntry, InspectionEntry.flat_id == Flat.id)
            .join(Building, Building.id == Floor.building_id)
            .where(
                Building.project_id == project_id,
                InspectionEntry.status == "SNAG",
                InspectionEntry.snag_fix_status == "OPEN",
            )
            .group_by(Floor.id)
        )
    ).all()
    open_snags_by_floor = {row.floor_id: row.open_snags for row in floor_snag_rows}

    # Per-tower snag aggregates — kept in a separate query to avoid row
    # multiplication against the flat-status counts above.
    tower_snag_rows = (
        await db.execute(
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
                func.count(
                    case((InspectionEntry.severity == "CRITICAL", InspectionEntry.id))
                ).label("critical"),
                func.count(
                    case((InspectionEntry.severity == "MAJOR", InspectionEntry.id))
                ).label("major"),
                func.count(
                    case((InspectionEntry.severity == "MINOR", InspectionEntry.id))
                ).label("minor"),
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
    ).all()
    snags_by_tower = {
        row.building_id: {
            "total": row.total_snags,
            "open": row.open_snags,
            "critical": row.critical,
            "major": row.major,
            "minor": row.minor,
        }
        for row in tower_snag_rows
    }

    floors_by_tower: dict[uuid.UUID, list[FloorProgress]] = {}
    for row in floor_rows:
        floors_by_tower.setdefault(row.building_id, []).append(
            FloorProgress(
                floor_id=row.floor_id,
                floor_number=row.floor_number,
                label=f"Floor {row.floor_number}",
                total_flats=row.total_flats,
                inspected_flats=row.inspected,
                in_progress_flats=row.in_progress,
                not_started_flats=row.not_started,
                completion_pct=_pct(row.inspected, row.total_flats),
                open_snags=open_snags_by_floor.get(row.floor_id, 0),
            )
        )

    towers: list[TowerProgress] = []
    for bld in bld_rows:
        floors = floors_by_tower.get(bld.id, [])
        total = sum(f.total_flats for f in floors)
        inspected = sum(f.inspected_flats for f in floors)
        in_progress = sum(f.in_progress_flats for f in floors)
        not_started = sum(f.not_started_flats for f in floors)
        snags = snags_by_tower.get(
            bld.id,
            {"total": 0, "open": 0, "critical": 0, "major": 0, "minor": 0},
        )
        towers.append(
            TowerProgress(
                building_id=bld.id,
                building_name=bld.name,
                total_flats=total,
                inspected_flats=inspected,
                in_progress_flats=in_progress,
                not_started_flats=not_started,
                completion_pct=_pct(inspected, total),
                total_snags=snags["total"],
                open_snags=snags["open"],
                critical_snags=snags["critical"],
                major_snags=snags["major"],
                minor_snags=snags["minor"],
                floors=floors,
            )
        )

    proj_total = sum(t.total_flats for t in towers)
    proj_inspected = sum(t.inspected_flats for t in towers)
    proj_in_progress = sum(t.in_progress_flats for t in towers)
    proj_not_started = sum(t.not_started_flats for t in towers)

    return TowerStatsResponse(
        project_id=project.id,
        project_name=project.name,
        total_flats=proj_total,
        inspected_flats=proj_inspected,
        in_progress_flats=proj_in_progress,
        not_started_flats=proj_not_started,
        completion_pct=_pct(proj_inspected, proj_total),
        towers=towers,
    )


@router.get(
    "/projects-overview",
    response_model=ProjectsOverviewResponse,
)
async def projects_overview(
    _user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ProjectsOverviewResponse:
    """
    Lightweight cross-project rollup for the Projects list page: every project
    with its tower-level (no floor) progress, in a single response.
    """
    project_rows = (
        await db.execute(
            select(Project.id, Project.name, Project.location).order_by(Project.name)
        )
    ).all()

    if not project_rows:
        return ProjectsOverviewResponse(projects=[])

    # Per-tower flat-status rollup for all projects at once.
    tower_rows = (
        await db.execute(
            select(
                Building.project_id.label("project_id"),
                Building.id.label("building_id"),
                Building.name.label("building_name"),
                func.count(Flat.id).label("total_flats"),
                func.count(
                    case((Flat.inspection_status == "COMPLETED", Flat.id))
                ).label("inspected"),
                func.count(
                    case((Flat.inspection_status == "IN_PROGRESS", Flat.id))
                ).label("in_progress"),
                func.count(
                    case((Flat.inspection_status == "NOT_STARTED", Flat.id))
                ).label("not_started"),
            )
            .outerjoin(Floor, Floor.building_id == Building.id)
            .outerjoin(Flat, Flat.floor_id == Floor.id)
            .group_by(Building.project_id, Building.id, Building.name)
            .order_by(Building.project_id, Building.name)
        )
    ).all()

    towers_by_project: dict[uuid.UUID, list[TowerMini]] = {}
    for row in tower_rows:
        towers_by_project.setdefault(row.project_id, []).append(
            TowerMini(
                building_id=row.building_id,
                building_name=row.building_name,
                total_flats=row.total_flats,
                inspected_flats=row.inspected,
                in_progress_flats=row.in_progress,
                not_started_flats=row.not_started,
                completion_pct=_pct(row.inspected, row.total_flats),
            )
        )

    projects = []
    for p in project_rows:
        towers = towers_by_project.get(p.id, [])
        total = sum(t.total_flats for t in towers)
        inspected = sum(t.inspected_flats for t in towers)
        in_progress = sum(t.in_progress_flats for t in towers)
        not_started = sum(t.not_started_flats for t in towers)
        projects.append(
            ProjectOverview(
                project_id=p.id,
                project_name=p.name,
                location=p.location,
                total_buildings=len(towers),
                total_flats=total,
                inspected_flats=inspected,
                in_progress_flats=in_progress,
                not_started_flats=not_started,
                completion_pct=_pct(inspected, total),
                towers=towers,
            )
        )

    return ProjectsOverviewResponse(projects=projects)


@router.get(
    "/projects/{project_id}/assignment-coverage",
    response_model=AssignmentCoverageResponse,
)
async def assignment_coverage(
    project_id: uuid.UUID,
    _manager: Annotated[User, Depends(require_manager)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> AssignmentCoverageResponse:
    """Per-flat inspector coverage for a project.

    Returns the full project hierarchy (buildings → floors → flats) with the
    list of inspectors assigned to each flat, either directly or inherited
    via a building- or project-level assignment. Lets the portal show
    unassigned-flat badges and drive the `By Project` coverage view.
    """
    proj_result = await db.execute(select(Project).where(Project.id == project_id))
    project = proj_result.scalars().first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    # Full hierarchy in one query, ordered so we can build nested structures
    # without hash lookups per row.
    hierarchy_rows = (
        await db.execute(
            select(
                Building.id.label("building_id"),
                Building.name.label("building_name"),
                Floor.id.label("floor_id"),
                Floor.floor_number.label("floor_number"),
                Flat.id.label("flat_id"),
                Flat.flat_number.label("flat_number"),
                Flat.flat_type.label("flat_type"),
                Flat.inspection_status.label("inspection_status"),
            )
            .outerjoin(Floor, Floor.building_id == Building.id)
            .outerjoin(Flat, Flat.floor_id == Floor.id)
            .where(Building.project_id == project_id)
            .order_by(Building.name, Floor.floor_number, Flat.flat_number)
        )
    ).all()

    # Project-level inspectors (inherit to every flat in the project).
    project_insp_rows = (
        await db.execute(
            select(User.id, User.full_name, User.username)
            .join(UserProjectAssignment, UserProjectAssignment.user_id == User.id)
            .where(UserProjectAssignment.project_id == project_id)
            .order_by(User.full_name)
        )
    ).all()
    project_inspectors = [
        InspectorRef(
            id=r.id, full_name=r.full_name, username=r.username, source="PROJECT"
        )
        for r in project_insp_rows
    ]

    # Building-level inspectors (inherit to every flat in the building).
    bld_insp_rows = (
        await db.execute(
            select(
                UserBuildingAssignment.building_id,
                User.id,
                User.full_name,
                User.username,
            )
            .join(User, User.id == UserBuildingAssignment.user_id)
            .join(Building, Building.id == UserBuildingAssignment.building_id)
            .where(Building.project_id == project_id)
            .order_by(User.full_name)
        )
    ).all()
    inspectors_by_building: dict[uuid.UUID, list[InspectorRef]] = {}
    for r in bld_insp_rows:
        inspectors_by_building.setdefault(r.building_id, []).append(
            InspectorRef(
                id=r.id, full_name=r.full_name, username=r.username, source="BUILDING"
            )
        )

    # Flat-level inspectors.
    flat_insp_rows = (
        await db.execute(
            select(
                UserFlatAssignment.flat_id,
                User.id,
                User.full_name,
                User.username,
            )
            .join(User, User.id == UserFlatAssignment.user_id)
            .join(Flat, Flat.id == UserFlatAssignment.flat_id)
            .join(Floor, Floor.id == Flat.floor_id)
            .join(Building, Building.id == Floor.building_id)
            .where(Building.project_id == project_id)
            .order_by(User.full_name)
        )
    ).all()
    inspectors_by_flat: dict[uuid.UUID, list[InspectorRef]] = {}
    for r in flat_insp_rows:
        inspectors_by_flat.setdefault(r.flat_id, []).append(
            InspectorRef(
                id=r.id, full_name=r.full_name, username=r.username, source="FLAT"
            )
        )

    # Build nested structure. We preserve ordering from the SQL ORDER BY so
    # the portal can render deterministically.
    buildings: list[BuildingCoverage] = []
    current_bld: dict | None = None
    current_floor: dict | None = None

    def finalize_floor(floor: dict, bld: dict) -> None:
        covered_flats = sum(
            1 for f in floor["flats"] if f.assigned_inspectors
        )
        bld["floors"].append(
            FloorCoverage(
                floor_id=floor["floor_id"],
                floor_number=floor["floor_number"],
                label=f"Floor {floor['floor_number']}",
                total_flats=len(floor["flats"]),
                covered_flats=covered_flats,
                unassigned_flats=len(floor["flats"]) - covered_flats,
                flats=floor["flats"],
            )
        )

    def finalize_building(bld: dict) -> None:
        total = sum(f.total_flats for f in bld["floors"])
        covered = sum(f.covered_flats for f in bld["floors"])
        buildings.append(
            BuildingCoverage(
                building_id=bld["building_id"],
                building_name=bld["building_name"],
                total_flats=total,
                covered_flats=covered,
                unassigned_flats=total - covered,
                building_inspectors=(
                    project_inspectors
                    + inspectors_by_building.get(bld["building_id"], [])
                ),
                floors=bld["floors"],
            )
        )

    for row in hierarchy_rows:
        if current_bld is None or current_bld["building_id"] != row.building_id:
            if current_floor is not None and current_bld is not None:
                finalize_floor(current_floor, current_bld)
                current_floor = None
            if current_bld is not None:
                finalize_building(current_bld)
            current_bld = {
                "building_id": row.building_id,
                "building_name": row.building_name,
                "floors": [],
            }

        if row.floor_id is None:
            continue  # Building with no floors — finalized with empty floors list.

        if current_floor is None or current_floor["floor_id"] != row.floor_id:
            if current_floor is not None:
                finalize_floor(current_floor, current_bld)
            current_floor = {
                "floor_id": row.floor_id,
                "floor_number": row.floor_number,
                "flats": [],
            }

        if row.flat_id is None:
            continue  # Floor with no flats.

        # Per-flat inspectors = project-level + building-level + flat-level.
        flat_inspectors = (
            project_inspectors
            + inspectors_by_building.get(row.building_id, [])
            + inspectors_by_flat.get(row.flat_id, [])
        )
        current_floor["flats"].append(
            FlatCoverage(
                flat_id=row.flat_id,
                flat_number=row.flat_number,
                flat_type=row.flat_type,
                inspection_status=row.inspection_status,
                assigned_inspectors=flat_inspectors,
            )
        )

    if current_floor is not None and current_bld is not None:
        finalize_floor(current_floor, current_bld)
    if current_bld is not None:
        finalize_building(current_bld)

    total_flats = sum(b.total_flats for b in buildings)
    covered_flats = sum(b.covered_flats for b in buildings)

    return AssignmentCoverageResponse(
        project_id=project.id,
        project_name=project.name,
        total_flats=total_flats,
        covered_flats=covered_flats,
        unassigned_flats=total_flats - covered_flats,
        project_inspectors=project_inspectors,
        buildings=buildings,
    )


@router.get("/users/summary", response_model=UsersSummary)
async def users_summary(
    _manager: Annotated[User, Depends(require_manager)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> UsersSummary:
    """Top-of-page stats for the Users admin page.

    `total_unassigned_flats` counts flats with no inspector via any assignment
    level across the whole system — the signal that drives the "needs
    attention" badge in the summary strip.
    """
    role_counts = (
        await db.execute(
            select(
                func.count(User.id).label("total"),
                func.count(case((User.role == "MANAGER", User.id))).label("managers"),
                func.count(case((User.role == "INSPECTOR", User.id))).label(
                    "inspectors"
                ),
            )
        )
    ).one()

    # Inspectors with zero assignments at any level.
    inspector_ids_with_any = set()
    for stmt in (
        select(UserProjectAssignment.user_id)
        .join(User, User.id == UserProjectAssignment.user_id)
        .where(User.role == "INSPECTOR"),
        select(UserBuildingAssignment.user_id)
        .join(User, User.id == UserBuildingAssignment.user_id)
        .where(User.role == "INSPECTOR"),
        select(UserFlatAssignment.user_id)
        .join(User, User.id == UserFlatAssignment.user_id)
        .where(User.role == "INSPECTOR"),
    ):
        rows = (await db.execute(stmt)).all()
        inspector_ids_with_any.update(r[0] for r in rows)
    idle_inspectors = max(0, role_counts.inspectors - len(inspector_ids_with_any))

    # Unassigned flats = total flats - flats covered by any inspector
    # at project, building, or flat level.
    covered_project_ids = {
        r[0]
        for r in (
            await db.execute(
                select(UserProjectAssignment.project_id)
                .join(User, User.id == UserProjectAssignment.user_id)
                .where(User.role == "INSPECTOR")
            )
        ).all()
    }
    covered_building_ids = {
        r[0]
        for r in (
            await db.execute(
                select(UserBuildingAssignment.building_id)
                .join(User, User.id == UserBuildingAssignment.user_id)
                .where(User.role == "INSPECTOR")
            )
        ).all()
    }
    covered_flat_ids = {
        r[0]
        for r in (
            await db.execute(
                select(UserFlatAssignment.flat_id)
                .join(User, User.id == UserFlatAssignment.user_id)
                .where(User.role == "INSPECTOR")
            )
        ).all()
    }

    flat_rows = (
        await db.execute(
            select(
                Flat.id,
                Floor.building_id,
                Building.project_id,
            )
            .join(Floor, Floor.id == Flat.floor_id)
            .join(Building, Building.id == Floor.building_id)
        )
    ).all()
    total_unassigned_flats = sum(
        1
        for r in flat_rows
        if r.project_id not in covered_project_ids
        and r.building_id not in covered_building_ids
        and r.id not in covered_flat_ids
    )

    return UsersSummary(
        total_users=role_counts.total,
        total_managers=role_counts.managers,
        total_inspectors=role_counts.inspectors,
        idle_inspectors=idle_inspectors,
        total_unassigned_flats=total_unassigned_flats,
    )
