"""Smart Rx Care - Streamlit app (doctor, pharmacist, patient, admin portals)."""
import os, json
import pandas as pd
import streamlit as st

import db
from safety import drugs, check
from pdf_rx import make_pdf, qr_png

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
    with st.expander("New patient? Create an account"):
        st.caption("Patient accounts only. Doctors and pharmacists are created by an admin. Use synthetic data in this demo.")
        with st.form("register"):
            r_user = st.text_input("Choose a username (letters, numbers, underscore)")
            r_name = st.text_input("Full name (synthetic)")
            r_pw = st.text_input("Password (at least 10 characters)", type="password")
            if st.form_submit_button("Create patient account"):
                import re
                if not re.fullmatch(r"[A-Za-z0-9_]{3,30}", r_user or ""):
                    st.error("Username must be 3 to 30 letters, numbers or underscores.")
                elif len(r_pw) < 10 or not r_name.strip():
                    st.error("Enter your name and a password of at least 10 characters.")
                else:
                    ok, msg = db.create_user(r_user, r_pw, "patient", r_name.strip())
                    if ok:
                        db.log(r_user, "patient_registered")
                        st.success("Account created. Sign in above.")
                    else:
                        st.error(msg)
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
    pats = db.df("select username, full_name from users where role = 'patient' order by full_name")
    walkin = "(walk-in patient: type details)"
    options = [walkin] + [f"{r.full_name} ({r.username})" for r in pats.itertuples()]
    sel = st.selectbox("Patient", options, help="Registered patients link the prescription to their portal.")
    pat_user, prof, pat_name = None, {}, ""
    if sel != walkin:
        pat_user = sel.rsplit("(", 1)[1].rstrip(")")
        pat_name = sel.rsplit(" (", 1)[0]
        prof = db.get_profile(pat_user)
        if not prof.get("consent"):
            st.warning("This patient has not recorded consent in their profile yet.")
        if prof.get("conditions") or prof.get("current_meds"):
            st.info(f"Conditions: {prof.get('conditions') or 'none recorded'}. Current medicines: {prof.get('current_meds') or 'none recorded'}.")
    k = pat_user or "walkin"
    c1, c2, c3, c4 = st.columns(4)
    patient = c1.text_input("Patient name (synthetic)", value=pat_name, key=f"pn_{k}")
    age = c2.number_input("Age", 0, 120, int(prof.get("age") or 30), key=f"age_{k}")
    weight = c3.number_input("Weight (kg)", 0.0, 250.0, float(prof.get("weight") or 65.0), key=f"w_{k}")
    pregnant = c4.checkbox("Pregnant", value=bool(prof.get("pregnant")), key=f"preg_{k}")
    allergies = st.text_input("Allergies (comma separated, e.g. penicillin, NSAID, sulfa)",
                              value=prof.get("allergies") or "", key=f"all_{k}")
    diagnosis = st.text_input("Diagnosis / notes", key=f"dx_{k}")

    st.subheader("Medicines")
    if "rows" not in st.session_state:
        st.session_state.rows = pd.DataFrame([{"generic_name": drugs.generic_name.iloc[0], "dose_mg": 500.0,
                                               "times_per_day": 3, "duration_days": 5, "instructions": "After meals"}])
    edited = st.data_editor(
        st.session_state.rows, num_rows="dynamic", width="stretch",
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
                                          allergies, diagnosis, rows, alerts, patient_username=pat_user)
        pdf = make_pdf(rx_id, sig, user["name"], patient, int(age), allergies, diagnosis, rows,
                       pd.Timestamp.utcnow().strftime("%Y-%m-%d %H:%M UTC"))
        st.success(f"Signed. Prescription ID: {rx_id}")
        st.download_button("Download PDF with QR", pdf, file_name=f"{rx_id}.pdf", mime="application/pdf")


def doctor_list():
    st.header("My prescriptions")
    d = db.df("select rx_id, patient_name, diagnosis, status, created_at, dispensed_at from prescriptions "
              "where doctor = :u order by created_at desc", u=user["username"])
    st.dataframe(d, width="stretch")


# ---------------- Pharmacist ----------------
def pharm_queue():
    st.header("Verification queue")
    q = db.df("select rx_id, patient_name, patient_age, status, created_at from prescriptions "
              "where status in ('pending','flagged') order by created_at")
    st.dataframe(q, width="stretch")
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
    st.dataframe(view, width="stretch")
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
    st.dataframe(inv, width="stretch")
    st.download_button("Export CSV", inv.to_csv(index=False), "stock.csv", "text/csv")


# ---------------- Admin ----------------
def admin_audit():
    st.header("Audit log")
    st.dataframe(db.df("select * from audit_log order by ts desc limit 500"), width="stretch")


