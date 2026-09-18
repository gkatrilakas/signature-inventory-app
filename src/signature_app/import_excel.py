"""One-off script: seed signature.db from the existing ΠΥΘΑΓΟΡΑΣ ΣΤΑΥΡΟΣ.xlsx inventory sheet."""

from pathlib import Path

import openpyxl

from signature_app import db

XLSX_PATH = Path(__file__).resolve().parent.parent.parent / "ΠΥΘΑΓΟΡΑΣ ΣΤΑΥΡΟΣ.xlsx"


def main() -> None:
    db.init_db()

    wb = openpyxl.load_workbook(XLSX_PATH, data_only=True)
    ws = wb["📦 ΑΠΟΘΗΚΗ"]

    # A single shared connection for the whole import — opening a fresh
    # connection per row (as db.add_product et al. do) hits Supabase's
    # pooler connection-rate limit and gets dropped mid-import.
    with db.get_connection() as conn:
        known_codes = {
            r["code"] for r in conn.execute("SELECT code FROM products").fetchall()
        }

        imported = 0
        skipped = 0
        for code, category, house, original_name, signature_name, stock_ml, *_ in ws.iter_rows(
            min_row=3, max_row=ws.max_row, values_only=True
        ):
            if not code or not signature_name:
                skipped += 1
                continue
            if code in known_codes:
                skipped += 1
                continue
            conn.execute(
                """
                INSERT INTO products
                    (code, category, house, original_name, signature_name, stock_ml, product_type)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    code,
                    category or "UNISEX",
                    house or None,
                    original_name or None,
                    signature_name,
                    float(stock_ml) if stock_ml else 0.0,
                    db.infer_product_type(code),
                ),
            )
            known_codes.add(code)
            imported += 1

        print(f"Imported {imported} products, skipped {skipped} incomplete/duplicate rows.")

        import_transactions(conn, known_codes, wb, "ΑΓΟΡΕΣ", "purchases")
        import_transactions(conn, known_codes, wb, "🧾 ΠΩΛΗΣΕΙΣ", "sales")


def import_transactions(conn, known_codes, wb, sheet_name: str, label: str) -> None:
    """Import historical rows from a ΑΓΟΡΕΣ/ΠΩΛΗΣΕΙΣ-shaped sheet.

    ΑΡΧΙΚΟ STOCK in the inventory sheet is a starting baseline, not a live
    figure, so historical rows must adjust stock the same as new entries do.
    """
    ws = wb[sheet_name]
    table = "purchases" if label == "purchases" else "sales"
    sign = 1 if label == "purchases" else -1

    imported = 0
    skipped = 0
    for _, date, code, _signature_name, _category, ml, comments in ws.iter_rows(
        min_row=4, max_row=ws.max_row, values_only=True
    ):
        if not date or not code or ml is None:
            continue
        if code not in known_codes:
            skipped += 1
            continue
        ml = float(ml)
        conn.execute(
            f"INSERT INTO {table} (date, code, ml, comments) VALUES (%s, %s, %s, %s)",
            (date.date().isoformat(), code, ml, comments or None),
        )
        conn.execute(
            "UPDATE products SET stock_ml = stock_ml + %s WHERE code = %s",
            (sign * ml, code),
        )
        imported += 1

    print(f"Imported {imported} {label}, skipped {skipped} rows with unknown codes.")


if __name__ == "__main__":
    main()
