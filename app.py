"""Database layer: Supabase PostgreSQL in production, SQLite locally."""
import os, json, hashlib, secrets, random, datetime as dt
import pandas as pd
from sqlalchemy import (create_engine, MetaData, Table, Column, Integer, String, Text, Float,
                        Boolean, DateTime, select, insert, update)
import streamlit as st

from safety import drugs


def _url():
    try:
        return st.secrets["DATABASE_URL"]
    except Exception:
        return os.getenv("DATABASE_URL", "sqlite:///smartrx.db")


def _clean(url):
    """Tolerate common paste mistakes: quotes, spaces, [brackets] around the password,
    and special characters in the password (re-encoded safely)."""
    from urllib.parse import quote, unquote
    url = url.strip().strip("\"'").replace("[", "").replace("]", "")
    if "://" not in url:
        return url
    scheme, rest = url.split("://", 1)
    if "@" in rest:
        creds, host = rest.rsplit("@", 1)
        user, _, pw = creds.partition(":")
        rest = quote(unquote(user), safe=".") + ":" + quote(unquote(pw), safe="") + "@" + host
    return scheme + "://" + rest


def describe_url():
    """Safe description of the connection settings (never includes the password)."""
    from sqlalchemy.engine import make_url
    try:
        u = make_url(_clean(_url()))
        return {"driver": u.drivername, "host": u.host, "port": u.port, "user": u.username,
                "database": u.database, "password_length": len(u.password or "")}
    except Exception as e:
        return {"could_not_parse": type(e).__name__}


@st.cache_resource
def engine():
    url = _clean(_url())
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql+psycopg2://", 1)
    elif url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+psycopg2://", 1)
    return create_engine(url, pool_pre_ping=True)


md = MetaData()
users = Table("users", md,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("username", String(60), unique=True, nullable=False),
    Column("pw_hash", String(128)), Column("salt", String(32)),
    Column("role", String(20)), Column("full_name", String(120)), Column("approved", Boolean, default=False))
prescriptions = Table("prescriptions", md,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("rx_id", String(40), unique=True, nullable=False),
    Column("doctor", String(60)), Column("patient_name", String(120)),
    Column("patient_username", String(60)),
    Column("patient_age", Integer), Column("patient_weight", Float), Column("pregnant", Boolean),
    Column("allergies", Text), Column("diagnosis", Text), Column("status", String(20)),
    Column("alerts_json", Text), Column("signed_hash", String(64)),
    Column("created_at", DateTime), Column("dispensed_by", String(60)),
    Column("dispensed_at", DateTime), Column("pharmacist_note", Text))
items = Table("prescription_items", md,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("rx_id", String(40)), Column("generic_name", String(120)),
    Column("dose_mg", Float), Column("times_per_day", Integer),
    Column("duration_days", Integer), Column("instructions", Text))
inventory = Table("inventory", md,
    Column("generic_name", String(120), primary_key=True),
    Column("qty", Integer), Column("expiry", String(10)), Column("reorder_level", Integer))
audit = Table("audit_log", md,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("ts", DateTime), Column("username", String(60)),
    Column("action", String(60)), Column("detail", Text))
patient_profiles = Table("patient_profiles", md,
    Column("username", String(60), primary_key=True),
    Column("age", Integer), Column("weight", Float), Column("pregnant", Boolean),
    Column("allergies", Text), Column("conditions", Text), Column("current_meds", Text),
    Column("consent", Boolean, default=False), Column("consent_at", DateTime))
side_effects = Table("side_effect_reports", md,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("ts", DateTime), Column("patient_username", String(60)), Column("rx_id", String(40)),
    Column("symptom", Text), Column("severity", String(20)),
    Column("status", String(20)), Column("doctor_note", Text))
refills = Table("refill_requests", md,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("ts", DateTime), Column("patient_username", String(60)), Column("rx_id", String(40)),
    Column("note", Text), Column("status", String(20)), Column("doctor_note", Text))
dose_log = Table("dose_log", md,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("patient_username", String(60)), Column("rx_id", String(40)),
    Column("generic_name", String(120)), Column("slot", String(5)),
    Column("day", String(10)), Column("ts", DateTime))

PKT = dt.timezone(dt.timedelta(hours=5))


def today_pkt():
    return dt.datetime.now(PKT).date()


def demo_password():
    fallback = os.getenv("DEMO_PASSWORD", "ChangeMe123!")
    try:
        return st.secrets.get("DEMO_PASSWORD", fallback)
    except Exception:
        return fallback


def hash_pw(pw, salt):
    return hashlib.pbkdf2_hmac("sha256", pw.encode(), salt.encode(), 200_000).hex()


def log(username, action, detail=""):
    with engine().begin() as c:
        c.execute(insert(audit).values(ts=dt.datetime.utcnow(), username=username, action=action, detail=detail))