def admin_users():
    st.header("Create / approve users")
    with st.form("nu"):
        uname = st.text_input("Username"); full = st.text_input("Full name")
        role = st.selectbox("Role", ["doctor", "pharmacist", "patient", "admin"])
        pw = st.text_input("Temporary password", type="password")
        if st.form_submit_button("Create (approved)") and uname and len(pw) >= 10:
            ok, msg = db.create_user(uname.strip(), pw, role, full)
            if ok:
                db.log(user["username"], "user_created", f"{uname} ({role})"); st.success("Created.")
            else:
                st.error(msg)
    st.dataframe(db.df("select username, role, full_name, approved from users"), width="stretch")


def admin_formulary():
    st.header("Formulary")
    st.caption("Starter list of common generics with WHO ATC codes. Verify against the DRAP registered-drug list before real use.")
    st.dataframe(drugs, width="stretch")


def admin_dash():
    st.header("Analytics")
    d = db.df("select status, count(*) as n from prescriptions group by status")
    st.bar_chart(d.set_index("status"))
    top = db.df("select generic_name, count(*) as n from prescription_items group by generic_name order by n desc limit 10")
    st.subheader("Top medicines"); st.bar_chart(top.set_index("generic_name"))


def doctor_feedback():
    st.header("Patient feedback")
    me = user["username"]

    st.subheader("Side-effect reports")
    se = db.df("select s.id, s.ts, s.patient_username, s.rx_id, s.symptom, s.severity, s.status, s.doctor_note "
               "from side_effect_reports s join prescriptions p on p.rx_id = s.rx_id "
               "where p.doctor = :u order by s.ts desc", u=me)
    if se.empty:
        st.caption("No reports yet.")
    else:
        st.dataframe(se, width="stretch")
        new = se[se.status == "new"]
        if not new.empty:
            sid = st.selectbox("Review report ID", new.id.tolist())
            note = st.text_input("Your note to the patient", key="se_note")
            if st.button("Mark reviewed"):
                with db.engine().begin() as c:
                    c.execute(db.side_effects.update().where(db.side_effects.c.id == int(sid))
                              .values(status="reviewed", doctor_note=note))
                db.log(me, "side_effect_reviewed", str(sid)); st.rerun()

    st.subheader("Refill requests")
    rf = db.df("select r.id, r.ts, r.patient_username, r.rx_id, r.note, r.status, r.doctor_note "
               "from refill_requests r join prescriptions p on p.rx_id = r.rx_id "
               "where p.doctor = :u order by r.ts desc", u=me)
    if rf.empty:
        st.caption("No refill requests yet.")
    else:
        st.dataframe(rf, width="stretch")
        pend = rf[rf.status == "requested"]
        if not pend.empty:
            rid = st.selectbox("Decide on request ID", pend.id.tolist())
            rnote = st.text_input("Note to the patient", key="rf_note")
            a, b = st.columns(2)
            for label, status, col in (("Approve (then issue a new prescription)", "approved", a), ("Decline", "declined", b)):
                if col.button(label):
                    with db.engine().begin() as c:
                        c.execute(db.refills.update().where(db.refills.c.id == int(rid))
                                  .values(status=status, doctor_note=rnote))
                    db.log(me, f"refill_{status}", str(rid)); st.rerun()

    st.subheader("Adherence (doses logged by patients)")
    its = db.df("select p.rx_id, p.patient_username, p.dispensed_at, i.generic_name, i.times_per_day, i.duration_days "
                "from prescriptions p join prescription_items i on i.rx_id = p.rx_id "
                "where p.doctor = :u and p.status = 'dispensed' and p.patient_username is not null", u=me)
    if its.empty:
        st.caption("Appears once a patient-linked prescription is dispensed.")
        return
    logs = db.df("select rx_id, generic_name, count(*) as taken from dose_log group by rx_id, generic_name")
    its["start"] = (pd.to_datetime(its.dispensed_at) + pd.Timedelta(hours=5)).dt.date
    its["days"] = its.start.apply(lambda s: max(1, (db.today_pkt() - s).days + 1))
    its["days"] = its[["days", "duration_days"]].min(axis=1)
    its["expected"] = its.times_per_day * its.days
    its = its.merge(logs, on=["rx_id", "generic_name"], how="left").fillna({"taken": 0})
    g = its.groupby(["patient_username", "rx_id"], as_index=False)[["expected", "taken"]].sum()
    g["adherence_%"] = (100 * g.taken / g.expected).round(0)
    st.dataframe(g.sort_values("adherence_%"), width="stretch")
    st.caption("Low percentages may need a follow-up call. Doses are self-reported by patients.")


# ---------------- Patient ----------------
STATUS_TEXT = {"pending": "Waiting for the pharmacist", "dispensed": "Dispensed",
               "flagged": "Pharmacist asked the doctor for a change"}


