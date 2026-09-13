"""Postgres (Supabase) access layer for the Signature inventory database."""

import os

import psycopg
import streamlit as st
from psycopg.rows import dict_row

CATEGORIES = ["ΓΥΝΑΙΚΕΙΑ", "ΑΝΤΡΙΚΑ", "UNISEX", "ΑΡΩΜΑΤΙΚΑ ΧΩΡΟΥ / ΑΥΤ"]

PRODUCT_TYPES = ["έλαια", "άρωμα"]

CATEGORY_PREFIX = {
    "ΓΥΝΑΙΚΕΙΑ": "W",
    "ΑΝΤΡΙΚΑ": "M",
    "UNISEX": "U",
    "ΑΡΩΜΑΤΙΚΑ ΧΩΡΟΥ / ΑΥΤ": "ΑΧ",
}
TYPE_LETTER = {"έλαια": "E", "άρωμα": "P"}


def unit_for(category: str) -> str:
    """Stock unit for a category: room fragrances are measured in pieces, everything else in ml."""
    return "τεμάχια" if category == "ΑΡΩΜΑΤΙΚΑ ΧΩΡΟΥ / ΑΥΤ" else "ml"


def infer_product_type(code: str) -> str:
    """Guess a product type from an existing code (…E-nnn = έλαια, …P-nnn / ΑΧ- = άρωμα)."""
    code = (code or "").upper()
    if code.startswith("ΑΧ-") or code[1:2] == "P":
        return "άρωμα"
    return "έλαια"


def code_prefix(category: str, product_type: str) -> str:
    base = CATEGORY_PREFIX.get(category, "X")
    if base == "ΑΧ":
        return "ΑΧ-"
    return f"{base}{TYPE_LETTER.get(product_type, 'E')}-"


def suggest_code(category: str, product_type: str) -> str:
    """Next free code for a category/type, e.g. 'WP-049', based on existing codes."""
    prefix = code_prefix(category, product_type)
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT code FROM products WHERE code LIKE %s", (prefix + "%",)
        ).fetchall()
    nums = [int(r["code"][len(prefix):]) for r in rows if r["code"][len(prefix):].isdigit()]
    return f"{prefix}{(max(nums) + 1) if nums else 1:03d}"


def db_url() -> str:
    """Postgres connection string from st.secrets or the SIGNATURE_DB_URL env var."""
    try:
        if "postgres_url" in st.secrets:
            return str(st.secrets["postgres_url"])
    except Exception:
        pass
    url = os.environ.get("SIGNATURE_DB_URL")
    if not url:
        raise RuntimeError(
            "No database connection configured — set 'postgres_url' in st.secrets "
            "or the SIGNATURE_DB_URL environment variable."
        )
    return url


def get_connection() -> psycopg.Connection:
    # prepare_threshold=None disables server-side prepared statements, which
    # break under Supabase's transaction-mode connection pooler (pgbouncer).
    return psycopg.connect(db_url(), row_factory=dict_row, sslmode="require", prepare_threshold=None)


def init_db() -> None:
    with get_connection() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS products (
                id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                code TEXT NOT NULL UNIQUE,
                category TEXT NOT NULL,
                house TEXT,
                original_name TEXT,
                signature_name TEXT NOT NULL,
                stock_ml REAL NOT NULL DEFAULT 0,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        cols = {
            r["column_name"]
            for r in conn.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'products'"
            ).fetchall()
        }
        if "product_type" not in cols:
            conn.execute("ALTER TABLE products ADD COLUMN product_type TEXT")
            conn.execute(
                "UPDATE products SET product_type = 'άρωμα' "
                "WHERE code LIKE 'ΑΧ-%' OR substr(code, 2, 1) = 'P'"
            )
            conn.execute(
                "UPDATE products SET product_type = 'έλαια' WHERE product_type IS NULL"
            )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS purchases (
                id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                date TEXT NOT NULL,
                code TEXT NOT NULL REFERENCES products(code),
                ml REAL NOT NULL,
                comments TEXT,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS sales (
                id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                date TEXT NOT NULL,
                code TEXT NOT NULL REFERENCES products(code),
                ml REAL NOT NULL,
                comments TEXT,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )


def code_exists(code: str) -> bool:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT 1 FROM products WHERE code = %s", (code,)
        ).fetchone()
        return row is not None


