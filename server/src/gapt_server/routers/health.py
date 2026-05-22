from fastapi import APIRouter
from pydantic import BaseModel

from gapt_server import __version__

router = APIRouter(tags=["meta"])


class HealthResponse(BaseModel):
    status: str
    version: str


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse(status="ok", version=__version__)


@router.get("/", response_model=HealthResponse)
async def root() -> HealthResponse:
    return HealthResponse(status="ok", version=__version__)
