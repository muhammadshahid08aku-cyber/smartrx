# Smart Rx Care (starter)

Closed-loop pharmacy demo: doctor prescribes -> rule-based safety check -> signed PDF with QR -> pharmacist verifies and dispenses -> stock updates -> analytics. AI (Groq) is used only to explain medicines in plain language. All safety decisions come from the CSV rule tables.

## What is in this pack
| File | Purpose |
|---|---|
| `app.py` | Streamlit app with doctor, pharmacist and admin portals |
| `safety.py` | Allergy, duplicate, interaction, dose, paediatric and pregnancy checks |
| `db.py` | SQLAlchemy layer (Supabase PostgreSQL or local SQLite), users, audit log |
| `pdf_rx.py` | Prescription PDF with QR code |
| `data/drugs.csv` | 41 common generics with WHO ATC codes (starter formulary) |
| `data/interactions.csv` | 38 well-known drug interaction rules |
| `data/dose_limits.csv`, `data/pregnancy.csv` | Max daily dose and pregnancy rules |
| `data/synthetic_prescriptions.csv` | 10 synthetic demo prescriptions for testing the safety engine |

## Important about the data
- The medicine list is a **starter formulary of common WHO-ATC generics**. It is **not** downloaded from DRAP and is not certified. Before real use, replace it with the DRAP registered-drug list (or your pharmacy's stock list) mapped to generic names.
- The interaction, dose and pregnancy tables are a small hand-built set for the demo, not a complete clinical database. A pharmacist or doctor must review and extend them. For broader coverage, evaluate DDInter 2.0 (check its licence) and DailyMed/openFDA label text.
- Prescriptions are **synthetic**. Do not load real patient data without hospital/ethics approval.
- The "digital stamp" is a SHA-256 hash for integrity, not a legally recognised e-signature.

## Run locally
```bash
cd smartrx
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
streamlit run app.py
```
With no `DATABASE_URL` it uses a local SQLite file. Default demo logins: `admin`, `dr_demo`, `pharm_demo`, password `ChangeMe123!` (or your `DEMO_PASSWORD`). Change it.

## Deploy (free): Supabase + Streamlit Community Cloud
1. **Supabase**: create a project at supabase.com. Go to Project Settings > Database > Connection string, choose the *Session pooler* URI, and put your DB password in it.
2. **GitHub**: create a new repository and push this `smartrx` folder (the `.gitignore` already excludes secrets).
   ```bash
   git init && git add . && git commit -m "Smart Rx Care"
   git branch -M main
   git remote add origin https://github.com/YOUR_USER/smartrx.git
   git push -u origin main
   ```
3. **Streamlit Community Cloud**: sign in at share.streamlit.io with GitHub, click *Create app*, pick the repo, branch `main`, main file `app.py`.
4. In *Advanced settings > Secrets*, paste the contents of `secrets.toml.example` with your real `DATABASE_URL`, a strong `DEMO_PASSWORD`, and optionally `GROQ_API_KEY`.
5. Click *Deploy*. On first start the app creates its tables in Supabase and seeds the three demo users and stock.
6. Sign in as `dr_demo`, create a prescription, then sign in as `pharm_demo` to verify and dispense. Check `admin` > Audit log.
7. Before sharing the URL, change the demo passwords (admin > Users) and keep the app private or use synthetic data only.

## Test the safety engine with the synthetic prescriptions
Enter the rows from `data/synthetic_prescriptions.csv` in the prescription builder. Expected highlights: DEMO-0003 (penicillin allergy + amoxicillin = red), DEMO-0004 (warfarin + ibuprofen = red), DEMO-0005 (pregnancy + ciprofloxacin = red), DEMO-0007 (two NSAIDs = amber duplication), DEMO-0008 (enalapril + losartan = red), DEMO-0010 (sulfa allergy + co-trimoxazole = red).

## Next steps (from your roadmap)
Patient portal and reminders, Records Agent (OCR), Orchestrator with LangGraph, Urdu output, Supabase Row Level Security, and migrating to the fuller schema (appointments, allergies, documents, dispensing history).
