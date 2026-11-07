"""Rule-based drug safety engine. No LLM is used for any decision here."""
from pathlib import Path
import pandas as pd

DATA = Path(__file__).parent / "data"

drugs = pd.read_csv(DATA / "drugs.csv")
interactions = pd.read_csv(DATA / "interactions.csv")
limits = pd.read_csv(DATA / "dose_limits.csv").set_index("generic_name")["max_daily_mg_adult"].to_dict()
pregnancy = pd.read_csv(DATA / "pregnancy.csv").set_index("generic_name").to_dict("index")

CLASS_BY_DRUG = drugs.set_index("generic_name")["drug_class"].to_dict()
ALIASES = {"sulfa": "sulfonamide", "quinolone": "fluoroquinolone", "penicillins": "penicillin",
           "nsaids": "nsaid", "cephalosporins": "cephalosporin", "macrolides": "macrolide"}

_pairs = {}
for r in interactions.itertuples():
    _pairs[frozenset((r.drug_a, r.drug_b))] = (r.severity, r.note)


def check(items, allergies="", age=None, weight=None, pregnant=False):
    """items: list of dicts {generic_name, dose_mg, times_per_day}. Returns alerts sorted by severity."""
    alerts = []
    names = [i["generic_name"] for i in items]

    # Allergies (free text, comma separated)
    for raw in [a.strip().lower() for a in allergies.split(",") if a.strip()]:
        key = ALIASES.get(raw, raw)
        for n in names:
            cls = CLASS_BY_DRUG.get(n, "").lower()
            if key in n.lower() or key in cls:
                alerts.append(dict(level="red", type="Allergy", msg=f"{n}: patient allergic to '{raw}'"))
            elif key == "penicillin" and "cephalosporin" in cls:
                alerts.append(dict(level="amber", type="Allergy cross-reactivity",
                                   msg=f"{n}: cephalosporin in a penicillin-allergic patient; verify history"))

    # Duplicates and same-class duplication
    seen = set()
    for n in names:
        if n in seen:
            alerts.append(dict(level="red", type="Duplicate", msg=f"{n} appears more than once"))
        seen.add(n)
    classes = {}
    for n in set(names):
        classes.setdefault(CLASS_BY_DRUG.get(n, n), []).append(n)
    for c, ns in classes.items():
        if len(ns) > 1:
            alerts.append(dict(level="amber", type="Therapeutic duplication",
                               msg=f"Same class ({c}): {', '.join(sorted(ns))}"))

    # Interactions
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            hit = _pairs.get(frozenset((a, b)))
            if hit:
                sev, note = hit
                lvl = {"major": "red", "moderate": "amber", "minor": "info"}[sev]
                alerts.append(dict(level=lvl, type=f"Interaction ({sev})", msg=f"{a} + {b}: {note}"))

    # Daily dose
    for it in items:
        n = it["generic_name"]
        daily = float(it.get("dose_mg") or 0) * float(it.get("times_per_day") or 0)
        if n in limits and daily > limits[n]:
            alerts.append(dict(level="red", type="Dose limit",
                               msg=f"{n}: {daily:g} mg/day exceeds adult max {limits[n]} mg/day"))

    # Age / weight
    if age is not None and age < 12:
        alerts.append(dict(level="amber", type="Paediatric",
                           msg="Child under 12: adult limits do not apply. Use weight-based dosing and verify."))
        if not weight:
            alerts.append(dict(level="amber", type="Paediatric", msg="Weight missing for a child"))
    if age is not None and age >= 65:
        alerts.append(dict(level="info", type="Elderly", msg="Age 65+: review renal function and sedative/NSAID use"))

    # Pregnancy
    if pregnant:
        for n in names:
            p = pregnancy.get(n)
            if p is None:
                alerts.append(dict(level="amber", type="Pregnancy", msg=f"{n}: no pregnancy data in the rule table; verify"))
            elif p["pregnancy_risk"] == "avoid":
                alerts.append(dict(level="red", type="Pregnancy", msg=f"{n}: {p['note']}"))
            elif p["pregnancy_risk"] == "caution":
                alerts.append(dict(level="amber", type="Pregnancy", msg=f"{n}: {p['note']}"))

    order = {"red": 0, "amber": 1, "info": 2}
    return sorted(alerts, key=lambda a: order[a["level"]])
