from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/ingest", tags=["ingest"])


@router.post("")
async def ingest_payload() -> dict[str, str]:
    raise HTTPException(status_code=501, detail="Not Implemented")

