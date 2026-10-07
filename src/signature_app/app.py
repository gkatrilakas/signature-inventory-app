import datetime
import hmac
import os
import time

import pandas as pd
import streamlit as st

from signature_app import db


def role_password(role: str) -> str | None:
    """Password for a role ("admin" / "sales") from st.secrets (`<role>_password`)
    or the SIGNATURE_<ROLE>_PASSWORD env var. No default: an unset role can't log in."""
    try:
        if f"{role}_password" in st.secrets:
            return str(st.secrets[f"{role}_password"]) or None
    except Exception:
        pass
    return os.environ.get(f"SIGNATURE_{role.upper()}_PASSWORD") or None


def check_login(pw: str) -> str | None:
    """Return the role the password unlocks, or None. Admin is checked first."""
    for role in ("admin", "sales"):
        expected = role_password(role)
        if expected and hmac.compare_digest(pw.encode(), expected.encode()):
            return role
    return None


def to_ml(value) -> float:
    """ml amounts are kept to 2 decimal places."""
    return round(float(value or 0), 2)


def ml_column(**kwargs):
    """Number column for ml amounts: accepts and shows 2 decimal places."""
    return st.column_config.NumberColumn(format="%.2f", step=0.01, **kwargs)


def product_options(products: list[dict]) -> dict[str, str]:
    """Map 'CODE — Signature name' labels to product codes, for select boxes."""
    return {f"{p['code']} — {p['signature_name']}": p["code"] for p in products}


@st.cache_resource
def init_db_once() -> None:
    """Create/migrate the schema once per app process, not on every rerun."""
    db.init_db()


st.set_page_config(page_title="Signature", page_icon="🧴", layout="wide")

if not st.session_state.get("role"):
    # Login gate: nothing below (including the DB) runs until a role is set.
    _, center, _ = st.columns([1, 1, 1])
    with center:
        st.title("🧴 Signature")
        st.subheader("Σύνδεση")
        with st.form("login_form"):
            pw = st.text_input("Κωδικός", type="password")
            if st.form_submit_button("Σύνδεση", width="stretch"):
                role = check_login(pw)
                if role:
                    st.session_state.role = role
                    st.rerun()
                time.sleep(1)  # slow down password guessing
                st.error("Λάθος κωδικός.")
    st.stop()

init_db_once()

is_admin = st.session_state.role == "admin"

with st.sidebar:
    st.header("Πρόσβαση")
    if is_admin:
        st.success("Συνδεδεμένος ως **Admin** — επεξεργασία ενεργή")
    else:
        st.info("Συνδεδεμένος ως **Πωλήσεις** — μόνο ανάγνωση")
    if st.button("Αποσύνδεση"):
        st.session_state.clear()
        st.rerun()

st.title("🧴 Signature")
st.caption("Προβολή Admin — πλήρης επεξεργασία" if is_admin else "Προβολή Πωλήσεων — μόνο ανάγνωση")


