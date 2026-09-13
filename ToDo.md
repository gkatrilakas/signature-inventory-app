# ToDo — Hosting & Security

Target setup: **Streamlit Community Cloud** (app) + **Supabase Postgres** (database). Both free tiers, non-commercial use.

## 1. Port the database from SQLite to Postgres

All changes live in `src/signature_app/db.py` (~1–2 hrs, mechanical).

- [x] Add `psycopg[binary]` to `pyproject.toml`, drop nothing (keep `sqlite3` import out).
- [x] `get_connection()` → `psycopg.connect(<SUPABASE_URL>, row_factory=dict_row)` with `sslmode=require`.
- [x] Replace `?` placeholders with `%s` throughout.
- [x] `INTEGER PRIMARY KEY AUTOINCREMENT` → `BIGINT GENERATED ALWAYS AS IDENTITY`.
- [x] Remove `PRAGMA foreign_keys = ON` (Postgres always enforces).
- [x] `init_db()` migration check: replace `PRAGMA table_info(products)` with an `information_schema.columns` query.
- [x] Verify `substr()`, `CURRENT_TIMESTAMP`, `CREATE TABLE IF NOT EXISTS`, and the `with get_connection() as conn` commit-on-exit pattern all still behave.
- [x] Update `import_excel.py` if any SQL/connection assumptions changed. (none needed — it only calls `db` functions)
- [x] Test locally against a Supabase dev project before deploying.

## 2. Deploy to Streamlit Community Cloud

- [ ] Push repo to GitHub.
- [ ] Create the app on share.streamlit.io, entrypoint `src/signature_app/app.py`.
- [ ] Confirm the `src/` layout + `uv_build` backend resolves `from signature_app import db` (may need a small tweak on first deploy).
- [ ] Add **Secrets** in the Community Cloud dashboard:
  - [ ] Supabase Postgres connection string.
  - [ ] `admin_password` — long random value.

## 3. Security — Layer 1: lock down the app (most important)

- [ ] Set app sharing to **"Specific people"** and invite only the needed emails (owner + sales).
- [ ] Do this before sharing the URL anywhere — a public Streamlit app is listed in the gallery and the URL is guessable.

## 4. Security — Layer 2: in-app roles

- [ ] Move the admin password fully into `st.secrets` — remove the `"admin"` env fallback.
- [ ] Compare with `hmac.compare_digest(...)` instead of `==`.
- [ ] (Optional) Switch to `st.login()` (OIDC / Google) and derive admin-vs-sales from the signed-in email against an allowlist in secrets, so "admin" is an identity, not a shared string.

## 5. Security — Layer 3: Supabase hardening

- [ ] Connection string stored only in Community Cloud Secrets, never in the repo.
- [ ] Strong generated DB password; rotate if ever exposed.
- [ ] Keep **Row Level Security enabled** on all tables (default); don't share the anon/service keys.
- [ ] `sslmode=require` in the connection string.
- [ ] (Optional) Create a dedicated least-privilege DB role limited to the app's tables and connect as that instead of `postgres`.

## 6. Ops / data safety

- [ ] Set up a periodic `pg_dump` backup (small GitHub Action or cron) — free Supabase has no point-in-time recovery.
- [ ] Note: free Supabase projects **pause after 7 days of inactivity** — needs a manual restore click, or a scheduled ping to keep it warm.

## Notes / alternatives (superseded — Postgres port is done)

- **Oracle Cloud Always Free VM** — real always-on server + persistent disk, SQLite + a Dockerfile would've worked. More server setup, and moot now.
- **Home machine + Cloudflare Tunnel** — zero code changes, most private, needs an always-on computer.
