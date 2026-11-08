# Fakturama Image-to-Cash Automation

Takes one order image and produces a saved **Order** and its **linked, paid Invoice** in Fakturama 2.2, creating the Debtor, payment method, VAT rate and Products when they do not exist yet. Everything is done through Fakturama's desktop UI with Microsoft UI Automation, real keyboard/mouse input and OCR. Nothing writes to the database or any API.

**Status:** the full flow runs end to end from an empty workspace: Order `PO000001` and linked Invoice `INV000001`, 678.30 EUR, paid by Bank Transfer, both confirmed in Data > Documents. Each record is saved exactly once. 206 offline tests pass.

## Deliverables

| Task PDF asks for | Where |
|---|---|
| Part 1 design document (1–4 pages, no code) | [`design_document.md`](design_document.md), PDF in [`submission/design_document.pdf`](submission/design_document.pdf) |
| Source code | `fakturama_full_workflow.py` and the `fakturama/` package (layout below) |
| Setup instructions | Quick start below |
| Annotated screenshots or recording | [Screen recording of a full run (Google Drive)](https://drive.google.com/file/d/1Nx7aQJUT-jpEOz_-Vhor9UZE61jBh4KI/view?usp=drive_link); [`submission/annotated_execution.pdf`](submission/annotated_execution.pdf); raw frames and reports in `submission/evidence/` |
| What was skipped | Sections "Deviations" and "Limitations" below |
| "3 more hours" question | Last section |

## Quick start

Requirements: Windows 10/11, Python 3.12, [Fakturama 2.2](https://www.fakturama.info/download/), [Tesseract OCR](https://github.com/UB-Mannheim/tesseract/wiki) (`tesseract.exe` on `PATH`, or pass `--tesseract`).

```powershell
python -m pip install -r requirements.txt

# Extraction and validation only. Nothing on the desktop is touched.
python fakturama_full_workflow.py --image examples/source_order.png

# Full run in a new, isolated Fakturama workspace (recommended; your own data is never opened).
$env:FAKTURAMA_EXE = "C:\Program Files\Fakturama2\Fakturama.exe"   # only if installed elsewhere
python scripts/run_fresh_assessment.py --name run_01
```

`run_fresh_assessment.py` creates an empty EUR workspace under `evidence/assessment_isolated/run_01/`, starts Fakturama on it, waits for the window, then runs the workflow. Pick a new name each time; existing workspaces are never reused. Do not touch the mouse or keyboard while it runs. A run takes about 6 minutes on an idle machine.

Output goes to `evidence/run_01_execution/`: `execution_report.json` (every stage with PASS/STOP and reason), a screenshot per stage, and `extracted_order.json` (what OCR read from the image).

To run against a Fakturama instance you already have open:

```powershell
python fakturama_full_workflow.py --image examples/source_order.png --execute --allow-new --strict-assessment ^
    --workspace "<workspace path shown in the Fakturama title bar>" --evidence evidence/run_02
```

Use a new `--evidence` folder for every attempt. Without `--execute` the script only extracts and validates.

## How it works

```
image --OCR--> Order model --validate--> stages, one at a time --> execution_report.json

 00  look for an existing Order with this external reference (skipped for a fresh launcher workspace)
 01-02 New Order; Date, Cust.Ref., Net, With VAT                                 PDF 1.3-1.8
 03  search the Debtor in the Order's address selector                          PDF 2.1-2.4
 04  payment method: find or create (only when the Debtor is missing)           PDF 2.10
 05  Debtor: select, or create and re-select from the Order                      PDF 2.5-2.13
 06  per item: Product select-or-create (VAT first), then fill the line          PDF 3.1-3.17
 07  check addresses, lines and totals                                           PDF 4.1-4.3
 08  Save the Order once; confirm its row in Data > Documents                    PDF 4.4-4.5
 09  "Create follow-up document > Invoice" from the saved Order                  PDF 4.6-4.7
 10  check copied fields; payment method, paid, date, value; Save once; confirm  PDF 5.1-5.7
```

**Extraction** (`fakturama/ocr.py`). Tesseract at 3x scale; words are grouped into lines and the document's headings are used as anchors, so nothing depends on pixel positions. `Order.validate()` recomputes every line, the net, VAT and gross totals with `Decimal` and stops the run on any mismatch.

**Finding controls** (`fakturama/ui.py`). The script attaches to the `Fakturama - <workspace>` window, finds editors by tab text and fields by their label (the input immediately to the right of the matching label, inside the active editor). Every value typed is read back before the next step. Fakturama's tables (items grid, selector dialogs, Documents list) are custom-drawn and expose no cells to UIA, so their region is located from neighbouring controls and read with OCR (`fakturama/grid.py`).

**Safety rules.** Each record is saved once and the run stops rather than save twice. Anything ambiguous (two matching rows, an unexpected dialog, an unreadable table, a total that does not reconcile) stops the run with a report and screenshot. OCR captures are taken from Fakturama's own window content, so another application's window on top (a screen-share banner, a notification) does not corrupt a read. A file lock stops two runs from driving the same desktop. Nothing is ever deleted.

## Deviations from the task PDF

| PDF step | What the code does | Why |
|---|---|---|
| 2.10: create a missing payment method from inside the open Debtor editor | The payment method is found or created **before** the Debtor editor opens | Fakturama's Debtor editor loads its payment list once when it opens; a method created while it is open never shows up in the dropdown. Creating it first avoids closing and reopening the editor or saving the Debtor twice. |
| 2.12–2.13: select the new Debtor and confirm both addresses | As specified. If, after selection, the Delivery tab shows the billing company (a Fakturama 2.2.0 bug seen on every run), the same Debtor is selected once more with the Delivery tab active, then both addresses are re-checked | Keeps the single Order Save; without it the Delivery address is wrong. |
| 3.7: "Click New product" | The left-panel "New product" shortcut is clicked first, as written. If Fakturama does not open the editor within 4 s and shows no error dialog, the green **+** ("Create a new product") of the Products list is used instead; it opens the same editor. The run log records which path was taken. | Fakturama 2.2.0 has raised a `ClassCastException` on the shortcut when used right after an address selection; the fallback keeps the run going without a second click on the shortcut. |
| 1.1: "OCR and/or LLM" | OCR only | Offline and deterministic. An LLM fallback is the first item in the "3 more hours" list. |

Everything else follows the PDF as written: Order first, selectors used as existence checks, generated numbers left untouched, Invoice created through the follow-up action rather than the toolbar button, paid/date/value set only when the image says PAID, no Delivery/Correction/Dunning documents.

## Repository layout

```
fakturama_full_workflow.py     Entry point: CLI, stage sequence, journal, recovery receipt
fakturama/
  models.py                    Order / Item / Address, Decimal money, Order.validate()
  ocr.py                       Image -> Order (Tesseract)
  fields.py                    Parse/format numbers the way Fakturama shows them (1.234,56 €)
  ui.py                        UI Automation: window attach, label-based field lookup, date widget, captures
  grid.py                      OCR of Fakturama's custom tables
  order_ui.py                  Order header layout helpers
  execution.py                 Recovery receipt and saved-record checks
  evidence.py                  execution_report.json writer
  lease.py                     Desktop lock
  stages/                      One module per business stage (order_header, debtor, payment, product,
                               invoice, documents, selectors, controls, common)
scripts/
  run_fresh_assessment.py      One command: new isolated workspace + full run
  launch_assessment_workspace.py   Starts Fakturama on a fresh EUR workspace (called by the script above)
  build_annotated_evidence.py  Turns a run's screenshots into submission/annotated_execution.pdf
tests/                         206 offline tests (extraction, arithmetic, date widget, selectors, guards, captures)
examples/                      source_order.png (the input) and extracted_order.json (what OCR reads from it)
submission/                    design_document.pdf, annotated_execution.pdf, ASSESSMENT_STATUS.md, evidence/
docs/                          ARCHITECTURE.md, VERIFICATION_MATRIX.md, CHANGELOG.md
design_document.md             Part 1 design document
requirements.txt               Runtime dependencies; requirements-docs.txt is only for the annotated PDF
```

`evidence/` (local runs, isolated Fakturama workspaces) is git-ignored. Curated evidence from the recorded passes is under `submission/evidence/`.

## Limitations and what was skipped

- **The extractor is anchor-based, not layout-free.** It finds each section by its heading (several spellings are accepted: "External/Order Reference", "Items/Line Items/Positions", "Net Total/Subtotal/Totals", …) and reads values by pattern class rather than fixed literals: any reference or customer-id token, ISO codes or €/$/£ for currency, 4–6 digit postal codes with an optional country prefix, item rows with or without a unit word or discount column, `1,234.56` and `1.234,56` amounts, ISO / day-first / month-name dates. A document with no recognisable section headings, or one that reconciles arithmetically but not to the printed totals, stops at extraction with the raw OCR text saved; it never produces a wrong Order. Non-EUR currencies are rejected because the workspace is EUR. Layout-free extraction (any purchase order) is the LLM-vision item under "3 more hours".
- **Tested live with one image and one window layout** (maximised, single monitor). Not tested live: non-EUR, partial payments, repeated SKUs, order-level discount or shipping, item lists that need scrolling, a Debtor that exists with conflicting data. See `docs/VERIFICATION_MATRIX.md`.
- **Date entry.** The date field is a custom widget UIA cannot set. The script clicks the month segment, types `MMDDYYYY` and reads the result back; if the read-back is wrong it falls back to driving each segment with the arrow keys.
- **A few relative pixel offsets remain** where UIA gives nothing to anchor to (scroll point inside the editor, double-click position within a list row, crop margins around tables). They are relative to discovered controls, but an unusual DPI or theme could break them.
- **OCR varies between Tesseract builds.** One Linux build reads `INV000001` as `INVOOO0001`; the run then stops with "document number ambiguous" rather than guess. The Windows build used for the live runs reads it correctly.
- **Recovery after an interrupted Save** is covered by offline tests only.
- Only the three payment methods named in the PDF are mapped to E-Invoice codes.

Run `python -m pytest -q` for the offline tests (about 35 s). 13 of them need Windows (`pywinauto`); the rest run anywhere with Tesseract installed.

## If I had 3 more hours

1. **LLM-vision fallback for extraction**, producing the same `Order` model and going through the same `validate()`, so other purchase-order layouts work without hand-written parsing.
2. **Speed.** Most of the ~6 minutes is the type → read-back cycle on every field and repeated OCR of the same tables (the items grid is read about five times per product, once of them a plain duplicate). Reading several fields in one UIA pass and OCR-ing each table once per stage should roughly halve the run time without dropping any check.
3. **Cleaner stage boundaries.** The `stages/` modules are mixins on one `Runner`; giving each an explicit interface would let them be tested without the whole Runner.
4. **A second live fixture** (existing Debtor, three items, UNPAID) and a non-maximised window, to cover two more rows of the verification matrix.