def editable_table(key, rows, drop_cols, rename, column_config, disabled, apply_changes, locked=()):
    """Render an st.data_editor for admins.

    New rows are inserted as soon as they are complete; edits/deletes are
    applied on Save. `apply_changes("insert", record)` returns None while the
    row is still incomplete, a success message once inserted, and raises
    ValueError for invalid input. `locked` columns can be filled in on new
    rows but not changed on existing ones.
    """
    base = pd.DataFrame([dict(r) for r in rows])
    display = base.drop(columns=[c for c in drop_cols if c in base.columns]).rename(columns=rename)
    inv_rename = {v: k for k, v in rename.items()}

    # Bumping the version gives the editor a fresh key, clearing its pending
    # state once new rows are in the database and part of `rows`.
    ver_key = f"{key}__ver"
    editor_key = f"{key}::{st.session_state.get(ver_key, 0)}"
    for msg in st.session_state.pop(f"{key}__flash", []):
        st.success(msg)

    st.data_editor(
        display,
        key=editor_key,
        use_container_width=True,
        hide_index=True,
        num_rows="dynamic",
        column_config=column_config,
        disabled=disabled,
    )
    state = st.session_state[editor_key]

    def insert_added_rows() -> list[str]:
        inserted = []
        for row in state.get("added_rows", []):
            record = {inv_rename.get(c, c): v for c, v in row.items()}
            try:
                msg = apply_changes("insert", record)
            except ValueError as e:
                st.error(str(e))
                continue
            if msg:
                inserted.append(msg)
        return inserted

    def reset_editor(messages: list[str]) -> None:
        st.session_state[ver_key] = st.session_state.get(ver_key, 0) + 1
        st.session_state[f"{key}__flash"] = messages
        st.rerun()

    has_pending = bool(state.get("edited_rows") or state.get("deleted_rows"))
    if state.get("added_rows"):
        if has_pending:
            # Resetting the editor would discard unsaved edits, so let Save
            # insert the new rows together with them.
            st.info("Πατήστε Αποθήκευση για να καταχωρηθούν οι νέες γραμμές μαζί με τις αλλαγές.")
        else:
            inserted = insert_added_rows()
            if inserted:
                reset_editor(inserted)

    if st.button("💾 Αποθήκευση αλλαγών", key=f"{key}_save"):
        n = 0
        for idx_str, changes in state.get("edited_rows", {}).items():
            original = base.iloc[int(idx_str)].to_dict()
            changed_locked = [c for c in locked if c in changes and changes[c] != original[inv_rename.get(c, c)]]
            if changed_locked:
                st.error(f"Η στήλη '{changed_locked[0]}' δεν αλλάζει σε υπάρχουσες γραμμές.")
                continue
            record = dict(original)
            for col, val in changes.items():
                record[inv_rename.get(col, col)] = val
            apply_changes("update", record)
            n += 1
        for idx in state.get("deleted_rows", []):
            apply_changes("delete", base.iloc[idx].to_dict())
            n += 1
        inserted = insert_added_rows()
        n += len(inserted)
        if n:
            reset_editor([f"{n} αλλαγές αποθηκεύτηκαν."])
        st.success("Καμία αλλαγή.")


# Loaded once per run and shared by every tab; any write is followed by a rerun.
all_products = db.get_all_products()

tab_inventory, tab_purchases, tab_sales, tab_dashboard = st.tabs(
    ["📦 Αποθήκη", "🛒 Αγορές", "🧾 Πωλήσεις", "📊 Dashboard"]
)

PRODUCT_RENAME = {
    "code": "Κωδικός",
    "category": "Κατηγορία",
    "house": "Οίκος",
    "original_name": "Όνομα (πρωτότυπο)",
    "signature_name": "Ονομασία (Signature)",
    "product_type": "Είδος",
    "stock_ml": "Στοκ",
}

