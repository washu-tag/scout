from fastapi import APIRouter
from pydantic import BaseModel

from ..config import settings


router = APIRouter(prefix="/api", tags=["config"])


class AppConfig(BaseModel):
    chatOrigin: str


@router.get("/config")
def get_config() -> AppConfig:
    return AppConfig(chatOrigin=settings.chat_origin)
