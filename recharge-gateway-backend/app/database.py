from __future__ import annotations

import logging
from typing import Generator

from sqlalchemy import create_engine, inspect, text
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


def ensure_schema_compatibility() -> None:
    """Apply the one additive migration needed by existing installations.

    ``create_all`` creates new tables (users) but does not add columns to an
    existing orders table. This adds orders.user_id once, without touching
    existing rows. Fresh databases already have the column from metadata.
    """
    inspector = inspect(engine)
    if "orders" not in inspector.get_table_names():
        return
    columns = {column["name"] for column in inspector.get_columns("orders")}
    if "user_id" not in columns:
        with engine.begin() as connection:
            connection.execute(text("ALTER TABLE orders ADD COLUMN user_id INTEGER"))
            connection.execute(text("CREATE INDEX IF NOT EXISTS ix_orders_user_id ON orders (user_id)"))
        logger.info("Schema compatibility: added orders.user_id for customer accounts")

    def add_column(table: str, column: str, ddl: str) -> None:
        if table not in inspector.get_table_names():
            return
        existing = {item["name"] for item in inspector.get_columns(table)}
        if column in existing:
            return
        with engine.begin() as connection:
            connection.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"))
        logger.info("Schema compatibility: added %s.%s", table, column)

    add_column("activation_codes", "preferred_region", "VARCHAR(16) NOT NULL DEFAULT 'PH_FIRST'")
    add_column("recharge_tasks", "executed_channel", "VARCHAR(16)")
    add_column("recharge_tasks", "executed_region", "VARCHAR(4)")
    add_column("recharge_tasks", "billed_currency", "VARCHAR(8)")
    add_column("recharge_tasks", "billed_amount", "NUMERIC(10,2)")
    add_column("recharge_tasks", "fallback_reason", "VARCHAR(256)")
    add_column("vcc_cards", "ph_billing_address_json", "JSON")
    add_column("vcc_cards", "three_ds_email_account", "VARCHAR(128)")

    if "vcc_cards" in inspector.get_table_names():
        # Earlier builds stored Python None as JSON ``null``; normalise it to
        # SQL NULL so PH card scheduling filters correctly.
        with engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE vcc_cards SET ph_billing_address_json = NULL "
                    "WHERE ph_billing_address_json IS NOT NULL "
                    "AND CAST(ph_billing_address_json AS TEXT) = 'null'"
                )
            )


def init_db() -> None:
    """Create tables and seed the demo catalogue when the database is empty."""
    from app import models  # noqa: F401  (register metadata)

    Base.metadata.create_all(bind=engine)
    ensure_schema_compatibility()
    if settings.seed_demo_data:
        from app.seed import seed_demo_data

        with SessionLocal() as db:
            seed_demo_data(db)
    if settings.admin_password == "change-me":
        logger.warning("ADMIN_PASSWORD is still the development default. Change it before deploying.")


def session_scope() -> Session:
    """Return a session for background workers. Caller owns commit/close."""
    return SessionLocal()