def _my_rx():
    return db.df("select p.rx_id, p.created_at, p.status, p.diagnosis, p.allergies, p.signed_hash, p.pharmacist_note, "
                 "p.patient_name, p.patient_age, p.dispensed_at, u.full_name as doctor_name "
                 "from prescriptions p left join users u on u.username = p.doctor "
                 "where p.patient_username = :u order by p.created_at desc", u=user["username"])


def _rx_items(rx_id):
    return db.df("select generic_name, dose_mg, times_per_day, duration_days, instructions "
                 "from prescription_items where rx_id = :r", r=rx_id)


def patient_rx():
    st.header("My prescriptions")
    d = _my_rx()
    if d.empty:
        st.info("No prescriptions linked to your account yet. Ask your doctor to select your name when prescribing.")
        return
    show = d[["rx_id", "created_at", "doctor_name", "diagnosis", "status"]].copy()
    show["status"] = show.status.map(STATUS_TEXT).fillna(show.status)
    st.dataframe(show, width="stretch")
    rx = st.selectbox("Open prescription", d.rx_id)
    row = d[d.rx_id == rx].iloc[0]
    its = _rx_items(rx)
    st.markdown(f"**Status:** {STATUS_TEXT.get(row.status, row.status)}")
    if row.status == "flagged" and row.pharmacist_note:
        st.warning(f"Pharmacist note: {row.pharmacist_note}")
    st.dataframe(its.rename(columns={"generic_name": "Medicine", "dose_mg": "Dose (mg)", "times_per_day": "Times/day",
                                     "duration_days": "Days", "instructions": "Instructions"}), width="stretch")
    created = str(row.created_at)[:16] + " UTC"
    pdf = make_pdf(rx, row.signed_hash, row.doctor_name or "Doctor", row.patient_name, int(row.patient_age or 0),
                   row.allergies or "", row.diagnosis or "", its.to_dict("records"), created)
    c1, c2 = st.columns([1, 2])
    c1.image(qr_png(rx, row.signed_hash), caption="Show this QR at the pharmacy", width=180)
    c2.download_button("Download prescription PDF", pdf, file_name=f"{rx}.pdf", mime="application/pdf")
    c2.caption("The pharmacist verifies every prescription before dispensing.")
    st.divider()
    lang = st.selectbox("Explain my medicines in", ["English", "Urdu", "Roman Urdu"])
    if st.button("Explain my medicines (AI)"):
        st.write(groq_explain(its.to_dict("records"), lang))


def _slots(n):
    n = int(n)
    fixed = {1: ["08:00"], 2: ["08:00", "20:00"], 3: ["08:00", "14:00", "20:00"], 4: ["08:00", "12:00", "16:00", "20:00"]}
    if n in fixed:
        return fixed[n]
    if n < 1:
        return []
    mins = [int(420 + i * 900 / (n - 1)) for i in range(n)]
    return [f"{m // 60:02d}:{m % 60:02d}" for m in mins]


def patient_schedule():
    st.header("Today's medicine schedule")
    me = user["username"]
    d = db.df("select p.rx_id, p.dispensed_at, i.generic_name, i.dose_mg, i.times_per_day, i.duration_days, i.instructions "
              "from prescriptions p join prescription_items i on i.rx_id = p.rx_id "
              "where p.patient_username = :u and p.status = 'dispensed'", u=me)
    if d.empty:
        st.info("Your schedule appears here once the pharmacist dispenses a prescription.")
        return
    today = db.today_pkt()
    d["start"] = (pd.to_datetime(d.dispensed_at) + pd.Timedelta(hours=5)).dt.date
    d["day_no"] = d.start.apply(lambda s: (today - s).days + 1)
    d = d[(d.day_no >= 1) & (d.day_no <= d.duration_days)]
    if d.empty:
        st.success("No active courses today. All your medicines are complete.")
        return
    logged = db.df("select rx_id, generic_name, slot from dose_log where patient_username = :u and day = :d",
                   u=me, d=today.isoformat())
    done = {(r.rx_id, r.generic_name, r.slot) for r in logged.itertuples()}
    st.caption(f"{today.strftime('%A %d %B %Y')}. Tick each dose after you take it. Follow your doctor's instructions.")
    picks = []
    for r in d.sort_values("generic_name").itertuples():
        st.markdown(f"**{r.generic_name}** {r.dose_mg:g} mg, day {r.day_no} of {r.duration_days}"
                    + (f" ({r.instructions})" if r.instructions else ""))
        cols = st.columns(max(1, int(r.times_per_day)))
        for col, slot in zip(cols, _slots(r.times_per_day)):
            if col.checkbox(slot, value=(r.rx_id, r.generic_name, slot) in done, key=f"d_{r.rx_id}_{r.generic_name}_{slot}"):
                picks.append((r.rx_id, r.generic_name, slot))
    if st.button("Save today's doses", type="primary"):
        with db.engine().begin() as c:
            c.execute(db.dose_log.delete().where((db.dose_log.c.patient_username == me) & (db.dose_log.c.day == today.isoformat())))
            for rx_id, g, slot in picks:
                c.execute(db.dose_log.insert().values(patient_username=me, rx_id=rx_id, generic_name=g, slot=slot,
                                                      day=today.isoformat(), ts=pd.Timestamp.utcnow().to_pydatetime()))
        db.log(me, "doses_logged", f"{len(picks)} doses")
        st.success(f"Saved {len(picks)} dose(s). Your doctor can see your adherence.")
    st.caption("Email and WhatsApp reminders are planned for a later phase.")