with tab_inventory:
    if is_admin:
        st.subheader("Προσθήκη νέου προϊόντος")
        # Not an st.form: category / type changes must rerun to refresh the
        # suggested code and the stock unit.
        col1, col2 = st.columns(2)
        with col1:
            category = st.selectbox("Κατηγορία *", db.CATEGORIES, key="np_category")
            product_type = st.selectbox("Είδος Προϊόντος *", db.PRODUCT_TYPES, key="np_type")
            suggested = db.suggest_code(category, product_type)
            code = st.text_input(
                "Κωδικός *", value=suggested, key=f"np_code::{suggested}",
                help="Προτεινόμενος βάσει κατηγορίας/είδους — αλλάξτε τον αν χρειάζεται.",
            )
            house = st.text_input("Οίκος", key="np_house")
        with col2:
            original_name = st.text_input("Όνομα (πρωτότυπο)", key="np_original")
            signature_name = st.text_input("Ονομασία (Signature) *", key="np_signature")
            stock_ml = st.number_input(
                "Στοκ (ml)", min_value=0.0, step=0.01, format="%.2f", value=0.0, key="np_stock",
            )

        if st.button("Προσθήκη", key="np_submit"):
            code_clean = code.strip()
            signature_name_clean = signature_name.strip()
            if not code_clean or not signature_name_clean:
                st.error("Ο κωδικός και η ονομασία Signature είναι υποχρεωτικά.")
            elif db.code_exists(code_clean):
                st.error(f"Ο κωδικός '{code_clean}' υπάρχει ήδη.")
            else:
                db.add_product(
                    code=code_clean,
                    category=category,
                    house=house.strip(),
                    original_name=original_name.strip(),
                    signature_name=signature_name_clean,
                    stock_ml=to_ml(stock_ml),
                    product_type=product_type,
                )
                for k in ("np_house", "np_original", "np_signature", "np_stock",
                          f"np_code::{suggested}"):
                    st.session_state.pop(k, None)
                # Shown after the rerun, which would otherwise wipe it immediately.
                st.session_state["np_flash"] = (
                    f"Το προϊόν '{signature_name_clean}' προστέθηκε ({code_clean})."
                )
                st.rerun()
        if msg := st.session_state.pop("np_flash", None):
            st.success(msg)
        st.divider()

    st.subheader("Προϊόντα στην αποθήκη")

    icol1, icol2, icol3 = st.columns([2, 1, 1])
    inv_search = icol1.text_input(
        "Αναζήτηση", key="inv_search",
        placeholder="Κωδικός, οίκος ή όνομα…",
    ).strip().casefold()
    inv_categories = icol2.multiselect("Κατηγορία", db.CATEGORIES, key="inv_categories")
    inv_types = icol3.multiselect("Είδος Προϊόντος", db.PRODUCT_TYPES, key="inv_types")

    products = [
        p for p in all_products
        if (not inv_categories or p["category"] in inv_categories)
        and (not inv_types or p["product_type"] in inv_types)
        and (not inv_search or any(
            inv_search in (p[f] or "").casefold()
            for f in ("code", "house", "original_name", "signature_name")
        ))
    ]
    count_label = (
        f"{len(products)} από {len(all_products)} προϊόντα"
        if len(products) != len(all_products) else f"{len(products)} προϊόντα"
    )

    if not all_products:
        st.info("Δεν υπάρχουν ακόμα προϊόντα στη βάση.")
    elif not products:
        st.info("Κανένα προϊόν δεν ταιριάζει με τα φίλτρα.")
    elif is_admin:
        def apply_product(action, record):
            if action == "insert":
                code = str(record.get("code") or "").strip()
                signature_name = str(record.get("signature_name") or "").strip()
                category = record.get("category")
                if not (code and signature_name and category):
                    return None
                if db.code_exists(code):
                    raise ValueError(f"Ο κωδικός '{code}' υπάρχει ήδη.")
                db.add_product(
                    code=code,
                    category=category,
                    house=str(record.get("house") or "").strip(),
                    original_name=str(record.get("original_name") or "").strip(),
                    signature_name=signature_name,
                    stock_ml=to_ml(record.get("stock_ml")),
                    product_type=record.get("product_type") or db.infer_product_type(code),
                )
                return f"Το προϊόν '{signature_name}' προστέθηκε ({code})."
            elif action == "update":
                db.update_product(
                    code=record["code"],
                    category=record["category"],
                    house=record["house"] or "",
                    original_name=record["original_name"] or "",
                    signature_name=record["signature_name"],
                    stock_ml=to_ml(record["stock_ml"]),
                    product_type=record.get("product_type") or "έλαια",
                )
            else:
                db.delete_product(record["code"])

        # Edits are applied by row position, so the editor must reset whenever
        # the filter (and therefore the row order) changes.
        filter_sig = f"{inv_search}|{sorted(inv_categories)}|{sorted(inv_types)}"
        editable_table(
            key=f"products_editor::{filter_sig}",
            rows=products,
            drop_cols=["id", "created_at"],
            rename=PRODUCT_RENAME,
            column_config={
                "Κατηγορία": st.column_config.SelectboxColumn(options=db.CATEGORIES),
                "Είδος": st.column_config.SelectboxColumn(options=db.PRODUCT_TYPES),
                "Στοκ": ml_column(help="ml"),
            },
            disabled=[],
            apply_changes=apply_product,
            locked=["Κωδικός"],
        )
        st.caption(
            f"{count_label} — νέες γραμμές καταχωρούνται μόλις συμπληρωθούν Κωδικός, Κατηγορία "
            "και Ονομασία· για επεξεργασία/διαγραφή πατήστε Αποθήκευση"
        )
    else:
        df = pd.DataFrame([dict(p) for p in products]).drop(columns=["id", "created_at"]).rename(columns=PRODUCT_RENAME)
        st.dataframe(df, use_container_width=True, hide_index=True, column_config={"Στοκ": ml_column()})
        st.caption(count_label if len(products) != len(all_products) else f"{len(products)} προϊόντα συνολικά")


