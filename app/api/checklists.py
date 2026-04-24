import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db, require_manager
from app.models.building import Building
from app.models.checklist import ChecklistTemplate, FlatTypeRoom, FloorPlanLayout
from app.models.flat import Flat
from app.models.floor import Floor
from app.models.project import Project
from app.models.user import User
from app.schemas.checklist import (
    ChecklistTemplateCreate,
    ChecklistTemplateResponse,
    ChecklistTemplateUpdate,
    FlatTypeRoomCreate,
    FlatTypeRoomResponse,
    FlatTypeRoomUpdate,
    FloorPlanLayoutCreate,
    FloorPlanLayoutResponse,
    FloorPlanLayoutUpdate,
)
from app.services.auth_service import hash_password
from app.services.inspection_service import (
    initialize_flat_checklist,
    recompute_flat_inspection_status,
)

router = APIRouter(tags=["checklists"])

# ---------------------------------------------------------------------------
# Seed data from Android app's SeedData.kt
# ---------------------------------------------------------------------------

SEED_CHECKLIST_ITEMS: list[dict] = [
    # LIVING_ROOM
    {"room_type": "LIVING_ROOM", "category": "ELECTRICAL", "item_name": "Switches working", "trade": "ELECTRICAL", "sort_order": 1},
    {"room_type": "LIVING_ROOM", "category": "ELECTRICAL", "item_name": "Lights working", "trade": "ELECTRICAL", "sort_order": 2},
    {"room_type": "LIVING_ROOM", "category": "ELECTRICAL", "item_name": "Fan working", "trade": "ELECTRICAL", "sort_order": 3},
    {"room_type": "LIVING_ROOM", "category": "PAINT", "item_name": "Wall paint finish", "trade": "PAINTING", "sort_order": 4},
    {"room_type": "LIVING_ROOM", "category": "PAINT", "item_name": "Ceiling paint", "trade": "PAINTING", "sort_order": 5},
    {"room_type": "LIVING_ROOM", "category": "CIVIL", "item_name": "Floor tiles ok", "trade": "TILING", "sort_order": 6},
    {"room_type": "LIVING_ROOM", "category": "CIVIL", "item_name": "Wall cracks or dampness", "trade": "CIVIL", "sort_order": 7},
    {"room_type": "LIVING_ROOM", "category": "DOORS_WINDOWS", "item_name": "Main door ok", "trade": "CARPENTRY", "sort_order": 8},
    {"room_type": "LIVING_ROOM", "category": "DOORS_WINDOWS", "item_name": "Windows open/close properly", "trade": "CARPENTRY", "sort_order": 9},
    # BEDROOM
    {"room_type": "BEDROOM", "category": "ELECTRICAL", "item_name": "Switches working", "trade": "ELECTRICAL", "sort_order": 1},
    {"room_type": "BEDROOM", "category": "ELECTRICAL", "item_name": "Lights working", "trade": "ELECTRICAL", "sort_order": 2},
    {"room_type": "BEDROOM", "category": "ELECTRICAL", "item_name": "Fan working", "trade": "ELECTRICAL", "sort_order": 3},
    {"room_type": "BEDROOM", "category": "ELECTRICAL", "item_name": "AC point available", "trade": "ELECTRICAL", "sort_order": 4},
    {"room_type": "BEDROOM", "category": "PAINT", "item_name": "Wall paint finish", "trade": "PAINTING", "sort_order": 5},
    {"room_type": "BEDROOM", "category": "PAINT", "item_name": "Ceiling paint", "trade": "PAINTING", "sort_order": 6},
    {"room_type": "BEDROOM", "category": "CIVIL", "item_name": "Floor tiles ok", "trade": "TILING", "sort_order": 7},
    {"room_type": "BEDROOM", "category": "CIVIL", "item_name": "Wall cracks or dampness", "trade": "CIVIL", "sort_order": 8},
    {"room_type": "BEDROOM", "category": "DOORS_WINDOWS", "item_name": "Door opens/closes properly", "trade": "CARPENTRY", "sort_order": 9},
    {"room_type": "BEDROOM", "category": "DOORS_WINDOWS", "item_name": "Windows open/close properly", "trade": "CARPENTRY", "sort_order": 10},
    # KITCHEN
    {"room_type": "KITCHEN", "category": "ELECTRICAL", "item_name": "Switches working", "trade": "ELECTRICAL", "sort_order": 1},
    {"room_type": "KITCHEN", "category": "ELECTRICAL", "item_name": "Lights working", "trade": "ELECTRICAL", "sort_order": 2},
    {"room_type": "KITCHEN", "category": "ELECTRICAL", "item_name": "Exhaust point available", "trade": "ELECTRICAL", "sort_order": 3},
    {"room_type": "KITCHEN", "category": "PLUMBING", "item_name": "Sink tap working", "trade": "PLUMBING", "sort_order": 4},
    {"room_type": "KITCHEN", "category": "PLUMBING", "item_name": "Sink drainage ok", "trade": "PLUMBING", "sort_order": 5},
    {"room_type": "KITCHEN", "category": "PLUMBING", "item_name": "No water leakage", "trade": "PLUMBING", "sort_order": 6},
    {"room_type": "KITCHEN", "category": "CIVIL", "item_name": "Wall tiles ok", "trade": "TILING", "sort_order": 7},
    {"room_type": "KITCHEN", "category": "CIVIL", "item_name": "Floor tiles ok", "trade": "TILING", "sort_order": 8},
    {"room_type": "KITCHEN", "category": "FIXTURES", "item_name": "Kitchen platform level", "trade": "CIVIL", "sort_order": 9},
    {"room_type": "KITCHEN", "category": "DOORS_WINDOWS", "item_name": "Windows open/close properly", "trade": "CARPENTRY", "sort_order": 10},
    # BATHROOM
    {"room_type": "BATHROOM", "category": "ELECTRICAL", "item_name": "Light & exhaust working", "trade": "ELECTRICAL", "sort_order": 1},
    {"room_type": "BATHROOM", "category": "ELECTRICAL", "item_name": "Geyser point available", "trade": "ELECTRICAL", "sort_order": 2},
    {"room_type": "BATHROOM", "category": "PLUMBING", "item_name": "Taps working (hot & cold)", "trade": "PLUMBING", "sort_order": 3},
    {"room_type": "BATHROOM", "category": "PLUMBING", "item_name": "Shower working", "trade": "PLUMBING", "sort_order": 4},
    {"room_type": "BATHROOM", "category": "PLUMBING", "item_name": "Flush working", "trade": "PLUMBING", "sort_order": 5},
    {"room_type": "BATHROOM", "category": "PLUMBING", "item_name": "Water drains properly", "trade": "PLUMBING", "sort_order": 6},
    {"room_type": "BATHROOM", "category": "PLUMBING", "item_name": "No leakage", "trade": "PLUMBING", "sort_order": 7},
    {"room_type": "BATHROOM", "category": "CIVIL", "item_name": "Wall tiles ok", "trade": "TILING", "sort_order": 8},
    {"room_type": "BATHROOM", "category": "CIVIL", "item_name": "Floor tiles ok (not slippery)", "trade": "TILING", "sort_order": 9},
    {"room_type": "BATHROOM", "category": "DOORS_WINDOWS", "item_name": "Door opens/closes properly", "trade": "CARPENTRY", "sort_order": 10},
    # BALCONY
    {"room_type": "BALCONY", "category": "ELECTRICAL", "item_name": "Light point available", "trade": "ELECTRICAL", "sort_order": 1},
    {"room_type": "BALCONY", "category": "PLUMBING", "item_name": "Water drains properly", "trade": "PLUMBING", "sort_order": 2},
    {"room_type": "BALCONY", "category": "CIVIL", "item_name": "Floor tiles ok", "trade": "TILING", "sort_order": 3},
    {"room_type": "BALCONY", "category": "CIVIL", "item_name": "Railing strong & proper height", "trade": "CIVIL", "sort_order": 4},
    {"room_type": "BALCONY", "category": "DOORS_WINDOWS", "item_name": "Sliding door works properly", "trade": "CARPENTRY", "sort_order": 5},
    # COMMON_AREA
    {"room_type": "COMMON_AREA", "category": "ELECTRICAL", "item_name": "Switches working", "trade": "ELECTRICAL", "sort_order": 1},
    {"room_type": "COMMON_AREA", "category": "ELECTRICAL", "item_name": "Lights working", "trade": "ELECTRICAL", "sort_order": 2},
    {"room_type": "COMMON_AREA", "category": "PAINT", "item_name": "Wall paint finish", "trade": "PAINTING", "sort_order": 3},
    {"room_type": "COMMON_AREA", "category": "CIVIL", "item_name": "Floor tiles ok", "trade": "TILING", "sort_order": 4},
]

