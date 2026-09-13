import datetime
import os

import pandas as pd
import streamlit as st

from signature_app import db


def admin_password() -> str:
    """Admin password from st.secrets or the SIGNATURE_ADMIN_PASSWORD env var."""
    try:
        if "admin_password" in st.secrets:
            return str(st.secrets["admin_password"])
    except Exception:
        pass
    return os.environ.get("SIGNATURE_ADMIN_PASSWORD", "admin")


def product_options() -> dict[str, str]:
    """Map 'CODE — Signature name' labels to product codes, for select boxes."""
    return {f"{p['code']} — {p['signature_name']}": p["code"] for p in db.get_all_products()}


st.set_page_config(page_title="Signature", page_icon="🧴", layout="wide")

db.init_db()

with st.sidebar:
    st.header("Πρόσβαση")
    if st.session_state.get("is_admin"):
        st.success("Συνδεδεμένος ως **Admin** — επεξεργασία ενεργή")
        if st.button("Αποσύνδεση"):
            st.session_state.is_admin = False
            st.rerun()
    else:
        st.info("Προβολή **Πωλήσεων** (μόνο ανάγνωση)")
        with st.form("login_form"):
            pw = st.text_input("Κωδικός admin", type="password")
            if st.form_submit_button("Σύνδεση ως Admin"):
                if pw == admin_password():
                    st.session_state.is_admin = True
                    st.rerun()
                else:
                    st.error("Λάθος κωδικός.")

is_admin = bool(st.session_state.get("is_admin"))

st.title("🧴 Signature")
st.caption("Προβολή Admin — πλήρης επεξεργασία" if is_admin else "Προβολή Πωλήσεων — μόνο ανάγνωση")


def editable_table(key, rows, drop_cols, rename, column_config, disabled, apply_changes):
    """Render an st.data_editor for admins; apply edits/deletes on Save."""
    base = pd.DataFrame([dict(r) for r in rows])
    display = base.drop(columns=[c for c in drop_cols if c in base.columns]).rename(columns=rename)

    st.data_editor(
        display,
        key=key,
        use_container_width=True,
        hide_index=True,
        num_rows="dynamic",
        column_config=column_config,
        disabled=disabled,
    )

    if st.button("💾 Αποθήκευση αλλαγών", key=f"{key}_save"):
        state = st.session_state[key]
        inv_rename = {v: k for k, v in rename.items()}
        n = 0
        for idx_str, changes in state.get("edited_rows", {}).items():
            record = base.iloc[int(idx_str)].to_dict()
            for col, val in changes.items():
                record[inv_rename.get(col, col)] = val
            apply_changes("update", record)
            n += 1
        for idx in state.get("deleted_rows", []):
            apply_changes("delete", base.iloc[idx].to_dict())
            n += 1
        if state.get("added_rows"):
            st.warning("Νέες γραμμές αγνοήθηκαν — χρησιμοποιήστε τη φόρμα προσθήκης.")
        st.success(f"{n} αλλαγές αποθηκεύτηκαν." if n else "Καμία αλλαγή.")
        if n:
            st.rerun()


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
            unit = db.unit_for(category)
            stock_ml = st.number_input(
                f"Στοκ ({unit})", min_value=0.0,
                step=10.0 if unit == "ml" else 1.0, value=0.0, key="np_stock",
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
                    stock_ml=stock_ml,
                    product_type=product_type,
                )
                for k in ("np_house", "np_original", "np_signature", "np_stock",
                          f"np_code::{suggested}"):
                    st.session_state.pop(k, None)
                st.success(f"Το προϊόν '{signature_name_clean}' προστέθηκε ({code_clean}).")
                st.rerun()
        st.divider()

    st.subheader("Προϊόντα στην αποθήκη")
    products = db.get_all_products()
    if not products:
        st.info("Δεν υπάρχουν ακόμα προϊόντα στη βάση.")
    elif is_admin:
        def apply_product(action, record):
            if action == "update":
                db.update_product(
                    code=record["code"],
                    category=record["category"],
                    house=record["house"] or "",
                    original_name=record["original_name"] or "",
                    signature_name=record["signature_name"],
                    stock_ml=float(record["stock_ml"] or 0),
                    product_type=record.get("product_type") or "έλαια",
                )
            else:
                db.delete_product(record["code"])

        editable_table(
            key="products_editor",
            rows=products,
            drop_cols=["id", "created_at"],
            rename=PRODUCT_RENAME,
            column_config={
                "Κατηγορία": st.column_config.SelectboxColumn(options=db.CATEGORIES),
                "Είδος": st.column_config.SelectboxColumn(options=db.PRODUCT_TYPES),
                "Στοκ": st.column_config.NumberColumn(help="τεμάχια για ΑΡΩΜΑΤΙΚΑ ΧΩΡΟΥ / ΑΥΤ, ml για τις υπόλοιπες κατηγορίες"),
            },
            disabled=["Κωδικός"],
            apply_changes=apply_product,
        )
        st.caption(f"{len(products)} προϊόντα — επεξεργαστείτε κελιά ή διαγράψτε γραμμές, μετά Αποθήκευση")
    else:
        df = pd.DataFrame([dict(p) for p in products]).drop(columns=["id", "created_at"]).rename(columns=PRODUCT_RENAME)
        st.dataframe(df, use_container_width=True, hide_index=True)
        st.caption(f"{len(products)} προϊόντα συνολικά")


