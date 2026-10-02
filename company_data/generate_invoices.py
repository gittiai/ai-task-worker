"""Generate the sample vendor invoices in company_data/inbox/.

Each one is a real PDF with a different layout, so extraction can't rely on one
template. Several are deliberately awkward to exercise the agent's judgement.
"""

from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

INBOX = Path(__file__).parent / "inbox"
styles = getSampleStyleSheet()

INVOICES = [
    # file, vendor, number, invoice date, due date line, amount text, items, note
    ("stark_logistics_2026-09-28.pdf", "Stark Logistics", "STK-1042", "28 Sep 2026",
     "Due Date: 30 Oct 2026", "24,500.00", [("Freight Mumbai to Pune (4 loads)", "24,500.00")], ""),
    ("stark_logistics_2026-08-15.pdf", "Stark Logistics", "STK-0988", "15 Aug 2026",
     "Due Date: 14 Sep 2026", "9,800.00", [("Freight Pune to Nashik", "9,800.00")], ""),
    ("WOC_77812.pdf", "Wayne Office Co", "WOC-77812", "2026-09-18",
     "Payment due by 2026-10-18", "12,999.00",
     [("Ergonomic chairs x3", "10,500.00"), ("Delivery", "499.00"),
      ("GST 18% (part)", "2,000.00")], ""),
    ("scan_0012.pdf", "Hooli Cloud Services", "HCS-INV-5521", "01/10/2026",
     "Due: 25/10/2026 (DD/MM/YYYY)", "1,24,000.00",
     [("Annual cloud hosting plan", "1,05,084.75"), ("IGST 18%", "18,915.25")], ""),
    ("initech_invoice_sept.pdf", "Initech Pvt Ltd", "INI-4512", "25 September 2026",
     "Payment terms: Net 30 from invoice date", "6,250.00",
     [("Printer maintenance (Sept)", "6,250.00")], ""),
    ("umbrella_supplies.pdf", "Umbrella Supplies", "UMB-0131", "29 Sep 2026",
     "", "4,410.00", [("Cleaning supplies", "4,410.00")], "Thank you for your business!"),
    ("globex_reminder.pdf", "Globex Corporation", "GLX-2026-0815", "02 Aug 2026",
     "Due Date: 01 Sep 2026", "18,250.00", [("Consulting retainer (Aug)", "18,250.00")],
     "REMINDER: this invoice is now overdue."),
]


def build(file, vendor, number, inv_date, due_line, total, items, note, layout):
    doc = SimpleDocTemplate(str(INBOX / file), pagesize=A4, leftMargin=20 * mm, rightMargin=20 * mm)
    story = []
    if layout == 0:
        story += [Paragraph(f"<b>{vendor}</b>", styles["Title"]),
                  Paragraph("TAX INVOICE", styles["Heading2"]),
                  Paragraph(f"Invoice No: {number}", styles["Normal"]),
                  Paragraph(f"Invoice Date: {inv_date}", styles["Normal"])]
        if due_line:
            story.append(Paragraph(due_line, styles["Normal"]))
        story.append(Paragraph("Bill To: Acme Corp, Finance Dept, Bengaluru", styles["Normal"]))
    else:
        meta = [["Bill To", "Acme Corp — Accounts Payable"], ["Reference", number],
                ["Issued", inv_date]]
        if due_line:
            meta.append(["Terms", due_line])
        story += [Paragraph(f"{vendor.upper()}", styles["Heading1"]), Spacer(1, 4 * mm)]
        t = Table(meta, colWidths=[35 * mm, 120 * mm])
        t.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.5, colors.grey)]))
        story.append(t)
    story.append(Spacer(1, 8 * mm))
    rows = [["Description", "Amount (INR)"], *items, ["TOTAL PAYABLE", f"INR {total}"]]
    t = Table(rows, colWidths=[120 * mm, 40 * mm])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
        ("LINEABOVE", (0, -1), (-1, -1), 1, colors.black),
        ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
    ]))
    story.append(t)
    if note:
        story += [Spacer(1, 8 * mm), Paragraph(note, styles["Italic"])]
    doc.build(story)


def main():
    INBOX.mkdir(exist_ok=True)
    for i, inv in enumerate(INVOICES):
        build(*inv, layout=i % 2)
    print(f"Wrote {len(INVOICES)} invoices to {INBOX}")


if __name__ == "__main__":
    main()
