from app.models.inspection import InspectionEntry
from app.schemas.inspection import (
    ContractorAssignmentBrief,
    InspectionEntryResponse,
)


def entry_to_response(entry: InspectionEntry) -> InspectionEntryResponse:
    """Build an InspectionEntryResponse including contractor_assignments with
    denormalized contractor name + trades. Callers must have eager-loaded the
    full relationship chain (entry.contractor_assignments → assignment.contractor)
    before invoking this — otherwise Pydantic will touch unloaded ORM attributes
    and SQLAlchemy will raise under the async session."""
    response = InspectionEntryResponse.model_validate(entry)
    response.contractor_assignments = [
        ContractorAssignmentBrief(
            id=assignment.id,
            inspection_entry_id=assignment.inspection_entry_id,
            contractor_id=assignment.contractor_id,
            contractor_name=assignment.contractor.full_name,
            contractor_trades=assignment.contractor.trades or [],
            assigned_at=assignment.assigned_at,
            due_date=assignment.due_date,
            notes=assignment.notes,
        )
        for assignment in entry.contractor_assignments
    ]
    return response
