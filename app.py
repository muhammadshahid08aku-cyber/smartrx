"""Smart Rx Care - Streamlit app (doctor, pharmacist, admin portals)."""
import os, json
import pandas as pd
import streamlit as st

import db
from safety import drugs, check
from pdf_rx import make_pdf

st.set_page_config(page_title="Smart Rx Care", page_icon="💊", layout="wide")
try:
    db.init()
except Exception as e:
    st.error("Database connection failed. Details below (password hidden):")
    st.code(f"{type(e).__name__}: {getattr(e, 'orig', e)}")
    st.json(db.describe_url())
    st.stop()

st.sidebar.title("💊 Smart Rx Care")
st.sidebar.caption("Demo with synthetic data. AI assists, humans decide.")

if "user" not in st.session_state:
    st.session_state.user = None

if not st.session_state.user:
    st.title("Sign in")
    with st.form("login"):
        u = st.text_input("Username")
        p = st.text_input("Password", type="password")
        if st.form_submit_button("Sign in"):
            user = db.login(u.strip(), p)
            if user:
                st.session_state.user = user
                db.log(user["username"], "login")
                st.rerun()
            else:
                st.error("Invalid credentials or account not approved.")
    st.stop()

user = st.session_state.user
st.sidebar.write(f"**{user['name']}** ({user['role']})")
if st.sidebar.button("Sign out"):
    db.log(user["username"], "logout")
    st.session_state.user = None
    st.rerun()

COLORS = {"red": "🔴", "amber": "🟠", "info": "🔵"}


def show_alerts(alerts):
    if not alerts:
        st.success("No rule-based alerts. The doctor still reviews the prescription.")
    for a in alerts:
        st.markdown(f"{COLORS[a['level']]} **{a['type']}**: {a['msg']}")


FREE_LLAMA_MODEL = "llama-3.1-8b-instant"   # free Llama model on Groq's free tier (fast, high daily limit)


def _secret(name, default=""):
    try:
        return st.secrets[name]
    except Exception:
        return os.getenv(name, default)


def groq_explain(rx_items, lang):
    """Free Llama model via Groq. Explains only; never changes doses or adds medicines."""
    key = _secret("GROQ_API_KEY")
    if not key:
        return "Add GROQ_API_KEY in the app Secrets (free key from console.groq.com) to enable explanations."
    try:
        from groq import Groq
        meds = "; ".join(f"{r['generic_name']} {r['dose_mg']:g} mg, {r['times_per_day']}x/day for {r['duration_days']} days"
                         for r in rx_items)
        prompt = (f"You are a pharmacy assistant. Explain these prescribed medicines to a patient in simple {lang}. "
                  f"For each medicine give: what it is for in general terms, how to take it, food advice, common side effects, "
                  f"and when to call the doctor. Do not change doses, add medicines or diagnose. Keep it short.\n"
                  f"Medicines: {meds}")
        r = Groq(api_key=key).chat.completions.create(
            model=_secret("GROQ_MODEL", FREE_LLAMA_MODEL),
            messages=[{"role": "user", "content": prompt}], temperature=0.2, max_tokens=700)
        return r.choices[0].message.content + "\n\n_AI-generated, for clinical review. Follow your doctor's and pharmacist's instructions._"
    except Exception as e:
        return f"Explanation unavailable right now (free-tier limit or network). Details: {type(e).__name__}"


# ---------------- Doctor ----------------
def doctor_new():
    st.header("New prescription")
    c1, c2, c3, c4 = st.columns(4)
    patient = c1.text_input("Patient name (synthetic)")
    age = c2.number_input("Age", 0, 120, 30)
    weight = c3.number_input("Weight (kg)", 0.0, 250.0, 65.0)
    pregnant = c4.checkbox("Pregnant")
    allergies = st.text_input("Allergies (comma separated, e.g. penicillin, NSAID, sulfa)")
    diagnosis = st.text_input("Diagnosis / notes")

    st.subheader("Medicines")
    if "rows" not in st.session_state:
        st.session_state.rows = pd.DataFrame([{"generic_name": drugs.generic_name.iloc[0], "dose_mg": 500.0,
                                               "times_per_day": 3, "duration_days": 5, "instructions": "After meals"}])
    edited = st.data_editor(
        st.session_state.rows, num_rows="dynamic", use_container_width=True,
        column_config={"generic_name": st.column_config.SelectboxColumn("Medicine", options=list(drugs.generic_name), required=True),
                       "dose_mg": st.column_config.NumberColumn("Dose (mg)", min_value=0.0),
                       "times_per_day": st.column_config.NumberColumn("Times/day", min_value=1, max_value=12, step=1),
                       "duration_days": st.column_config.NumberColumn("Days", min_value=1, max_value=365, step=1),
                       "instructions": st.column_config.TextColumn("Instructions")})
    rows = [r for r in edited.to_dict("records") if r.get("generic_name")]
    alerts = check(rows, allergies, age, weight, pregnant)
    st.subheader("Safety panel")
    show_alerts(alerts)
    reds = [a for a in alerts if a["level"] == "red"]
    ack = True
    if reds:
        ack = st.checkbox("I have reviewed the RED alerts and take clinical responsibility for signing.")
    if st.button("Sign prescription", type="primary", disabled=not (patient and rows and ack)):
        rx_id, sig = db.save_prescription(user["username"], patient, int(age), float(weight), pregnant,
                                          allergies, diagnosis, rows, alerts)
        pdf = make_pdf(rx_id, sig, user["name"], patient, int(age), allergies, diagnosis, rows,
                       pd.Timestamp.utcnow().strftime("%Y-%m-%d %H:%M UTC"))
        st.success(f"Signed. Prescription ID: {rx_id}")
        st.download_button("Download PDF with QR", pdf, file_name=f"{rx_id}.pdf", mime="application/pdf")