def patient_refill():
    st.header("Request a refill")
    me = user["username"]
    d = _my_rx()
    d = d[d.status == "dispensed"] if not d.empty else d
    if d.empty:
        st.info("You can request a refill after a prescription has been dispensed.")
    else:
        with st.form("refill"):
            rx = st.selectbox("Prescription", d.rx_id)
            note = st.text_area("Note to the doctor (optional)")
            if st.form_submit_button("Send refill request"):
                with db.engine().begin() as c:
                    c.execute(db.refills.insert().values(ts=pd.Timestamp.utcnow().to_pydatetime(), patient_username=me,
                                                         rx_id=rx, note=note, status="requested"))
                db.log(me, "refill_requested", rx); st.success("Sent to your doctor.")
    mine = db.df("select ts, rx_id, status, doctor_note from refill_requests where patient_username = :u order by ts desc", u=me)
    if not mine.empty:
        st.subheader("My requests"); st.dataframe(mine, width="stretch")


def patient_report():
    st.header("Report a side effect")
    me = user["username"]
    st.warning("In an emergency (trouble breathing, swelling of the face, chest pain, fainting) call your local emergency number or go to the nearest hospital. Do not wait for a reply here.")
    d = _my_rx()
    if d.empty:
        st.info("You can report a side effect once you have a prescription.")
        return
    with st.form("se"):
        rx = st.selectbox("Which prescription?", d.rx_id)
        symptom = st.text_area("What are you experiencing?")
        sev = st.select_slider("How severe?", ["mild", "moderate", "severe"])
        if st.form_submit_button("Send to my doctor") and symptom.strip():
            with db.engine().begin() as c:
                c.execute(db.side_effects.insert().values(ts=pd.Timestamp.utcnow().to_pydatetime(), patient_username=me,
                                                          rx_id=rx, symptom=symptom.strip(), severity=sev, status="new"))
            db.log(me, "side_effect_reported", f"{rx} ({sev})"); st.success("Sent. Your doctor will review it.")
    mine = db.df("select ts, rx_id, symptom, severity, status, doctor_note from side_effect_reports "
                 "where patient_username = :u order by ts desc", u=me)
    if not mine.empty:
        st.subheader("My reports"); st.dataframe(mine, width="stretch")


def patient_profile():
    st.header("My profile")
    st.caption("Doctors use this for safety checks (allergies, pregnancy, other medicines). Keep it up to date.")
    me = user["username"]
    p = db.get_profile(me)
    with st.form("prof"):
        c1, c2, c3 = st.columns(3)
        age = c1.number_input("Age", 0, 120, int(p["age"] or 30))
        weight = c2.number_input("Weight (kg)", 0.0, 250.0, float(p["weight"] or 65.0))
        pregnant = c3.checkbox("Pregnant", value=p["pregnant"])
        allergies = st.text_input("Allergies (comma separated)", value=p["allergies"])
        conditions = st.text_input("Medical conditions", value=p["conditions"])
        meds = st.text_input("Medicines I already take", value=p["current_meds"])
        consent = st.checkbox("I consent to my data being used for my prescriptions and safety checks.", value=p["consent"])
        if st.form_submit_button("Save profile", type="primary"):
            db.save_profile(me, age, weight, pregnant, allergies, conditions, meds, consent)
            st.success("Profile saved.")


PAGES = {
    "doctor": {"New prescription": doctor_new, "My prescriptions": doctor_list, "Patient feedback": doctor_feedback},
    "patient": {"My prescriptions": patient_rx, "Today's schedule": patient_schedule, "Request refill": patient_refill,
                "Report side effect": patient_report, "My profile": patient_profile},
    "pharmacist": {"Verification queue": pharm_queue, "Stock": pharm_stock},
    "admin": {"Analytics": admin_dash, "Users": admin_users, "Formulary": admin_formulary, "Audit log": admin_audit},
}[user["role"]]
choice = st.sidebar.radio("Menu", list(PAGES))
PAGES[choice]()
