"""Prescription PDF with QR code."""
import io
import qrcode
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas


def qr_png(rx_id, sig):
    """PNG bytes of the prescription QR code (same payload as in the PDF)."""
    q = io.BytesIO()
    qrcode.make(f"{rx_id}|{sig[:16]}").save(q, format="PNG")
    return q.getvalue()


def make_pdf(rx_id, sig, doctor, patient, age, allergies, diagnosis, rows, created):
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    w, h = A4
    y = h - 25 * mm
    c.setFont("Helvetica-Bold", 16); c.drawString(20 * mm, y, "Smart Rx Care - Prescription")
    c.setFont("Helvetica", 9); c.setFillGray(0.4)
    c.drawString(20 * mm, y - 6 * mm, "DEMO / SYNTHETIC DATA - for academic use, not a valid medical prescription")
    c.setFillGray(0)
    y -= 18 * mm
    c.setFont("Helvetica", 10)
    for line in [f"Prescription ID: {rx_id}", f"Date: {created}", f"Doctor: {doctor}",
                 f"Patient: {patient} (age {age})", f"Allergies: {allergies or 'None recorded'}",
                 f"Diagnosis: {diagnosis}"]:
        c.drawString(20 * mm, y, line); y -= 6 * mm
    y -= 4 * mm
    c.setFont("Helvetica-Bold", 10)
    c.drawString(20 * mm, y, "Medicine"); c.drawString(75 * mm, y, "Dose"); c.drawString(100 * mm, y, "Frequency")
    c.drawString(130 * mm, y, "Days"); c.drawString(145 * mm, y, "Instructions")
    y -= 2 * mm; c.line(20 * mm, y, 190 * mm, y); y -= 6 * mm
    c.setFont("Helvetica", 10)
    for r in rows:
        c.drawString(20 * mm, y, str(r["generic_name"])[:28])
        c.drawString(75 * mm, y, f'{r["dose_mg"]:g} mg')
        c.drawString(100 * mm, y, f'{r["times_per_day"]}x daily')
        c.drawString(130 * mm, y, str(r["duration_days"]))
        c.drawString(145 * mm, y, (r.get("instructions") or "")[:28])
        y -= 6 * mm
    qr = qrcode.make(f"{rx_id}|{sig[:16]}")
    q = io.BytesIO(); qr.save(q, format="PNG"); q.seek(0)
    c.drawImage(ImageReader(q), 20 * mm, 25 * mm, 35 * mm, 35 * mm)
    c.setFont("Helvetica", 8)
    c.drawString(60 * mm, 45 * mm, f"Digital stamp: {sig[:32]}")
    c.drawString(60 * mm, 40 * mm, "Electronically prepared and signed by the prescribing doctor.")
    c.drawString(60 * mm, 35 * mm, "Pharmacist must verify before dispensing.")
    c.save()
    return buf.getvalue()
