import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db
from app.models.inspection import InspectionVideo, VideoFrameAnalysis
from app.models.user import User
from app.schemas.ai import (
    AnalyzeVideoFrameRequest,
    AnalyzeVideoFrameResponse,
    DescribeSnagRequest,
    DescribeSnagResponse,
)
from app.services.ai_service import ai_service

router = APIRouter(prefix="/ai", tags=["ai"])


@router.post("/describe-snag", response_model=DescribeSnagResponse)
async def describe_snag(
    body: DescribeSnagRequest,
    _user: Annotated[User, Depends(get_current_user)],
) -> DescribeSnagResponse:
    if not body.image_minio_key and not body.image_base64:
        raise HTTPException(
            status_code=400,
            detail="Provide either image_minio_key or image_base64",
        )

    try:
        description = await ai_service.describe_snag(
            item_name=body.item_name,
            category=body.category,
            room_label=body.room_label,
            image_minio_key=body.image_minio_key,
            image_base64=body.image_base64,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc))

    return DescribeSnagResponse(description=description)


@router.post("/analyze-video-frame", response_model=AnalyzeVideoFrameResponse)
async def analyze_video_frame(
    body: AnalyzeVideoFrameRequest,
    _user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> AnalyzeVideoFrameResponse:
    # Validate video exists
    result = await db.execute(
        select(InspectionVideo).where(InspectionVideo.id == body.video_id)
    )
    video = result.scalars().first()
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")

    try:
        description = await ai_service.analyze_video_frame(
            frame_base64=body.frame_base64,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc))

    # Save the analysis
    analysis = VideoFrameAnalysis(
        video_id=body.video_id,
        timestamp_ms=body.timestamp_ms,
        description=description,
    )
    db.add(analysis)
    await db.commit()
    await db.refresh(analysis)

    return AnalyzeVideoFrameResponse(
        description=description,
        frame_analysis_id=analysis.id,
    )
