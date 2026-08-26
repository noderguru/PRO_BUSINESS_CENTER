import logging
import uuid

from fastapi import Depends, FastAPI, Request
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.api.sessions import router as sessions_router
from app.errors import StorageUnavailableError, register_handlers

logging.basicConfig(level=get_settings().log_level)

app = FastAPI(title="Chat Sessions Service", version="0.1.0")
register_handlers(app)
app.include_router(sessions_router)


@app.middleware("http")
async def request_id_middleware(request: Request, call_next):
    request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
    request.state.request_id = request_id
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    return response


@app.get("/health")
def health(db: Session = Depends(get_db)) -> dict:
    try:
        db.execute(text("SELECT 1"))
    except SQLAlchemyError:
        raise StorageUnavailableError("Database connection failed")
    return {"status": "ok"}