def add_product(
    code: str,
    category: str,
    house: str,
    original_name: str,
    signature_name: str,
    stock_ml: float,
    product_type: str = "έλαια",
) -> None:
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO products
                (code, category, house, original_name, signature_name, stock_ml, product_type)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (code, category, house or None, original_name or None, signature_name,
             stock_ml, product_type),
        )


def get_all_products() -> list[dict]:
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM products ORDER BY category, code"
        ).fetchall()


def get_product(code: str) -> dict | None:
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM products WHERE code = %s", (code,)
        ).fetchone()


def add_purchase(
    date: str, code: str, ml: float, comments: str, adjust_stock: bool = True
) -> None:
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO purchases (date, code, ml, comments) VALUES (%s, %s, %s, %s)",
            (date, code, ml, comments or None),
        )
        if adjust_stock:
            conn.execute(
                "UPDATE products SET stock_ml = stock_ml + %s WHERE code = %s",
                (ml, code),
            )


def add_sale(
    date: str, code: str, ml: float, comments: str, adjust_stock: bool = True
) -> None:
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO sales (date, code, ml, comments) VALUES (%s, %s, %s, %s)",
            (date, code, ml, comments or None),
        )
        if adjust_stock:
            conn.execute(
                "UPDATE products SET stock_ml = stock_ml - %s WHERE code = %s",
                (ml, code),
            )


def get_all_purchases() -> list[dict]:
    with get_connection() as conn:
        return conn.execute(
            """
            SELECT p.id, p.date, p.code, pr.signature_name, pr.category, p.ml, p.comments
            FROM purchases p
            JOIN products pr ON pr.code = p.code
            ORDER BY p.date DESC, p.id DESC
            """
        ).fetchall()


def _product_filter(
    categories: list[str] | None, product_types: list[str] | None, alias: str = "pr"
) -> tuple[str, list]:
    """Build a 'WHERE …' clause (and params) restricting to the given categories/types."""
    clauses, params = [], []
    if categories:
        clauses.append(f"{alias}.category IN ({','.join('%s' * len(categories))})")
        params.extend(categories)
    if product_types:
        clauses.append(f"{alias}.product_type IN ({','.join('%s' * len(product_types))})")
        params.extend(product_types)
    return (" WHERE " + " AND ".join(clauses)) if clauses else "", params


def get_dashboard_stats(
    categories: list[str] | None = None, product_types: list[str] | None = None
) -> dict:
    where, params = _product_filter(categories, product_types)
    with get_connection() as conn:
        product_count = conn.execute(
            f"SELECT COUNT(*) AS n FROM products pr{where}", params
        ).fetchone()["n"]
        total_stock_ml = conn.execute(
            f"SELECT COALESCE(SUM(pr.stock_ml), 0) AS total FROM products pr{where}", params
        ).fetchone()["total"]
        sales_row = conn.execute(
            f"SELECT COUNT(*) AS n, COALESCE(SUM(s.ml), 0) AS total "
            f"FROM sales s JOIN products pr ON pr.code = s.code{where}", params
        ).fetchone()
        sales_count, total_sold_ml = sales_row["n"], sales_row["total"]
        purchases_row = conn.execute(
            f"SELECT COUNT(*) AS n, COALESCE(SUM(p.ml), 0) AS total "
            f"FROM purchases p JOIN products pr ON pr.code = p.code{where}", params
        ).fetchone()
        purchases_count, total_purchased_ml = purchases_row["n"], purchases_row["total"]
        sold_by_category = conn.execute(
            f"""
            SELECT pr.category, COALESCE(SUM(s.ml), 0) AS ml
            FROM products pr
            LEFT JOIN sales s ON s.code = pr.code
            {where}
            GROUP BY pr.category
            ORDER BY pr.category
            """,
            params,
        ).fetchall()

        return {
            "product_count": product_count,
            "total_stock_ml": total_stock_ml,
            "sales_count": sales_count,
            "total_sold_ml": total_sold_ml,
            "purchases_count": purchases_count,
            "total_purchased_ml": total_purchased_ml,
            "sold_by_category": sold_by_category,
        }