# Demo contractor users seeded alongside the project hierarchy so Phase 2 API
# work has realistic role=CONTRACTOR rows to test against. All share the same
# demo password; reset via user-management API after rollout.
DEMO_CONTRACTOR_PASSWORD = "contractor123"
DEMO_CONTRACTORS: list[dict] = [
    {
        "username": "rajan-mehta",
        "full_name": "Rajan Mehta",
        "email": "rajan.mehta@demo.in",
        "phone": "+91-9800001001",
        "company": "Mehta Plumbing Services",
        "trades": ["PLUMBING"],
    },
    {
        "username": "priya-electrical",
        "full_name": "Priya Electrical Works",
        "email": "priya@electrical-demo.in",
        "phone": "+91-9800001002",
        "company": "Priya Electricals Pvt Ltd",
        "trades": ["ELECTRICAL"],
    },
    {
        "username": "deepak-tilers",
        "full_name": "Deepak Tilers Co",
        "email": "deepak@tilers-demo.in",
        "phone": "+91-9800001003",
        "company": "Deepak Tile Works",
        "trades": ["TILING", "PAINTING"],
    },
    {
        "username": "suresh-civil",
        "full_name": "Suresh Civil & Carpentry",
        "email": "suresh@civil-demo.in",
        "phone": "+91-9800001004",
        "company": "Suresh Constructions",
        "trades": ["CIVIL", "CARPENTRY"],
    },
]

