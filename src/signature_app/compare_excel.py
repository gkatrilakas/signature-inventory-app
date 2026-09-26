"""Compare two inventory workbooks (and optionally the database) to decide if data needs updating.

Read-only: never writes to the workbooks or the database.
Exit code 0 = everything in sync, 1 = differences found, 2 = error.

    uv run python -m signature_app.compare_excel OLD.xlsx NEW.xlsx [--db]
"""

import argparse
import sys
from collections import Counter
from datetime import date, datetime
from pathlib import Path

import openpyxl

INVENTORY_SHEET = "📦 ΑΠΟΘΗΚΗ"
TRANSACTION_SHEETS = {"ΑΓΟΡΕΣ": "purchases", "🧾 ΠΩΛΗΣΕΙΣ": "sales"}
STATIC_FIELDS = ("category", "house", "original_name", "signature_name")
PRODUCT_FIELDS = (*STATIC_FIELDS, "stock")
STOCK_TOLERANCE = 0.001


def _text(value) -> str:
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return "" if value is None else str(value).strip()


def _number(value) -> float:
    return round(float(value), 3) if value else 0.0


def _iso(value) -> str:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return _text(value)


def read_products(ws) -> dict[str, dict]:
    """Rows of the inventory sheet keyed by code, skipping rows the importer skips."""
    products: dict[str, dict] = {}
    for code, category, house, original_name, signature_name, stock, *_ in ws.iter_rows(
        min_row=3, max_row=ws.max_row, values_only=True
    ):
        if not code or not signature_name:
            continue
        products.setdefault(
            _text(code),
            {
                "category": _text(category) or "UNISEX",
                "house": _text(house),
                "original_name": _text(original_name),
                "signature_name": _text(signature_name),
                "stock": _number(stock),
            },
        )
    return products


def read_transactions(ws) -> Counter:
    """Purchase/sale rows as a multiset — the sheets have no unique row id."""
    rows: Counter = Counter()
    for _, when, code, _signature_name, _category, ml, comments in ws.iter_rows(
        min_row=4, max_row=ws.max_row, values_only=True
    ):
        if not when or not code or ml is None:
            continue
        rows[(_iso(when), _text(code), _number(ml), _text(comments))] += 1
    return rows


def read_db() -> tuple[dict[str, dict], dict[str, Counter]]:
    from signature_app import db

    with db.get_connection() as conn:
        products = {
            r["code"]: {
                "category": _text(r["category"]),
                "house": _text(r["house"]),
                "original_name": _text(r["original_name"]),
                "signature_name": _text(r["signature_name"]),
                "stock": _number(r["stock_ml"]),
            }
            for r in conn.execute(
                "SELECT code, category, house, original_name, signature_name, stock_ml FROM products"
            ).fetchall()
        }
        transactions = {}
        for table in TRANSACTION_SHEETS.values():
            rows: Counter = Counter()
            for r in conn.execute(f"SELECT date, code, ml, comments FROM {table}").fetchall():
                rows[(_iso(r["date"]), _text(r["code"]), _number(r["ml"]), _text(r["comments"]))] += 1
            transactions[table] = rows
    return products, transactions


def diff_products(left: dict, right: dict, fields=PRODUCT_FIELDS) -> tuple[list, list, dict]:
    """(only in right, only in left, changed fields) for two code-keyed product dicts."""
    only_right = sorted(right.keys() - left.keys())
    only_left = sorted(left.keys() - right.keys())
    changed = {}
    for code in sorted(left.keys() & right.keys()):
        diffs = {f: (left[code][f], right[code][f]) for f in fields if left[code][f] != right[code][f]}
        if diffs:
            changed[code] = diffs
    return only_right, only_left, changed


def diff_transactions(left: Counter, right: Counter) -> tuple[list, list]:
    """(only in right, only in left) as sorted row lists."""
    return sorted((right - left).elements()), sorted((left - right).elements())


def expected_stock(products: dict, purchases: Counter, sales: Counter) -> dict[str, float]:
    """Excel baseline + purchases - sales, the same way the importer adjusts stock."""
    stock = {code: p["stock"] for code, p in products.items()}
    for counter, sign in ((purchases, 1), (sales, -1)):
        for (_, code, ml, _), n in counter.items():
            if code in stock:
                stock[code] += sign * ml * n
    return stock