@st.cache_resource
def init():
    md.create_all(engine())
    # Migration: add patient_username to an existing prescriptions table
    from sqlalchemy import inspect, text
    cols = [col["name"] for col in inspect(engine()).get_columns("prescriptions")]
    if "patient_username" not in cols:
        with engine().begin() as c:
            c.execute(text("ALTER TABLE prescriptions ADD COLUMN patient_username VARCHAR(60)"))
    with engine().begin() as c:
        if c.execute(select(users.c.id).limit(1)).first() is None:
            demo_pw = demo_password()
            for u, role, name in [("admin", "admin", "System Admin"),
                                  ("dr_demo", "doctor", "Dr. Demo"),
                                  ("pharm_demo", "pharmacist", "Pharmacist Demo")]:
                salt = secrets.token_hex(8)
                c.execute(insert(users).values(username=u, pw_hash=hash_pw(demo_pw, salt), salt=salt,
                                               role=role, full_name=name, approved=True))
        if c.execute(select(inventory.c.generic_name).limit(1)).first() is None:
            rnd = random.Random(7)
            for g in drugs["generic_name"]:
                c.execute(insert(inventory).values(
                    generic_name=g, qty=rnd.choice([0, 15, 40, 120, 300]),
                    expiry=f"{rnd.choice([2027, 2028])}-{rnd.randint(1, 12):02d}", reorder_level=30))
        # Added after the base seed so existing deployments also get the demo patient
        if c.execute(select(users.c.id).where(users.c.username == "patient_demo")).first() is None:
            salt = secrets.token_hex(8)
            c.execute(insert(users).values(username="patient_demo", pw_hash=hash_pw(demo_password(), salt),
                                           salt=salt, role="patient", full_name="Demo Patient A", approved=True))
            c.execute(insert(patient_profiles).values(
                username="patient_demo", age=34, weight=70.0, pregnant=False, allergies="penicillin",
                conditions="Seasonal allergy (synthetic)", current_meds="", consent=True,
                consent_at=dt.datetime.utcnow()))
    return True


def login(username, pw):
    with engine().connect() as c:
        r = c.execute(select(users).where(users.c.username == username)).first()
    if r and r.approved and secrets.compare_digest(r.pw_hash, hash_pw(pw, r.salt)):
        return dict(username=r.username, role=r.role, name=r.full_name)
    return None


def create_user(username, pw, role, full_name):
    """Create an approved user. Patients also get an empty profile. Returns (ok, message)."""
    from sqlalchemy.exc import IntegrityError
    salt = secrets.token_hex(8)
    try:
        with engine().begin() as c:
            c.execute(insert(users).values(username=username, pw_hash=hash_pw(pw, salt), salt=salt,
                                           role=role, full_name=full_name, approved=True))
            if role == "patient":
                c.execute(insert(patient_profiles).values(username=username, age=30, weight=65.0, pregnant=False,
                                                          allergies="", conditions="", current_meds="", consent=False))
        return True, "Created."
    except IntegrityError:
        return False, "That username is already taken."


def get_profile(username):
    with engine().connect() as c:
        r = c.execute(select(patient_profiles).where(patient_profiles.c.username == username)).first()
    if r is None:
        return dict(age=30, weight=65.0, pregnant=False, allergies="", conditions="", current_meds="", consent=False)
    return dict(age=r.age, weight=r.weight, pregnant=bool(r.pregnant), allergies=r.allergies or "",
                conditions=r.conditions or "", current_meds=r.current_meds or "", consent=bool(r.consent))


def save_profile(username, age, weight, pregnant, allergies, conditions, current_meds, consent):
    vals = dict(age=int(age), weight=float(weight), pregnant=bool(pregnant), allergies=allergies.strip(),
                conditions=conditions.strip(), current_meds=current_meds.strip(), consent=bool(consent),
                consent_at=dt.datetime.utcnow() if consent else None)
    with engine().begin() as c:
        if c.execute(select(patient_profiles.c.username).where(patient_profiles.c.username == username)).first():
            c.execute(update(patient_profiles).where(patient_profiles.c.username == username).values(**vals))
        else:
            c.execute(insert(patient_profiles).values(username=username, **vals))
    log(username, "profile_updated")


def save_prescription(doctor, patient, age, weight, pregnant, allergies, diagnosis, rows, alerts, patient_username=None):
    rx_id = "RX-" + dt.datetime.utcnow().strftime("%y%m%d") + "-" + secrets.token_hex(3).upper()
    now = dt.datetime.utcnow()
    payload = json.dumps([patient, age, weight, allergies, diagnosis, rows, doctor, rx_id, str(now)], sort_keys=True)
    sig = hashlib.sha256(payload.encode()).hexdigest()
    with engine().begin() as c:
        c.execute(insert(prescriptions).values(
            rx_id=rx_id, doctor=doctor, patient_username=patient_username, patient_name=patient, patient_age=age, patient_weight=weight,
            pregnant=pregnant, allergies=allergies, diagnosis=diagnosis, status="pending",
            alerts_json=json.dumps(alerts), signed_hash=sig, created_at=now))
        for r in rows:
            c.execute(insert(items).values(rx_id=rx_id, **r))
    log(doctor, "prescription_signed", rx_id)
    return rx_id, sig


def df(query, **params):
    from sqlalchemy import text
    with engine().connect() as c:
        return pd.read_sql(text(query), c, params=params)
