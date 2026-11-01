from __future__ import annotations

import logging
from pathlib import Path
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

    add_column("orders", "payment_amount", "NUMERIC(12,2)")
    add_column("orders", "payment_currency", "VARCHAR(8)")
    add_column("orders", "payment_address", "VARCHAR(96)")
    add_column("orders", "payment_network", "VARCHAR(16)")
    if "orders" in inspector.get_table_names() and engine.dialect.name == "postgresql":
        with engine.begin() as connection:
            connection.execute(text("ALTER TABLE orders ALTER COLUMN payment_amount TYPE NUMERIC(24,8)"))
    if "vcc_cards" in inspector.get_table_names() and engine.dialect.name == "postgresql":
        with engine.begin() as connection:
            connection.execute(text("ALTER TABLE vcc_cards ALTER COLUMN available_balance TYPE NUMERIC(12,2)"))
            connection.execute(text("ALTER TABLE vcc_cards ALTER COLUMN reserved_balance TYPE NUMERIC(12,2)"))
    if "payment_events" in inspector.get_table_names() and engine.dialect.name == "postgresql":
        with engine.begin() as connection:
            connection.execute(text("ALTER TABLE payment_events ALTER COLUMN amount TYPE NUMERIC(24,8)"))
    add_column("activation_codes", "preferred_region", "VARCHAR(16) NOT NULL DEFAULT 'PH_FIRST'")
    add_column("recharge_tasks", "executed_channel", "VARCHAR(16)")
    add_column("recharge_tasks", "executed_region", "VARCHAR(4)")
    add_column("recharge_tasks", "billed_currency", "VARCHAR(8)")
    add_column("recharge_tasks", "billed_amount", "NUMERIC(10,2)")
    add_column("recharge_tasks", "fallback_reason", "VARCHAR(256)")
    add_column("vcc_cards", "card_fingerprint", "VARCHAR(64)")
    add_column("vcc_cards", "ph_billing_address_json", "JSON")
    add_column("vcc_cards", "three_ds_email_account", "VARCHAR(128)")
    add_column("vcc_cards", "reserved_balance", "NUMERIC(12,2) NOT NULL DEFAULT 0")
    add_column("vcc_cards", "daily_limit", "INTEGER")
    add_column("vcc_cards", "consecutive_failures", "INTEGER NOT NULL DEFAULT 0")
    add_column("vcc_cards", "cooldown_until", "TIMESTAMP")
    add_column("vcc_cards", "last_error", "VARCHAR(256)")
    add_column("vcc_cards", "last_used_at", "TIMESTAMP")
    add_column("vcc_cards", "is_locked", "BOOLEAN NOT NULL DEFAULT FALSE")
    add_column("vcc_cards", "locked_at", "TIMESTAMP")
    add_column("vcc_cards", "total_used_count", "INTEGER NOT NULL DEFAULT 0")

    if "vcc_cards" in inspector.get_table_names():
        _backfill_card_fingerprints()
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


def _alembic_config():
    from alembic.config import Config

    base_dir = Path(__file__).resolve().parent.parent
    config = Config(str(base_dir / "alembic.ini"))
    config.set_main_option("script_location", str(base_dir / "alembic"))
    config.set_main_option("sqlalchemy.url", settings.database_url)
    return config


def _run_migrations() -> None:
    """Bring an existing database up to head, stamping pre-Alembic installs.

    Installations created before Alembic already have every table at the
    baseline revision. They are stamped rather than re-created, and only the
    revisions after the baseline run. Fresh databases run everything.
    """
    from alembic import command

    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())
    has_version_table = "alembic_version" in existing_tables
    config = _alembic_config()
    if not existing_tables:
        command.upgrade(config, "head")
        return
    if not has_version_table:
        if "orders" not in existing_tables:
            # Partially built database; let upgrade create what is missing.
            command.upgrade(config, "head")
            return
        logger.info("Stamping pre-Alembic database at baseline revision")
        command.stamp(config, "2a8f730946fe")
    command.upgrade(config, "head")


def _backfill_card_fingerprints() -> None:
    """Fill ``card_fingerprint`` for cards created before the column existed."""
    from app.services.crypto import card_fingerprint, decrypt_text
    from app.models import VccCard

    with SessionLocal() as db:
        rows = (
            db.query(VccCard)
            .filter(VccCard.card_fingerprint.is_(None))
            .limit(2000)
            .all()
        )
        if not rows:
            return
        for card in rows:
            try:
                card.card_fingerprint = card_fingerprint(decrypt_text(card.card_number_enc))
            except Exception:
                logger.warning("Unable to fingerprint vcc card id=%s", card.id)
            db.add(card)
        db.commit()
        logger.info("Backfilled %s card fingerprints", len(rows))


def init_db() -> None:
    """Migrate the schema and seed the demo catalogue when the database is empty."""
    from app import models  # noqa: F401  (register metadata)

    try:
        _run_migrations()
    except Exception:
        # A migration failure must not lock the store; the additive
        # compatibility pass below still runs so the app can serve.
        logger.exception("Alembic migration failed; falling back to compatibility pass")
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
