"""Add the new commercial SKUs to a live catalogue.

    python -m scripts.add_launch_products

Production runs with ``SEED_DEMO_DATA=false``, so :func:`seed_demo_data` never
fires and the new products would be missing after a deploy. This inserts only
the slugs that are absent and never touches an existing row, so a price already
adjusted in the admin console survives.

It also sets the JIT product's strategy, which is the whole point of that SKU.
"""

from __future__ import annotations

import logging

from app.database import SessionLocal, init_db
from app.models import FulfillmentStrategy, Product
from app.seed import build_icebreaker, build_jit_account

logging.basicConfig(level="INFO")

NEW_PRODUCTS = (
    ("ai-icebreaker-trial", build_icebreaker),
    ("chatgpt-plus-jit-account", build_jit_account),
)


def main() -> None:
    init_db()
    created = 0
    with SessionLocal() as db:
        for slug, factory in NEW_PRODUCTS:
            if db.query(Product.id).filter(Product.slug == slug).first():
                continue
            db.add(factory())
            created += 1
        db.commit()

        # The JIT strategy is a property of the SKU, not of the seed run, so keep
        # it correct even when the row already existed.
        jit = db.query(Product).filter(Product.slug == "chatgpt-plus-jit-account").first()
        if jit and jit.fulfillment_strategy != FulfillmentStrategy.JIT_ACCOUNT_RECHARGE:
            jit.fulfillment_strategy = FulfillmentStrategy.JIT_ACCOUNT_RECHARGE
            db.commit()
    print(f"created {created} products")


if __name__ == "__main__":
    main()