SEED_FLAT_TYPE_ROOMS: list[dict] = [
    # 1BHK
    {"flat_type": "1BHK", "room_type": "LIVING_ROOM", "label": "Living Room", "sort_order": 1},
    {"flat_type": "1BHK", "room_type": "BEDROOM", "label": "Bedroom", "sort_order": 2},
    {"flat_type": "1BHK", "room_type": "KITCHEN", "label": "Kitchen", "sort_order": 3},
    {"flat_type": "1BHK", "room_type": "BATHROOM", "label": "Bathroom", "sort_order": 4},
    {"flat_type": "1BHK", "room_type": "BALCONY", "label": "Balcony", "sort_order": 5},
    # 2BHK
    {"flat_type": "2BHK", "room_type": "LIVING_ROOM", "label": "Living Room", "sort_order": 1},
    {"flat_type": "2BHK", "room_type": "BEDROOM", "label": "Bedroom 1", "sort_order": 2},
    {"flat_type": "2BHK", "room_type": "BEDROOM", "label": "Bedroom 2", "sort_order": 3},
    {"flat_type": "2BHK", "room_type": "KITCHEN", "label": "Kitchen", "sort_order": 4},
    {"flat_type": "2BHK", "room_type": "BATHROOM", "label": "Bathroom 1", "sort_order": 5},
    {"flat_type": "2BHK", "room_type": "BATHROOM", "label": "Bathroom 2", "sort_order": 6},
    {"flat_type": "2BHK", "room_type": "BALCONY", "label": "Balcony", "sort_order": 7},
    # 3BHK
    {"flat_type": "3BHK", "room_type": "LIVING_ROOM", "label": "Living Room", "sort_order": 1},
    {"flat_type": "3BHK", "room_type": "BEDROOM", "label": "Master Bedroom", "sort_order": 2},
    {"flat_type": "3BHK", "room_type": "BEDROOM", "label": "Bedroom 2", "sort_order": 3},
    {"flat_type": "3BHK", "room_type": "BEDROOM", "label": "Bedroom 3", "sort_order": 4},
    {"flat_type": "3BHK", "room_type": "KITCHEN", "label": "Kitchen", "sort_order": 5},
    {"flat_type": "3BHK", "room_type": "BATHROOM", "label": "Master Bathroom", "sort_order": 6},
    {"flat_type": "3BHK", "room_type": "BATHROOM", "label": "Bathroom 2", "sort_order": 7},
    {"flat_type": "3BHK", "room_type": "BATHROOM", "label": "Bathroom 3", "sort_order": 8},
    {"flat_type": "3BHK", "room_type": "BALCONY", "label": "Balcony 1", "sort_order": 9},
    {"flat_type": "3BHK", "room_type": "BALCONY", "label": "Balcony 2", "sort_order": 10},
]