def _print_section(title: str, lines: list[str], limit: int) -> bool:
    print(f"  {title}: {len(lines)}")
    for line in lines[:limit]:
        print(f"    {line}")
    if len(lines) > limit:
        print(f"    … and {len(lines) - limit} more")
    return bool(lines)


def _fmt_row(r: tuple) -> str:
    return f"{r[0]}  {r[1]}  {r[2]:g} ml  {r[3]}".rstrip()


def _fmt_changes(changed: dict) -> list[str]:
    return [
        f"{code}: " + ", ".join(f"{f} {o!r} -> {n!r}" for f, (o, n) in diffs.items())
        for code, diffs in changed.items()
    ]


def _load(path: Path):
    try:
        return openpyxl.load_workbook(path, data_only=True)
    except (FileNotFoundError, openpyxl.utils.exceptions.InvalidFileException) as e:
        raise SystemExit(f"Cannot open {path}: {e}") from e


def _sheet(wb, name: str, path: Path):
    if name not in wb.sheetnames:
        raise SystemExit(f"Sheet {name!r} not found in {path}")
    return wb[name]


def read_workbook(path: Path) -> tuple[dict, dict[str, Counter]]:
    wb = _load(path)
    products = read_products(_sheet(wb, INVENTORY_SHEET, path))
    transactions = {
        table: read_transactions(_sheet(wb, sheet, path))
        for sheet, table in TRANSACTION_SHEETS.items()
    }
    return products, transactions


def compare_files(old, new, limit: int) -> bool:
    print("== OLD file vs NEW file ==")
    changed_any = False
    print(f"Sheet {INVENTORY_SHEET}")
    added, removed, changed = diff_products(old[0], new[0])
    changed_any |= _print_section("added products", added, limit)
    changed_any |= _print_section("removed products", removed, limit)
    changed_any |= _print_section("changed products", _fmt_changes(changed), limit)
    for sheet, table in TRANSACTION_SHEETS.items():
        print(f"Sheet {sheet}")
        added_rows, removed_rows = diff_transactions(old[1][table], new[1][table])
        changed_any |= _print_section("added rows", [_fmt_row(r) for r in added_rows], limit)
        changed_any |= _print_section("removed rows", [_fmt_row(r) for r in removed_rows], limit)
    return changed_any


def compare_db(new, limit: int) -> bool:
    print("== NEW file vs database ==")
    db_products, db_transactions = read_db()
    changed_any = False

    print("Products")
    missing, extra, changed = diff_products(db_products, new[0], fields=STATIC_FIELDS)
    changed_any |= _print_section("in file, missing from DB", missing, limit)
    changed_any |= _print_section("in DB, missing from file", extra, limit)
    changed_any |= _print_section(
        "fields differ (DB -> file)", _fmt_changes(changed), limit
    )

    for sheet, table in TRANSACTION_SHEETS.items():
        print(f"{table.capitalize()} ({sheet})")
        missing_rows, extra_rows = diff_transactions(db_transactions[table], new[1][table])
        changed_any |= _print_section("in file, missing from DB", [_fmt_row(r) for r in missing_rows], limit)
        changed_any |= _print_section("in DB, missing from file", [_fmt_row(r) for r in extra_rows], limit)

    print("Stock (expected = file baseline + purchases - sales)")
    expected = expected_stock(new[0], new[1]["purchases"], new[1]["sales"])
    mismatches = [
        f"{code}: DB {db_products[code]['stock']:g} vs expected {exp:g}"
        for code, exp in sorted(expected.items())
        if code in db_products and abs(db_products[code]["stock"] - exp) > STOCK_TOLERANCE
    ]
    changed_any |= _print_section("stock mismatches", mismatches, limit)
    return changed_any


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("old", type=Path, help="previously imported workbook")
    parser.add_argument("new", type=Path, help="new workbook")
    parser.add_argument("--db", action="store_true", help="also compare the new workbook to the database")
    parser.add_argument("--limit", type=int, default=20, help="max rows to list per section")
    args = parser.parse_args()

    old, new = read_workbook(args.old), read_workbook(args.new)
    files_differ = compare_files(old, new, args.limit)
    db_differs = False
    if args.db:
        print()
        db_differs = compare_db(new, args.limit)

    print()
    if not (files_differ or db_differs):
        print("NO CHANGES")
        return 0
    reasons = [r for r, hit in (("NEW file differs from OLD", files_differ), ("database differs from NEW file", db_differs)) if hit]
    print("UPDATE NEEDED — " + "; ".join(reasons))
    return 1


if __name__ == "__main__":
    sys.exit(main())
