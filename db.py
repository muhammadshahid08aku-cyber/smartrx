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


@st.cache_resource
def engine():
    url = _url()
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


def hash_pw(pw, salt):
    return hashlib.pbkdf2_hmac("sha256", pw.encode(), salt.encode(), 200_000).hex()


def log(username, action, detail=""):
    with engine().begin() as c:
        c.execute(insert(audit).values(ts=dt.datetime.utcnow(), username=username, action=action, detail=detail))


@st.cache_resource
def init():
    md.create_all(engine())
    with engine().begin() as c:
        if c.execute(select(users.c.id).limit(1)).first() is None:
            try:
                demo_pw = st.secrets.get("DEMO_PASSWORD", "ChangeMe123!")
            except Exception:
                demo_pw = os.getenv("DEMO_PASSWORD", "ChangeMe123!")
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
    return True


def login(username, pw):
    with engine().connect() as c:
        r = c.execute(select(users).where(users.c.username == username)).first()
    if r and r.approved and secrets.compare_digest(r.pw_hash, hash_pw(pw, r.salt)):
        return dict(username=r.username, role=r.role, name=r.full_name)
    return None


def save_prescription(doctor, patient, age, weight, pregnant, allergies, diagnosis, rows, alerts):
    rx_id = "RX-" + dt.datetime.utcnow().strftime("%y%m%d") + "-" + secrets.token_hex(3).upper()
    now = dt.datetime.utcnow()
    payload = json.dumps([patient, age, weight, allergies, diagnosis, rows, doctor, rx_id, str(now)], sort_keys=True)
    sig = hashlib.sha256(payload.encode()).hexdigest()
    with engine().begin() as c:
        c.execute(insert(prescriptions).values(
            rx_id=rx_id, doctor=doctor, patient_name=patient, patient_age=age, patient_weight=weight,
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
