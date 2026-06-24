from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/evidence", tags=["evidence"])


@router.get("")
async def list_evidence() -> dict[str, str]:
    raise HTTPException(status_code=501, detail="Not Implemented")

