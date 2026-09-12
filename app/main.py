from fastapi import FastAPI

from app.core.config import get_settings
from app.api.v1.router import router as v1_router

settings = get_settings()
app = FastAPI(title=settings.app_name, version="0.1.0", debug=settings.debug)
app.include_router(v1_router)