SEED_FLOOR_PLAN_LAYOUTS: list[dict] = [
    # 2BHK
    {"flat_type": "2BHK", "room_label": "Bathroom 1", "x": 0.0, "y": 0.0, "width": 0.22, "height": 0.22},
    {"flat_type": "2BHK", "room_label": "Kitchen", "x": 0.0, "y": 0.22, "width": 0.22, "height": 0.36},
    {"flat_type": "2BHK", "room_label": "Bedroom 2", "x": 0.22, "y": 0.0, "width": 0.39, "height": 0.58},
    {"flat_type": "2BHK", "room_label": "Bedroom 1", "x": 0.61, "y": 0.0, "width": 0.39, "height": 0.58},
    {"flat_type": "2BHK", "room_label": "Living Room", "x": 0.0, "y": 0.58, "width": 0.72, "height": 0.30},
    {"flat_type": "2BHK", "room_label": "Bathroom 2", "x": 0.72, "y": 0.58, "width": 0.28, "height": 0.30},
    {"flat_type": "2BHK", "room_label": "Balcony", "x": 0.0, "y": 0.88, "width": 1.0, "height": 0.12},
    # 3BHK
    {"flat_type": "3BHK", "room_label": "Master Bathroom", "x": 0.0, "y": 0.0, "width": 0.18, "height": 0.22},
    {"flat_type": "3BHK", "room_label": "Kitchen", "x": 0.0, "y": 0.22, "width": 0.18, "height": 0.33},
    {"flat_type": "3BHK", "room_label": "Master Bedroom", "x": 0.18, "y": 0.0, "width": 0.32, "height": 0.55},
    {"flat_type": "3BHK", "room_label": "Bedroom 2", "x": 0.50, "y": 0.0, "width": 0.25, "height": 0.55},
    {"flat_type": "3BHK", "room_label": "Bedroom 3", "x": 0.75, "y": 0.0, "width": 0.25, "height": 0.55},
    {"flat_type": "3BHK", "room_label": "Living Room", "x": 0.0, "y": 0.55, "width": 0.50, "height": 0.30},
    {"flat_type": "3BHK", "room_label": "Bathroom 2", "x": 0.50, "y": 0.55, "width": 0.25, "height": 0.30},
    {"flat_type": "3BHK", "room_label": "Bathroom 3", "x": 0.75, "y": 0.55, "width": 0.25, "height": 0.30},
    {"flat_type": "3BHK", "room_label": "Balcony 1", "x": 0.0, "y": 0.85, "width": 0.50, "height": 0.15},
    {"flat_type": "3BHK", "room_label": "Balcony 2", "x": 0.50, "y": 0.85, "width": 0.50, "height": 0.15},
    # 1BHK
    {"flat_type": "1BHK", "room_label": "Bathroom", "x": 0.0, "y": 0.0, "width": 0.30, "height": 0.28},
    {"flat_type": "1BHK", "room_label": "Kitchen", "x": 0.0, "y": 0.28, "width": 0.30, "height": 0.30},
    {"flat_type": "1BHK", "room_label": "Bedroom", "x": 0.30, "y": 0.0, "width": 0.70, "height": 0.58},
    {"flat_type": "1BHK", "room_label": "Living Room", "x": 0.0, "y": 0.58, "width": 1.0, "height": 0.30},
    {"flat_type": "1BHK", "room_label": "Balcony", "x": 0.0, "y": 0.88, "width": 1.0, "height": 0.12},
]

