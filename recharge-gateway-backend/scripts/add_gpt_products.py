"""Add the GPT Pro tiers / Codex SMS item to an existing catalogue.

    python -m scripts.add_gpt_products

Idempotent: existing slugs are skipped.
"""

from __future__ import annotations

import logging

from app.database import SessionLocal, init_db
from app.seed import upsert_gpt_pro_products

logging.basicConfig(level="INFO")


def main() -> None:
    init_db()
    with SessionLocal() as db:
        created = upsert_gpt_pro_products(db)
    print(f"created {created} products")


if __name__ == "__main__":
    main()
