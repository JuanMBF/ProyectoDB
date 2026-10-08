"""analytics views

Views used by the reports. Written in SQL that runs unchanged on MySQL 8 and SQLite 3.25+
(window functions and CTEs), so the same queries are tested locally and run in production.

Revision ID: 5b1e9c3a7d42
Revises: 2d8ab7e39911
Create Date: 2026-10-08 18:30:00.000000

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "5b1e9c3a7d42"
down_revision: str | Sequence[str] | None = "2d8ab7e39911"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Last capture of every listing (price is NULL when it was seen without price or stock).
V_LATEST_PRICES = """
CREATE VIEW v_latest_prices AS
SELECT ranked.listing_id, ranked.supermarket_id, ranked.product_id, ranked.price, ranked.regular_price,
       ranked.promo_text, ranked.is_available, ranked.captured_at
FROM (
    SELECT s.listing_id, l.supermarket_id, l.product_id, s.price, s.regular_price, s.promo_text,
           s.is_available, s.captured_at,
           ROW_NUMBER() OVER (PARTITION BY s.listing_id ORDER BY s.captured_at DESC, s.id DESC) AS rn
    FROM price_snapshots s
    JOIN listings l ON l.id = s.listing_id
) ranked
WHERE ranked.rn = 1
"""

# Same product (same barcode) in two or more supermarkets, ranked by current price.
V_PRICE_COMPARISON = """
CREATE VIEW v_price_comparison AS
WITH per_chain AS (
    SELECT lp.product_id, lp.supermarket_id, lp.listing_id, lp.price, lp.regular_price, lp.promo_text,
           lp.captured_at,
           ROW_NUMBER() OVER (
               PARTITION BY lp.product_id, lp.supermarket_id ORDER BY lp.price, lp.listing_id
           ) AS rn
    FROM v_latest_prices lp
    WHERE lp.product_id IS NOT NULL AND lp.is_available AND lp.price IS NOT NULL
),
ranked AS (
    SELECT pc.product_id, pc.supermarket_id, pc.listing_id, pc.price, pc.regular_price, pc.promo_text,
           pc.captured_at,
           RANK() OVER (PARTITION BY pc.product_id ORDER BY pc.price) AS price_rank,
           MIN(pc.price) OVER (PARTITION BY pc.product_id) AS best_price,
           COUNT(*) OVER (PARTITION BY pc.product_id) AS chains
    FROM per_chain pc
    WHERE pc.rn = 1
)
SELECT p.id AS product_id, p.ean, p.name AS product, p.brand, sm.code AS supermarket, r.listing_id,
       r.price, r.regular_price, r.promo_text, r.price_rank, r.best_price,
       r.price - r.best_price AS diff_vs_best,
       ROUND(100.0 * (r.price - r.best_price) / r.best_price, 1) AS pct_vs_best,
       r.chains, r.captured_at
FROM ranked r
JOIN products p ON p.id = r.product_id
JOIN supermarkets sm ON sm.id = r.supermarket_id
WHERE r.chains >= 2
"""

# Every time a listing changed its price between two consecutive captures.
V_PRICE_CHANGES = """
CREATE VIEW v_price_changes AS
SELECT c.listing_id, sm.code AS supermarket, l.name AS product, l.ean, c.captured_at,
       c.previous_price, c.price,
       c.price - c.previous_price AS change_amount,
       ROUND(100.0 * (c.price - c.previous_price) / c.previous_price, 1) AS change_pct
FROM (
    SELECT s.listing_id, s.captured_at, s.price,
           LAG(s.price) OVER (PARTITION BY s.listing_id ORDER BY s.captured_at, s.id) AS previous_price
    FROM price_snapshots s
    WHERE s.price IS NOT NULL
) c
JOIN listings l ON l.id = c.listing_id
JOIN supermarkets sm ON sm.id = l.supermarket_id
WHERE c.previous_price IS NOT NULL AND c.price <> c.previous_price
"""


def upgrade() -> None:
    op.execute(V_LATEST_PRICES)
    op.execute(V_PRICE_COMPARISON)
    op.execute(V_PRICE_CHANGES)


def downgrade() -> None:
    op.execute("DROP VIEW v_price_changes")
    op.execute("DROP VIEW v_price_comparison")
    op.execute("DROP VIEW v_latest_prices")
