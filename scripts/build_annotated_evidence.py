"""Build submission/annotated_execution.pdf from one live run's screenshots.

Usage:  python scripts/build_annotated_evidence.py [evidence/<run>_execution]

Each page is one screenshot from the run with numbered boxes and notes that tie
what is visible to the step of the task PDF it satisfies. The screenshots are
also copied to submission/evidence/ so the PDF and its sources travel together.
Box coordinates are in the pixels of the 1938x1038 maximised-window captures
produced by the runner.
"""

import shutil
import sys
from pathlib import Path

from PIL import Image
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

ROOT = Path(__file__).resolve().parents[1]
RUN = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "evidence" / "seq5_execution"
OUT = ROOT / "submission"
(OUT / "evidence").mkdir(parents=True, exist_ok=True)


def frame(pattern):
    hits = sorted(RUN.glob(pattern))
    if not hits:
        raise SystemExit(f"No screenshot matching {pattern} in {RUN}")
    return hits[-1]


PAGES = [
    dict(
        title="PDF 2.5-2.10: New Debtor filled from the image, before its single Save",
        subtitle="Order PO000001 stays open in its tab. Bank Transfer was created first (documented deviation) and "
        "is already in the terms-of-payment list below.",
        source=frame("*_BEFORE_SAVE_New_Debtor.png"),
        name="annotated_1_new_debtor_before_save.png",
        boxes=[
            (497, 142, 850, 165),
            (560, 188, 1000, 345),
            (570, 460, 1190, 615),
            (1300, 460, 1885, 520),
            (430, 770, 1190, 798),
        ],
        notes=[
            "1  *New Order and *New Debtor are both open and unsaved (PDF 1.8, 2.5).",
            "2  Customer ID left as proposed; Company, First Name, Last Name from the image (2.6).",
            "3  Main address: Street, ZIP, City, Country Germany; the billing address from the image (2.7).",
            "4  E-Mail and Telephone from the Customer and Contact block (2.7).",
            "5  Bank Transfer exists in terms of payment before the Debtor is saved (2.10, payment-first deviation).",
        ],
    ),
    dict(
        title="PDF 2.12-2.13: saved Debtor selected from the still-open Order",
        subtitle="After one Debtor Save the Order's address selector found CUST000001 as the single exact match; "
        "both address tabs were read back.",
        source=frame("*_PASS_05_debtor_select_or_create.png"),
        name="annotated_2_debtor_selected_in_order.png",
        boxes=[
            (497, 142, 1015, 165),
            (445, 182, 645, 205),
            (478, 255, 975, 280),
            (478, 285, 975, 360),
            (430, 745, 1190, 795),
        ],
        notes=[
            "1  Editor tabs: the Order draft, the saved Bank Transfer and the saved Debtor; nothing else was opened.",
            "2  Proposed Order number PO000001 untouched (1.4).",
            "3  Cust.Ref. = WEB-2026-0714-A17, Date = Jul 14, 2026 (1.5-1.6).",
            "4  Invoice address populated from the selected Debtor; the Delivery tab was verified the same way (2.13).",
            "5  Lower list: Cash (standard, unchanged) and the new Bank Transfer (2.10.5).",
        ],
    ),
    dict(
        title="PDF 3.13-3.16 and 4.1-4.3: both item lines and the totals match the source",
        subtitle="Products CHR-ERG-01 and MAT-DESK-02 were created with gross master prices 297,50 / 47,60 and "
        "re-selected from the Order; line discounts applied on the Order only.",
        source=frame("*_PASS_07_verify_order.png"),
        name="annotated_3_order_lines_and_totals.png",
        boxes=[
            (445, 380, 1620, 432),
            (1370, 380, 1495, 432),
            (1580, 490, 1900, 640),
            (430, 745, 1190, 795),
            (1020, 142, 1450, 165),
        ],
        notes=[
            "1  Qty 2.00 x 250,00 at -10 % = 450,00; Qty 3.00 x 40,00 = 120,00; VAT 19 % on both (3.13-3.16).",
            "2  Line discount lives on the Order line, not on the Product master (3.9, 3.15).",
            "3  Total Net 570,00 + VAT 108,30 = Total 678,30; Discount 0 %, Free of shipping costs (4.2-4.3).",
            "4  Products list: master prices 297,50 and 47,60 = unit net x 1.19 (3.9).",
            "5  VAT 19 % and both Product editors were saved once each and left open (3.6, 3.11).",
        ],
    ),
    dict(
        title="PDF 4.4-4.5: Order saved once and confirmed in Data > Documents",
        subtitle="The tab now carries the generated number; the Documents list, filtered by the external reference, "
        "shows exactly one Order row.",
        source=frame("*_PASS_08_save_order.png"),
        name="annotated_4_order_saved_documents_row.png",
        boxes=[
            (497, 142, 605, 165),
            (590, 745, 1760, 770),
            (1765, 685, 1910, 710),
            (1424, 320, 1900, 360),
        ],
        notes=[
            "1  Editor tab renamed to PO000001 after the single Save (4.4).",
            "2  Documents row: PO000001, Jul 14 2026, Northstar Office GmbH, WEB-2026-0714-A17, open, 678,30 (4.5).",
            "3  Search box holds the external reference; the list is filtered to this transaction only.",
            "4  Create a follow-up document: the Invoice is created from here, not from the toolbar (4.6).",
        ],
    ),
    dict(
        title="PDF 5.2-5.5: linked Invoice paid, saved once, both rows confirmed in Documents",
        subtitle="Final state of the run (matches the task's Figure 10). INV000001 was created from PO000001 and "
        "carries its reference, addresses, lines and totals.",
        source=frame("*_PASS_10_invoice_paid_and_verified.png"),
        name="annotated_5_invoice_paid_verified.png",
        boxes=[
            (500, 142, 1520, 165),
            (397, 590, 860, 618),
            (1580, 490, 1900, 640),
            (590, 745, 1760, 770),
            (1000, 340, 1420, 370),
        ],
        notes=[
            "1  PO000001 remains open in its tab beside INV000001 (5.5).",
            "2  paid ticked, Bank Transfer, at Jul 18 2026, Value 678,30 = full invoice total (5.2-5.3).",
            "3  Totals copied from the Order: 570,00 / 108,30 / 678,30 (5.1).",
            "4  Invoices list: INV000001, paid, 678,30 for reference WEB-2026-0714-A17 (5.5).",
            "5  Order Date Jul 14 2026 copied; Invoice Date and Service date left as proposed (5.1).",
        ],
    ),
]


