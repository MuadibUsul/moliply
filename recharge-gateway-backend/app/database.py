from __future__ import annotations

import logging
from typing import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import settings

logger = logging.getLogger(__name__)

connect_args: dict = {}
if settings.database_url.startswith("sqlite"):
    connect_args["check_same_thread"] = False

engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,
    future=True,
    connect_args=connect_args,
)
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False, expire_on_commit=False, class_=Session)


class Base(DeclarativeBase):
    pass


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    """Create tables and seed the demo catalogue when the database is empty."""
    from app import models  # noqa: F401  (register metadata)

    Base.metadata.create_all(bind=engine)
    if settings.seed_demo_data:
        from app.seed import seed_demo_data

        with SessionLocal() as db:
            seed_demo_data(db)
    if settings.admin_password == "change-me":
        logger.warning("ADMIN_PASSWORD is still the development default. Change it before deploying.")


def session_scope() -> Session:
    """Return a session for background workers. Caller owns commit/close."""
    return SessionLocal()
