# Signature

Inventory, purchases, and sales tracking for Signature perfumes, built with
Streamlit and a Supabase Postgres database.

## Setup

```bash
uv sync
```

Copy `.streamlit/secrets.toml.example` to `.streamlit/secrets.toml` and fill in
your Supabase connection string (`postgres_url`) and `admin_password`.

## Import existing inventory (one-time)

Seeds `signature.db` from `ΠΥΘΑΓΟΡΑΣ ΣΤΑΥΡΟΣ.xlsx` (the 📦 ΑΠΟΘΗΚΗ sheet).
Safe to re-run — existing codes are skipped.

```bash
uv run python3 -m signature_app.import_excel
```

## Run the app

```bash
uv run streamlit run src/signature_app/app.py
```

Opens at [http://localhost:8501](http://localhost:8501).

## Roles

The app opens in a read-only **Sales** view. Enter the admin password in the
sidebar to unlock editing. Set the password via the `SIGNATURE_ADMIN_PASSWORD`
env var or `admin_password` in `.streamlit/secrets.toml` (defaults to `admin`).

- **Sales view** — every tab is read-only.
- **Admin view** — add rows via the forms, and edit cells or delete rows
  directly in each table's grid, then click **Αποθήκευση αλλαγών**. Editing or
  deleting a purchase/sale recomputes the product's stock automatically.

## Tabs

- **📦 Αποθήκη** — products and current inventory. Each product has an **Είδος**
  (έλαια → stock in ml, άρωμα → stock in τεμάχια). When adding a product the
  **Κωδικός** is pre-filled with the next free code for the chosen
  category/είδος (e.g. `WP-057`).
- **🛒 Αγορές** — purchase history.
- **🧾 Πωλήσεις** — sales history.
- **📊 Dashboard** — totals, sales by category, low-stock alerts, filterable by
  **Κατηγορία** and **Είδος Προϊόντος**.

## Project layout

- `src/signature_app/db.py` — Postgres schema and data access
- `src/signature_app/import_excel.py` — one-off Excel → DB seed script
- `src/signature_app/app.py` — Streamlit app