def main():
    page_w, page_h = landscape(A4)
    pdf = OUT / "annotated_execution.pdf"
    c = canvas.Canvas(str(pdf), pagesize=(page_w, page_h))
    c.setTitle("Fakturama Image-to-Cash - annotated execution evidence")
    for number, page in enumerate(PAGES, 1):
        c.setFillColor(colors.HexColor("#16324F"))
        c.setFont("Helvetica-Bold", 15)
        c.drawString(30, page_h - 30, page["title"])
        c.setFillColor(colors.HexColor("#444444"))
        c.setFont("Helvetica", 8.5)
        c.drawString(30, page_h - 45, page["subtitle"])
        shutil.copy2(page["source"], OUT / "evidence" / page["name"])
        image = Image.open(page["source"])
        scale = min((page_w - 60) / image.width, (page_h - 175) / image.height)
        x, y = 30, 122
        c.drawImage(
            ImageReader(image), x, y, width=image.width * scale, height=image.height * scale
        )
        for i, (left, top, right, bottom) in enumerate(page["boxes"], 1):
            bx = x + left * scale
            by = y + (image.height - bottom) * scale
            c.setStrokeColor(colors.HexColor("#D24B27"))
            c.setLineWidth(1.4)
            c.rect(bx, by, (right - left) * scale, (bottom - top) * scale, fill=0)
            cx, cy = bx - 6, by + (bottom - top) * scale + 5
            c.setFillColor(colors.HexColor("#16324F"))
            c.circle(cx, cy, 6.5, stroke=0, fill=1)
            c.setFillColor(colors.white)
            c.setFont("Helvetica-Bold", 7.5)
            c.drawCentredString(cx, cy - 2.5, str(i))
        c.setFont("Helvetica", 8.3)
        for i, note in enumerate(page["notes"]):
            c.setFillColor(colors.HexColor("#16324F"))
            c.drawString(30, 104 - i * 16, note)
        c.setFillColor(colors.HexColor("#777777"))
        c.setFont("Helvetica", 7)
        c.drawString(
            30,
            14,
            f"Run: {RUN.name}  -  source frame: {page['source'].name}  -  page {number}/{len(PAGES)}",
        )
        c.showPage()
    c.save()
    print(f"Wrote {pdf} ({len(PAGES)} pages) and {len(PAGES)} frames to {OUT / 'evidence'}")


if __name__ == "__main__":
    main()