def transaction_tab(kind, label, rows, add_fn, update_fn, delete_fn, ml_label):
    options = product_options(all_products)
    if is_admin:
        st.subheader(f"Καταχώρηση νέας {label}")
        if not options:
            st.info("Προσθέστε πρώτα προϊόντα στην Αποθήκη.")
        else:
            with st.form(f"add_{kind}_form", clear_on_submit=True):
                col1, col2 = st.columns(2)
                with col1:
                    date = st.date_input("Ημερομηνία *", value=datetime.date.today(), key=f"{kind}_date")
                    sel = st.selectbox("Προϊόν *", options.keys(), key=f"{kind}_product")
                with col2:
                    ml = st.number_input(f"{ml_label} *", min_value=0.0, step=0.01, format="%.2f", value=0.0, key=f"{kind}_ml")
                    comments = st.text_input("Σχόλια", key=f"{kind}_comments")
                if st.form_submit_button("Προσθήκη"):
                    ml = to_ml(ml)
                    if ml <= 0:
                        st.error(f"Τα {ml_label} πρέπει να είναι μεγαλύτερα από 0.")
                    else:
                        code = options[sel]
                        add_fn(date=date.isoformat(), code=code, ml=ml, comments=comments.strip())
                        new_stock = db.get_product(code)["stock_ml"]
                        if new_stock < 0:
                            st.warning(f"Καταχωρήθηκε. Προσοχή: αρνητικό στοκ ({new_stock:.2f} ml).")
                        else:
                            st.success(f"Καταχωρήθηκε. Νέο στοκ: {new_stock:.2f} ml.")
        st.divider()

    st.subheader(f"Ιστορικό {label}")
    rename = {
        "date": "Ημερομηνία",
        "code": "Κωδικός",
        "signature_name": "Ονομασία (Signature)",
        "category": "Κατηγορία",
        "ml": ml_label,
        "comments": "Σχόλια",
    }
    if not rows:
        st.info(f"Δεν υπάρχουν ακόμα {label}.")
    elif is_admin:
        # The grid shows "CODE — Signature name" so admins can tell products
        # apart; apply_txn maps the label back to the code.
        code_labels = {code: lbl for lbl, code in options.items()}
        admin_rows = [{**dict(r), "code": code_labels.get(r["code"], r["code"])} for r in rows]

        def apply_txn(action, record):
            if record.get("code"):
                record = {**record, "code": options.get(record["code"], record["code"])}
            if action == "insert":
                date, code, ml = record.get("date"), record.get("code"), record.get("ml")
                if not (date and code and ml):
                    return None
                try:
                    date = datetime.date.fromisoformat(str(date).strip()).isoformat()
                except ValueError:
                    raise ValueError("Η ημερομηνία πρέπει να είναι της μορφής ΕΕΕΕ-ΜΜ-ΗΗ.")
                ml = to_ml(ml)
                if ml <= 0:
                    raise ValueError(f"Τα {ml_label} πρέπει να είναι μεγαλύτερα από 0.")
                add_fn(date=date, code=code, ml=ml,
                       comments=str(record.get("comments") or "").strip())
                new_stock = db.get_product(code)["stock_ml"]
                return f"Καταχωρήθηκε ({code}). Νέο στοκ: {new_stock:.2f} ml."
            elif action == "update":
                update_fn(
                    row_id=int(record["id"]),
                    date=str(record["date"]),
                    code=record["code"],
                    ml=to_ml(record["ml"]),
                    comments=record["comments"] or "",
                )
            else:
                delete_fn(int(record["id"]))

        editable_table(
            key=f"{kind}_editor",
            rows=admin_rows,
            drop_cols=["signature_name", "category"],
            rename={**rename, "code": "Προϊόν"},
            column_config={
                ml_label: ml_column(),
                "Προϊόν": st.column_config.SelectboxColumn(options=list(options.keys()), width="large"),
                "Ημερομηνία": st.column_config.TextColumn(help="ΕΕΕΕ-ΜΜ-ΗΗ"),
            },
            disabled=["id"],
            apply_changes=apply_txn,
            locked=["Προϊόν"],
        )
        st.caption(
            f"{len(rows)} εγγραφές — νέες γραμμές καταχωρούνται μόλις συμπληρωθούν Ημερομηνία, "
            f"Προϊόν και {ml_label}· Προϊόν δεν αλλάζει σε υπάρχουσες· το στοκ ενημερώνεται αυτόματα"
        )
    else:
        df = pd.DataFrame([dict(r) for r in rows]).drop(columns=["id"]).rename(columns=rename)
        st.dataframe(df, use_container_width=True, hide_index=True, column_config={ml_label: ml_column()})
        st.caption(f"{len(rows)} συνολικά")