SEED_PROJECTS: list[dict] = [
    {"name": "Godrej Aria", "location": "Sector 79, Gurugram", "towers": 12},
    {"name": "Godrej Woods", "location": "Sector 43, Noida", "towers": 10},
]

FLOORS_PER_BUILDING = 10
FLATS_PER_FLOOR = [
    {"type": "2BHK", "position": 1},
    {"type": "3BHK", "position": 2},
    {"type": "2BHK", "position": 3},
]


# ---------------------------------------------------------------------------
# Checklist Template CRUD
# ---------------------------------------------------------------------------


@router.get("/checklist-templates", response_model=list[ChecklistTemplateResponse])
async def list_checklist_templates(
    _user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> list[ChecklistTemplateResponse]:
    result = await db.execute(
        select(ChecklistTemplate).order_by(
            ChecklistTemplate.room_type, ChecklistTemplate.sort_order
        )
    )
    templates = result.scalars().all()
    return [ChecklistTemplateResponse.model_validate(t) for t in templates]


@router.post(
    "/checklist-templates",
    response_model=ChecklistTemplateResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_checklist_template(
    body: ChecklistTemplateCreate,
    _manager: Annotated[User, Depends(require_manager)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ChecklistTemplateResponse:
    template = ChecklistTemplate(
        project_id=body.project_id,
        room_type=body.room_type,
        category=body.category,
        item_name=body.item_name,
        trade=body.trade,
        sort_order=body.sort_order,
        is_active=body.is_active,
    )
    db.add(template)
    await db.commit()
    await db.refresh(template)
    return ChecklistTemplateResponse.model_validate(template)


@router.patch(
    "/checklist-templates/{template_id}",
    response_model=ChecklistTemplateResponse,
)
async def update_checklist_template(
    template_id: uuid.UUID,
    body: ChecklistTemplateUpdate,
    _manager: Annotated[User, Depends(require_manager)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ChecklistTemplateResponse:
    result = await db.execute(
        select(ChecklistTemplate).where(ChecklistTemplate.id == template_id)
    )
    template = result.scalars().first()
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")

    for field in ("room_type", "category", "item_name", "trade", "sort_order", "is_active"):
        value = getattr(body, field, None)
        if value is not None:
            setattr(template, field, value)

    await db.commit()
    await db.refresh(template)
    return ChecklistTemplateResponse.model_validate(template)


@router.delete(
    "/checklist-templates/{template_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_checklist_template(
    template_id: uuid.UUID,
    _manager: Annotated[User, Depends(require_manager)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> None:
    result = await db.execute(
        select(ChecklistTemplate).where(ChecklistTemplate.id == template_id)
    )
    template = result.scalars().first()
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")

    await db.delete(template)
    await db.commit()


@router.post("/checklist-templates/seed-defaults", status_code=status.HTTP_201_CREATED)
async def seed_defaults(
    _manager: Annotated[User, Depends(require_manager)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    """Seed the default checklist templates, flat type rooms, and floor plan layouts."""
    # Check if already seeded
    existing = await db.execute(select(ChecklistTemplate).limit(1))
    if existing.scalars().first():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Defaults already seeded. Delete existing data before re-seeding.",
        )

    # Seed checklist templates
    for item in SEED_CHECKLIST_ITEMS:
        template = ChecklistTemplate(
            project_id=None,
            room_type=item["room_type"],
            category=item["category"],
            item_name=item["item_name"],
            trade=item["trade"],
            sort_order=item["sort_order"],
        )
        db.add(template)

    # Seed flat type rooms
    for room in SEED_FLAT_TYPE_ROOMS:
        ftr = FlatTypeRoom(
            project_id=None,
            flat_type=room["flat_type"],
            room_type=room["room_type"],
            label=room["label"],
            sort_order=room["sort_order"],
        )
        db.add(ftr)

    # Seed floor plan layouts
    for layout in SEED_FLOOR_PLAN_LAYOUTS:
        fpl = FloorPlanLayout(
            project_id=None,
            flat_type=layout["flat_type"],
            room_label=layout["room_label"],
            x=layout["x"],
            y=layout["y"],
            width=layout["width"],
            height=layout["height"],
        )
        db.add(fpl)

    await db.commit()

    return {
        "detail": "Defaults seeded",
        "checklist_templates": len(SEED_CHECKLIST_ITEMS),
        "flat_type_rooms": len(SEED_FLAT_TYPE_ROOMS),
        "floor_plan_layouts": len(SEED_FLOOR_PLAN_LAYOUTS),
    }


@router.post("/seed-hierarchy", status_code=status.HTTP_201_CREATED)
async def seed_hierarchy(
    _manager: Annotated[User, Depends(require_manager)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict:
    """Seed dummy projects, buildings, floors, and flats matching the Android SeedData."""
    # Check if projects already exist to avoid duplicate seeding
    existing = await db.execute(select(Project).limit(1))
    if existing.scalars().first():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Projects already exist. Delete existing data before re-seeding.",
        )

    project_count = 0
    building_count = 0
    floor_count = 0
    flat_count = 0

    for proj_data in SEED_PROJECTS:
        project = Project(name=proj_data["name"], location=proj_data["location"])
        db.add(project)
        await db.flush()  # get project.id
        project_count += 1

        tower_count: int = proj_data["towers"]
        for t in range(1, tower_count + 1):
            letter = chr(ord("A") + (t - 1) % 26)
            tower_name = f"Tower {letter}{t}" if tower_count > 26 else f"Tower {letter}"

            building = Building(project_id=project.id, name=tower_name)
            db.add(building)
            await db.flush()
            building_count += 1

            for f in range(1, FLOORS_PER_BUILDING + 1):
                floor = Floor(building_id=building.id, floor_number=f)
                db.add(floor)
                await db.flush()
                floor_count += 1

                for flat_def in FLATS_PER_FLOOR:
                    flat_number = f"{f}0{flat_def['position']}"
                    flat = Flat(
                        floor_id=floor.id,
                        flat_number=flat_number,
                        flat_type=flat_def["type"],
                    )
                    db.add(flat)
                    await db.flush()  # assign flat.id before init
                    await initialize_flat_checklist(flat.id, db)
                    await recompute_flat_inspection_status(flat.id, db)
                    flat_count += 1

    # Seed demo contractor users (role=CONTRACTOR) so Phase 2 API work has
    # realistic rows to test against. Skip any that already exist by username.
    demo_password_hash = hash_password(DEMO_CONTRACTOR_PASSWORD)
    demo_contractor_count = 0
    for contractor in DEMO_CONTRACTORS:
        existing_user = await db.scalar(
            select(User).where(User.username == contractor["username"])
        )
        if existing_user is not None:
            continue
        db.add(
            User(
                username=contractor["username"],
                password_hash=demo_password_hash,
                full_name=contractor["full_name"],
                role="CONTRACTOR",
                email=contractor["email"],
                phone=contractor["phone"],
                company=contractor["company"],
                trades=contractor["trades"],
                is_active=True,
            )
        )
        demo_contractor_count += 1

    await db.commit()

    return {
        "detail": "Hierarchy seeded",
        "projects": project_count,
        "buildings": building_count,
        "floors": floor_count,
        "flats": flat_count,
        "demo_contractors": demo_contractor_count,
    }


# ---------------------------------------------------------------------------
# Flat Type Room CRUD
# ---------------------------------------------------------------------------


@router.get("/flat-type-rooms", response_model=list[FlatTypeRoomResponse])
async def list_flat_type_rooms(
    _user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> list[FlatTypeRoomResponse]:
    result = await db.execute(
        select(FlatTypeRoom).order_by(FlatTypeRoom.flat_type, FlatTypeRoom.sort_order)
    )
    rooms = result.scalars().all()
    return [FlatTypeRoomResponse.model_validate(r) for r in rooms]


@router.post(
    "/flat-type-rooms",
    response_model=FlatTypeRoomResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_flat_type_room(
    body: FlatTypeRoomCreate,
    _manager: Annotated[User, Depends(require_manager)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> FlatTypeRoomResponse:
    room = FlatTypeRoom(
        project_id=body.project_id,
        flat_type=body.flat_type,
        room_type=body.room_type,
        label=body.label,
        sort_order=body.sort_order,
    )
    db.add(room)
    await db.commit()
    await db.refresh(room)
    return FlatTypeRoomResponse.model_validate(room)


@router.patch("/flat-type-rooms/{room_id}", response_model=FlatTypeRoomResponse)
async def update_flat_type_room(
    room_id: uuid.UUID,
    body: FlatTypeRoomUpdate,
    _manager: Annotated[User, Depends(require_manager)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> FlatTypeRoomResponse:
    result = await db.execute(
        select(FlatTypeRoom).where(FlatTypeRoom.id == room_id)
    )
    room = result.scalars().first()
    if not room:
        raise HTTPException(status_code=404, detail="Flat type room not found")

    for field in ("flat_type", "room_type", "label", "sort_order"):
        value = getattr(body, field, None)
        if value is not None:
            setattr(room, field, value)

    await db.commit()
    await db.refresh(room)
    return FlatTypeRoomResponse.model_validate(room)


@router.delete(
    "/flat-type-rooms/{room_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def delete_flat_type_room(
    room_id: uuid.UUID,
    _manager: Annotated[User, Depends(require_manager)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> None:
    result = await db.execute(
        select(FlatTypeRoom).where(FlatTypeRoom.id == room_id)
    )
    room = result.scalars().first()
    if not room:
        raise HTTPException(status_code=404, detail="Flat type room not found")

    await db.delete(room)
    await db.commit()


# ---------------------------------------------------------------------------
# Floor Plan Layout CRUD
# ---------------------------------------------------------------------------


@router.get("/floor-plan-layouts", response_model=list[FloorPlanLayoutResponse])
async def list_floor_plan_layouts(
    _user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> list[FloorPlanLayoutResponse]:
    result = await db.execute(
        select(FloorPlanLayout).order_by(
            FloorPlanLayout.flat_type, FloorPlanLayout.room_label
        )
    )
    layouts = result.scalars().all()
    return [FloorPlanLayoutResponse.model_validate(l) for l in layouts]


@router.post(
    "/floor-plan-layouts",
    response_model=FloorPlanLayoutResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_floor_plan_layout(
    body: FloorPlanLayoutCreate,
    _manager: Annotated[User, Depends(require_manager)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> FloorPlanLayoutResponse:
    layout = FloorPlanLayout(
        project_id=body.project_id,
        flat_type=body.flat_type,
        room_label=body.room_label,
        x=body.x,
        y=body.y,
        width=body.width,
        height=body.height,
    )
    db.add(layout)
    await db.commit()
    await db.refresh(layout)
    return FloorPlanLayoutResponse.model_validate(layout)


@router.patch(
    "/floor-plan-layouts/{layout_id}", response_model=FloorPlanLayoutResponse
)
async def update_floor_plan_layout(
    layout_id: uuid.UUID,
    body: FloorPlanLayoutUpdate,
    _manager: Annotated[User, Depends(require_manager)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> FloorPlanLayoutResponse:
    result = await db.execute(
        select(FloorPlanLayout).where(FloorPlanLayout.id == layout_id)
    )
    layout = result.scalars().first()
    if not layout:
        raise HTTPException(status_code=404, detail="Floor plan layout not found")

    for field in ("flat_type", "room_label", "x", "y", "width", "height"):
        value = getattr(body, field, None)
        if value is not None:
            setattr(layout, field, value)

    await db.commit()
    await db.refresh(layout)
    return FloorPlanLayoutResponse.model_validate(layout)


@router.delete(
    "/floor-plan-layouts/{layout_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def delete_floor_plan_layout(
    layout_id: uuid.UUID,
    _manager: Annotated[User, Depends(require_manager)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> None:
    result = await db.execute(
        select(FloorPlanLayout).where(FloorPlanLayout.id == layout_id)
    )
    layout = result.scalars().first()
    if not layout:
        raise HTTPException(status_code=404, detail="Floor plan layout not found")

    await db.delete(layout)
    await db.commit()