def doctor_list():
    st.header("My prescriptions")
    d = db.df("select rx_id, patient_name, diagnosis, status, created_at, dispensed_at from prescriptions "
              "where doctor = :u order by created_at desc", u=user["username"])
    st.dataframe(d, use_container_width=True)


# ---------------- Pharmacist ----------------
def pharm_queue():
    st.header("Verification queue")
    q = db.df("select rx_id, patient_name, patient_age, status, created_at from prescriptions "
              "where status in ('pending','flagged') order by created_at")
    st.dataframe(q, use_container_width=True)
    if q.empty:
        return
    rx = st.selectbox("Open prescription", q.rx_id)
    head = db.df("select * from prescriptions where rx_id=:r", r=rx).iloc[0]
    its = db.df("select generic_name, dose_mg, times_per_day, duration_days, instructions from prescription_items where rx_id=:r", r=rx)
    inv = db.df("select generic_name, qty, expiry from inventory")
    view = its.merge(inv, on="generic_name", how="left")
    view["needed"] = (view.times_per_day * view.duration_days).astype(int)
    view["stock_ok"] = view.qty >= view.needed
    st.write(f"**{head.patient_name}**, age {head.patient_age}. Allergies: {head.allergies or 'none'}. Dx: {head.diagnosis}")
    st.dataframe(view, use_container_width=True)
    show_alerts(json.loads(head.alerts_json or "[]"))
    note = st.text_input("Pharmacist note (required to flag)")
    b1, b2 = st.columns(2)
    if b1.button("Verify and dispense", type="primary", disabled=not view.stock_ok.all()):
        with db.engine().begin() as c:
            for r in view.itertuples():
                c.execute(db.inventory.update().where(db.inventory.c.generic_name == r.generic_name)
                          .values(qty=int(r.qty) - int(r.needed)))
            c.execute(db.prescriptions.update().where(db.prescriptions.c.rx_id == rx)
                      .values(status="dispensed", dispensed_by=user["username"],
                              dispensed_at=pd.Timestamp.utcnow().to_pydatetime(), pharmacist_note=note))
        db.log(user["username"], "dispensed", rx)
        st.success("Dispensed and stock updated."); st.rerun()
    if not view.stock_ok.all():
        st.warning("Insufficient stock for one or more items. Request a substitution from the doctor.")
    if b2.button("Flag / request change", disabled=not note):
        with db.engine().begin() as c:
            c.execute(db.prescriptions.update().where(db.prescriptions.c.rx_id == rx)
                      .values(status="flagged", pharmacist_note=note))
        db.log(user["username"], "flagged", f"{rx}: {note}")
        st.info("Flagged for the doctor."); st.rerun()
    st.divider()
    lang = st.selectbox("Patient explanation language", ["English", "Urdu", "Roman Urdu"])
    if st.button("Generate patient explanation (AI, for review)"):
        st.write(groq_explain(its.to_dict("records"), lang))


def pharm_stock():
    st.header("Stock")
    inv = db.df("select * from inventory order by generic_name")
    inv["low_stock"] = inv.qty <= inv.reorder_level
    st.dataframe(inv, use_container_width=True)
    st.download_button("Export CSV", inv.to_csv(index=False), "stock.csv", "text/csv")


# ---------------- Admin ----------------
def admin_audit():
    st.header("Audit log")
    st.dataframe(db.df("select * from audit_log order by ts desc limit 500"), use_container_width=True)


def admin_users():
    st.header("Create / approve users")
    with st.form("nu"):
        uname = st.text_input("Username"); full = st.text_input("Full name")
        role = st.selectbox("Role", ["doctor", "pharmacist", "admin"])
        pw = st.text_input("Temporary password", type="password")
        if st.form_submit_button("Create (approved)") and uname and len(pw) >= 10:
            import secrets as sc
            salt = sc.token_hex(8)
            with db.engine().begin() as c:
                c.execute(db.users.insert().values(username=uname, pw_hash=db.hash_pw(pw, salt), salt=salt,
                                                   role=role, full_name=full, approved=True))
            db.log(user["username"], "user_created", uname); st.success("Created.")
    st.dataframe(db.df("select username, role, full_name, approved from users"), use_container_width=True)


def admin_formulary():
    st.header("Formulary")
    st.caption("Starter list of common generics with WHO ATC codes. Verify against the DRAP registered-drug list before real use.")
    st.dataframe(drugs, use_container_width=True)


def admin_dash():
    st.header("Analytics")
    d = db.df("select status, count(*) as n from prescriptions group by status")
    st.bar_chart(d.set_index("status"))
    top = db.df("select generic_name, count(*) as n from prescription_items group by generic_name order by n desc limit 10")
    st.subheader("Top medicines"); st.bar_chart(top.set_index("generic_name"))


PAGES = {
    "doctor": {"New prescription": doctor_new, "My prescriptions": doctor_list},
    "pharmacist": {"Verification queue": pharm_queue, "Stock": pharm_stock},
    "admin": {"Analytics": admin_dash, "Users": admin_users, "Formulary": admin_formulary, "Audit log": admin_audit},
}[user["role"]]
choice = st.sidebar.radio("Menu", list(PAGES))
PAGES[choice]()