with tab_purchases:
    transaction_tab(
        "purchase", "αγοράς", db.get_all_purchases(),
        db.add_purchase, db.update_purchase, db.delete_purchase, "ML αγοράς",
    )

with tab_sales:
    transaction_tab(
        "sale", "πώλησης", db.get_all_sales(),
        db.add_sale, db.update_sale, db.delete_sale, "ML πώλησης",
    )

with tab_dashboard:
    fcol1, fcol2 = st.columns(2)
    sel_categories = fcol1.multiselect("Κατηγορία", db.CATEGORIES, key="dash_categories")
    sel_types = fcol2.multiselect("Είδος Προϊόντος", db.PRODUCT_TYPES, key="dash_types")
    categories = sel_categories or None
    product_types = sel_types or None
    if categories or product_types:
        st.caption("Τα στοιχεία είναι φιλτραρισμένα.")

    stats = db.get_dashboard_stats(categories, product_types)

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Προϊόντα", stats["product_count"])
    col2.metric("Τρέχον στοκ", f"{stats['total_stock_ml']:.2f} ml")
    col3.metric("Πωλήσεις", stats["sales_count"], f"{stats['total_sold_ml']:.2f} ml")
    col4.metric("Αγορές", stats["purchases_count"], f"{stats['total_purchased_ml']:.2f} ml")

    st.divider()
    st.subheader("ML πωλήσεων ανά κατηγορία")
    sold_by_category = stats["sold_by_category"]
    if sold_by_category:
        df_cat = pd.DataFrame([dict(r) for r in sold_by_category]).set_index("category").round(2)
        st.bar_chart(df_cat)
    else:
        st.info("Δεν υπάρχουν ακόμα δεδομένα πωλήσεων.")

    st.divider()
    st.subheader("Χαμηλό στοκ")

    threshold = st.number_input(
        "Όριο ειδοποίησης (ml)", min_value=0.0, value=100.0, step=0.01, format="%.2f", key="low_stock_ml",
    )
    low_stock = db.get_low_stock_products(
        threshold=threshold, categories=categories, product_types=product_types,
    )
    if low_stock:
        df_low = pd.DataFrame([dict(p) for p in low_stock]).drop(columns=["id", "created_at"]).rename(columns=PRODUCT_RENAME)
        st.dataframe(df_low, use_container_width=True, hide_index=True, column_config={"Στοκ": ml_column()})
        st.caption(f"{len(low_stock)} προϊόντα κάτω από {threshold:.2f} ml")
    else:
        st.success(f"Κανένα προϊόν κάτω από {threshold:.2f} ml.")
