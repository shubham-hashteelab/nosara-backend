import uuid
from datetime import date, datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict


class ContractorCreate(BaseModel):
    name: str
    company: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    specialty: Optional[str] = None


class ContractorUpdate(BaseModel):
    name: Optional[str] = None
    company: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    specialty: Optional[str] = None
    is_active: Optional[bool] = None


class ContractorResponse(BaseModel):
    id: uuid.UUID
    name: str
    company: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    specialty: Optional[str] = None
    is_active: bool
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class SnagContractorAssignmentCreate(BaseModel):
    due_date: Optional[date] = None
    notes: Optional[str] = None


class SnagContractorAssignmentResponse(BaseModel):
    id: uuid.UUID
    inspection_entry_id: uuid.UUID
    contractor_id: uuid.UUID
    contractor_name: str
    contractor_trades: list[str] = []
    assigned_at: datetime
    due_date: Optional[date] = None
    notes: Optional[str] = None


class OrphanedAssignmentResponse(BaseModel):
    assignment_id: uuid.UUID
    inspection_entry_id: uuid.UUID
    contractor_id: uuid.UUID
    contractor_name: str
    contractor_role: str
    contractor_is_active: bool
    assigned_at: datetime