def get_low_stock_products(
    threshold_ml: float = 100.0,
    categories: list[str] | None = None,
    product_types: list[str] | None = None,
) -> list[dict]:
    where, params = _product_filter(categories, product_types)
    clause = f"{where} AND pr.stock_ml <= %s" if where else " WHERE pr.stock_ml <= %s"
    with get_connection() as conn:
        return conn.execute(
            f"SELECT pr.* FROM products pr{clause} ORDER BY pr.stock_ml ASC",
            [*params, threshold_ml],
        ).fetchall()


def update_product(
    code: str,
    category: str,
    house: str,
    original_name: str,
    signature_name: str,
    stock_ml: float,
    product_type: str = "έλαια",
) -> None:
    """Update an existing product, matched by its (unchanged) code."""
    with get_connection() as conn:
        conn.execute(
            """
            UPDATE products
            SET category = %s, house = %s, original_name = %s, signature_name = %s,
                stock_ml = %s, product_type = %s
            WHERE code = %s
            """,
            (category, house or None, original_name or None, signature_name,
             stock_ml, product_type, code),
        )


def delete_product(code: str) -> None:
    """Delete a product and all of its purchase/sale history."""
    with get_connection() as conn:
        conn.execute("DELETE FROM purchases WHERE code = %s", (code,))
        conn.execute("DELETE FROM sales WHERE code = %s", (code,))
        conn.execute("DELETE FROM products WHERE code = %s", (code,))


def _get_row(conn: psycopg.Connection, table: str, row_id: int) -> dict | None:
    return conn.execute(f"SELECT * FROM {table} WHERE id = %s", (row_id,)).fetchone()


def update_purchase(row_id: int, date: str, code: str, ml: float, comments: str) -> None:
    with get_connection() as conn:
        old = _get_row(conn, "purchases", row_id)
        if old is None:
            return
        conn.execute(
            "UPDATE products SET stock_ml = stock_ml - %s WHERE code = %s",
            (old["ml"], old["code"]),
        )
        conn.execute(
            "UPDATE purchases SET date = %s, code = %s, ml = %s, comments = %s WHERE id = %s",
            (date, code, ml, comments or None, row_id),
        )
        conn.execute(
            "UPDATE products SET stock_ml = stock_ml + %s WHERE code = %s", (ml, code)
        )


def delete_purchase(row_id: int) -> None:
    with get_connection() as conn:
        old = _get_row(conn, "purchases", row_id)
        if old is None:
            return
        conn.execute(
            "UPDATE products SET stock_ml = stock_ml - %s WHERE code = %s",
            (old["ml"], old["code"]),
        )
        conn.execute("DELETE FROM purchases WHERE id = %s", (row_id,))


def update_sale(row_id: int, date: str, code: str, ml: float, comments: str) -> None:
    with get_connection() as conn:
        old = _get_row(conn, "sales", row_id)
        if old is None:
            return
        conn.execute(
            "UPDATE products SET stock_ml = stock_ml + %s WHERE code = %s",
            (old["ml"], old["code"]),
        )
        conn.execute(
            "UPDATE sales SET date = %s, code = %s, ml = %s, comments = %s WHERE id = %s",
            (date, code, ml, comments or None, row_id),
        )
        conn.execute(
            "UPDATE products SET stock_ml = stock_ml - %s WHERE code = %s", (ml, code)
        )


def delete_sale(row_id: int) -> None:
    with get_connection() as conn:
        old = _get_row(conn, "sales", row_id)
        if old is None:
            return
        conn.execute(
            "UPDATE products SET stock_ml = stock_ml + %s WHERE code = %s",
            (old["ml"], old["code"]),
        )
        conn.execute("DELETE FROM sales WHERE id = %s", (row_id,))


def get_all_sales() -> list[dict]:
    with get_connection() as conn:
        return conn.execute(
            """
            SELECT s.id, s.date, s.code, pr.signature_name, pr.category, s.ml, s.comments
            FROM sales s
            JOIN products pr ON pr.code = s.code
            ORDER BY s.date DESC, s.id DESC
            """
        ).fetchall()