def transaction_tab(kind, label, rows, add_fn, update_fn, delete_fn, ml_label):
    options = product_options()
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
                    ml = st.number_input(f"{ml_label} *", min_value=0.0, step=10.0, value=0.0, key=f"{kind}_ml")
                    comments = st.text_input("Σχόλια", key=f"{kind}_comments")
                if st.form_submit_button("Προσθήκη"):
                    if ml <= 0:
                        st.error(f"Τα {ml_label} πρέπει να είναι μεγαλύτερα από 0.")
                    else:
                        code = options[sel]
                        add_fn(date=date.isoformat(), code=code, ml=ml, comments=comments.strip())
                        new_stock = db.get_product(code)["stock_ml"]
                        if new_stock < 0:
                            st.warning(f"Καταχωρήθηκε. Προσοχή: αρνητικό στοκ ({new_stock:g} ml).")
                        else:
                            st.success(f"Καταχωρήθηκε. Νέο στοκ: {new_stock:g} ml.")
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
        def apply_txn(action, record):
            if action == "update":
                update_fn(
                    row_id=int(record["id"]),
                    date=str(record["date"]),
                    code=record["code"],
                    ml=float(record["ml"] or 0),
                    comments=record["comments"] or "",
                )
            else:
                delete_fn(int(record["id"]))

        editable_table(
            key=f"{kind}_editor",
            rows=rows,
            drop_cols=["signature_name", "category"],
            rename=rename,
            column_config={ml_label: st.column_config.NumberColumn(step=10.0)},
            disabled=["Κωδικός"],
            apply_changes=apply_txn,
        )
        st.caption(f"{len(rows)} εγγραφές — Κωδικός δεν αλλάζει· το στοκ ενημερώνεται αυτόματα")
    else:
        df = pd.DataFrame([dict(r) for r in rows]).drop(columns=["id"]).rename(columns=rename)
        st.dataframe(df, use_container_width=True, hide_index=True)
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
    col2.metric("Τρέχον στοκ", f"{stats['total_stock_ml']:g} ml")
    col3.metric("Πωλήσεις", stats["sales_count"], f"{stats['total_sold_ml']:g} ml")
    col4.metric("Αγορές", stats["purchases_count"], f"{stats['total_purchased_ml']:g} ml")

    st.divider()
    st.subheader("ML πωλήσεων ανά κατηγορία")
    sold_by_category = stats["sold_by_category"]
    if sold_by_category:
        df_cat = pd.DataFrame([dict(r) for r in sold_by_category]).set_index("category")
        st.bar_chart(df_cat)
    else:
        st.info("Δεν υπάρχουν ακόμα δεδομένα πωλήσεων.")

    st.divider()
    st.subheader("Χαμηλό στοκ")
    threshold = st.slider("Όριο ειδοποίησης (ml)", min_value=0, max_value=500, value=100, step=10)
    low_stock = db.get_low_stock_products(
        threshold_ml=threshold, categories=categories, product_types=product_types
    )
    if low_stock:
        df_low = pd.DataFrame([dict(p) for p in low_stock]).drop(columns=["id", "created_at"]).rename(columns=PRODUCT_RENAME)
        st.dataframe(df_low, use_container_width=True, hide_index=True)
        st.caption(f"{len(low_stock)} προϊόντα κάτω από {threshold} ml")
    else:
        st.success(f"Κανένα προϊόν κάτω από {threshold} ml.")
