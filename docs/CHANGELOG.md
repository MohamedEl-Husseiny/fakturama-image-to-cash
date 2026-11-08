# Development log and run history

Chronological record of every live attempt, stop, fix and evidence directory (newest first). The older sections are the former README body, kept for traceability; the README holds only the current state. Paths under `evidence/` are local and git-ignored; curated copies live in `submission/evidence/`.

Existing Windows Python project continued in place. All Fakturama changes used desktop UI Automation/native input/OCR; no database or application API writes. The original workspace was not recreated or restarted. A separately authorized test instance uses an isolated profile and business workspace.

## 8 November 2026 - sequence fidelity and speed

Comment pass across the package: 257 comment lines reduced to 143 and docstrings from 347 to 265 lines (10 % to 7 % of non-blank lines). History and observations moved out of the code into this log; comments now state the rule and the reason. Verified by AST comparison that no code changed.

**Live result:** `seq5` (14:25-14:46 local) passed uninterrupted on the PDF-faithful sequence, exit 0, seven Saves (Payment 1 / Debtor 1 / VAT 1 / Product 2 / Order 1 / Invoice 1), final state as in the task's Figure 10. Curated as `submission/evidence/sequence_faithful_*`. Three stops preceded it, each caught by a guard before any wrong Save: `seq1` - after the 5.1 confirmation the editor stayed scrolled down and `Cust.Ref.` was out of the captured bounds (fixed: scroll to top and re-take bounds); `seq2`/`seq3` - "This transaction" is not a unique Documents node right after a Save (fixed: 5.5 reads the Invoices and Orders categories and checks the Order tab is still open; `seq3` ran the old file because of a staging mix-up); `seq4` - run while a remote-desktop session held the screen, so its always-on-top window kept keyboard focus and the selector search keystrokes went into it; the search read-back mismatch stopped the run at 2.2. Lesson recorded: nothing may hold the foreground while the runner drives.


The run now follows the PDF step list with no added steps apart from the two documented deviations. Removed: the two extra "Select the address" searches (2.2 asks for one, by Company); the reopen-and-reverify of the Order after Save (4.5 asks for the Documents row) and of the Invoice after Save (5.5 asks for the Invoice and Order rows in Documents); 5.1's copied-field confirmation now happens before payment entry instead of after Save. The conditional Delivery re-selection stays: it runs only when Fakturama 2.2.0 shows the billing company on the Delivery tab, which it did in every run to date. The re-run / recovery / `--verify-existing` paths keep the full reopen verification (`reopen=True`).

Date entry: fast path first (click month, type `MMDDYYYY`, one readback), probe path only on mismatch. `--assume-fresh-workspace` (passed by `run_fresh_assessment.py`) skips stage 00 for a launcher-created workspace. All OCR captures now use the window's own rendered content (`PrintWindow`), confirmed live with a Google Meet screen-share banner over the Documents table (`bon2`); a screen grab is used only if rendering fails and only when no other process's window overlaps the region. `field()` no longer screenshots on each polling attempt. Offline suite: 181.

## 7 November 2026 - submission clean-up, date-entry fix, Runner split

Two fresh isolated EUR runs passed uninterrupted on the reworked code: `post_refactor_b` (13:08-13:18 Cairo) and `post_split_a` (13:55-14:05). Each: PO000001 open 678.30 / INV000001 paid 678.30 by Bank Transfer on 2026-07-18, every address, line and total verified after reopen, exactly Payment 1 / Debtor 1 / VAT 1 / Product 2 / Order 1 / Invoice 1 Saves, exit 0. Curated: `submission/evidence/post_refactor_fresh_*`.

Changes: README rewritten (history moved here); first-iteration CLI/workflow/probes moved to `legacy/`; `black` formatting; `FakturamaUI.controls()` pushes the control-type filter into the UIA query; `launch_assessment_workspace.py` takes `--executable` / `FAKTURAMA_EXE`; `Runner` split mechanically into `fakturama/stages/*` mixins (AST-verified; only change a static-method reference and three unused inline imports).

Bug found and fixed: `post_refactor_a` stopped at `02_header` with `Date mismatch: expected 2026-07-14, got 'Jul 7, 0014'`. The CDateTime day segment was selected by a fixed 0.34-width click calibrated on a two-digit day (`Oct 17`); with today's one-digit day (`Nov 7`) the text is shorter and the click landed on the year. `_set_order_date_by_uia` now probes which segment is selected (Arrow Up changes one field, Arrow Down restores it) and steers with Arrow Left/Right before typing. `tests/test_date_segments.py` reproduces the failure against a fake widget. Offline suite: 167 passed on Windows.

## Latest verification - 17 October 2026, 22:03 Cairo

**Live resumed end-to-end PASS with the current safety/refactor code.** A new isolated EUR workspace `evidence/assessment_isolated/safety_e2e_20261017/business` was launched by `python scripts/run_fresh_assessment.py --name safety_e2e_20261017`. Python created Payment before Debtor, VAT and both Products, repaired delivery binding before the first Order Save, and saved PO000001 once. Reopen verification stopped because a grid screen capture contained another foreground window. Recovery then exposed a second issue: Fakturama has no Invoices tree category until an Invoice exists. Both stops and their reports are retained.

Small fixes restore the owner before document scrolling and check the exact workspace foreground before and after grid/cell captures; an image captured during focus loss is discarded. Discovery now accepts an absent Orders/Invoices category only when its counterpart exists uniquely during full discovery; explicitly requested categories and duplicate categories still fail. Recovery receipts still reject missing previously saved document identities. Regression tests cover both fixes.

The same saved Order was resumed using the main Python CLI, `--execute --allow-new --strict-assessment --workspace evidence/assessment_isolated/safety_e2e_20261017/business`, with separate evidence directories `evidence/safety_e2e_20261017_resume_a` (stopped discovery) and `evidence/safety_e2e_20261017_resume_b` (exit 0). No manual record actions or duplicate/corrective Order Save occurred. The successful continuation independently reopened the Order, created its follow-up INV000001, applied full Bank Transfer payment dated 18 July 2026, saved the Invoice once, reopened it and verified the exact open Order / paid Invoice pair at **678.30 EUR**. Both addresses and source lines passed persisted readback; generated Invoice/service dates remained 17 October 2026.

Aggregate Saves across all three attempts: **Payment 1, Debtor 1, VAT 1, Products 2 (one each), Order 1, Invoice 1**. This is a **resumed** execution, not a new uninterrupted pass; the earlier uninterrupted fresh run remains separate evidence. All older workspaces and records are preserved. Payment-before-Debtor remains the approved difference from the PDF sequence.

**Offline: 160 passed in 33.59 seconds**, `python -m pytest -q --basetemp evidence/pytest_safety_final`. Added simulated failure tests cover both document types: Save input occurs only after a durable attempt marker; receipt-write failure prevents Save input. These are offline simulations, not real Fakturama termination around Save. That interruption matrix and the broader business/layout cases remain pending in `docs/VERIFICATION_MATRIX.md`.

Curated evidence: `submission/evidence/safety_e2e_verified_summary.json`, the three `safety_e2e_*_report.json` reports, original failed manifest and `safety_e2e_verified_pair.png`. The summary audits aggregate save counts and records code hashes. Full local evidence retains each stop independently.

## Driver ownership and Save-recovery safety - 17 October 2026

The next architecture increment adds `fakturama/lease.py`: every Runner acquires an exclusive OS-backed lease before initializing desktop controls. Physical keyboard/mouse input is shared, so different workspaces and read-only runs also exclude one another. The main CLI releases the lease through a context manager; initialization failure and process exit release it too. A leftover lock file is not ownership and must not be deleted to bypass a running process. This coordinates this project's Runner-based workflows, not humans or unrelated automation tools.

Recovery receipts now use validated `RecoveryPhase` values, retain legacy string compatibility and prevent document/source identity reassignment. Atomic updates preserve prior in-memory and on-disk state on failure. Immediately before document Save input, a durable Order/Invoice Save-attempt marker is written. On recovery, UI discovery must find the exact attempted/saved document number. Missing or conflicting results stop before automatic replacement or Save retry. A marker is not proof of a successful Save; independent reopen verification remains mandatory. Master-record interruption handling still relies on the existing UI lookup/draft guards.

**Latest offline verification: 152 passed in 34.17 seconds**, using `python -m pytest -q --basetemp evidence/pytest_safety_extended_full`. The expanded failure matrix checks both Order and Invoice Save-input interruptions and blocks Save input when the receipt marker cannot be written. Real child-process tests verify lock contention and OS release after a test-process crash. Temporary-receipt and mocked-input tests verify durable marker ordering, reload, unresolved outcome stops, saved-record disappearance, identity protection and failed replacement consistency. These do not terminate Fakturama or perform real document Saves.

**Live verification:** `evidence/recovery_lease_readonly_a/` exited 0 at 21:41:06 Cairo, reopening PO000001 and linked paid INV000001 with correct addresses, lines, totals and payment; **zero business-record Saves**. A simultaneous second main CLI invocation was denied before desktop initialization. After the first CLI exited, an independent lease acquisition succeeded. The latest phase-policy extension was also checked against the captured live record identities. Curated evidence: `submission/evidence/recovery_lease_verification_summary.json` and `recovery_lease_readonly_report.json`.

The later resumed creation run above verifies the write flow after this increment. Controlled interruption around a real Fakturama Save remains pending. Existing records and all previous environments remain preserved; the earlier uninterrupted fresh run remains separate evidence.

## Historical first architecture extraction - 17 October 2026

The first safe extraction is complete. `fakturama/fields.py` now provides shared desktop-independent numeric parsing/formatting for workflow fields and grid verification. Multiple amounts and ambiguous signs are rejected; parsing preserves quantity precision while monetary callers explicitly apply cent rounding. The verified entry format remains two decimal places; this does not establish support for three-decimal quantity widgets. Existing `decimal_from_ui` / `number_for_ui` imports remain compatible.

`fakturama/evidence.py` now owns the per-attempt ExecutionJournal. Reports preserve the existing event schema and are replaced atomically; interrupted writes retain the last valid report and in-memory state. Existing report paths are refused rather than overwritten: always use a new `--evidence` directory for each attempt or verification. Recovery receipts remain separate from evidence and remain identity hints only.

**Offline result: 132 passed in 31.66 seconds**, `python -m pytest -q --basetemp evidence/pytest_architecture_full`. Added tests cover signed/grouped/ambiguous numeric fields, quantity precision versus monetary rounding, event-schema/Unicode preservation, failed atomic replacement and report-overwrite protection.

**Live integration result: read-only PASS**, `evidence/architecture_readonly_verify_a/`, exit 0 at 21:31:13 Cairo. The refactored code reopened the completed fresh trial's PO000001 and INV000001 and independently verified both addresses, lines, source identity/totals, full Bank Transfer payment on 18 July 2026 and the open/paid transaction pair. **Zero business-record Saves.** This is not a new write-flow or fresh creation test after the refactor; the prior fresh run below remains separate evidence.

Architecture boundaries and the remaining UI-adapter/recovery-state work are documented in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md). The prioritized broader-case plan and actual coverage are in [docs/VERIFICATION_MATRIX.md](docs/VERIFICATION_MATRIX.md). Most proposed broader live cases remain pending. Non-EUR source data, partial payments, repeated SKU lines, nonzero shipping/overall discount and long scrolling item lists must not be presented as established live capabilities. Concurrent writers to one workspace are not supported.

Curated evidence: `submission/evidence/architecture_verification_summary.json` and `architecture_readonly_report.json`. Existing saved records and every earlier workspace remain preserved. This first extraction does not change save sequencing or introduce save retries.

## Fresh Python-only run - LIVE PASS

On **17 October 2026, 21:19:39 Cairo time**, the single Python command below completed a new isolated EUR execution from the original image, **without manual UI intervention, process restarts, code edits or recovery resumes during that successful execution**:

```powershell
python scripts/run_fresh_assessment.py --name fresh_python_v3_20261017
```

That name now belongs to completed records. For another fresh test, choose a **new unused name**, for example:

```powershell
python scripts/run_fresh_assessment.py --name my_next_euro_trial
```

The entry point launches the installed Fakturama into a separate empty profile/business workspace, configures EUR before startup, waits for the workspace window, and invokes the image-driven strict workflow. It refuses existing workspace/profile/evidence directories. `--image PATH` optionally selects another source image. No existing workspace is reset, and all business records are entered through the desktop UI.

**Verified live results in `evidence/assessment_isolated/fresh_python_v3_20261017/business`:**

- Original image extracted and validated; empty Documents list and absent Debtor established before creation.
- Bank Transfer created before Debtor, with Credit transfer code, then saved/verified. CUST000001 created with source contacts, billing/delivery roles, alias and Bank Transfer; saved once and reselected through the same Order.
- Python automatically initialized the delivery-tab binding and corrected the warehouse company before the first Order Save. Billing and warehouse delivery passed before Save and after both documents were reopened.
- Missing VAT 19% created with code S (Standard rate). Both missing Products created, saved once each, and reselected by exact SKU from the Order. Master gross prices 297.50 / 47.60 were read back in their selectors.
- **PO000001 saved once and reopened:** source reference/date, quantity 2 chair at net 250.00 with 10% discount, quantity 3 mat at net 40.00 with no discount; both VAT 19%, line nets 450.00 / 120.00. Net 570.00 + VAT 108.30 = gross **678.30 EUR**; overall discount/shipping zero.
- **Linked INV000001** created only through the saved Order's follow-up Invoice action. Generated Invoice number/date/service date retained (17 October 2026 for both dates). Saved once, reopened and verified PAID by Bank Transfer on **18 July 2026**, full value **678.30 EUR**.
- Final This transaction readback verifies **PO000001 open 678.30 / INV000001 paid 678.30**. Process and launcher exited **0**. EUR remained configured/displayed throughout.

The successful run has exactly **Payment 1, Debtor 1, VAT 1, Products 2, Order 1 and Invoice 1** Save events. No corrective Order Save or extra document was used. All previous completed workspaces and failed-attempt drafts remain preserved.

**Scope of PDF compliance:** this is now a fresh uninterrupted end-to-end Python workflow, retaining the project owner's explicit **payment-before-Debtor** change. It therefore differs from the PDF's literal same-open-Debtor payment sequence at that approved step. The single-Order-Save requirement is met.

**Offline verification:** **107 passed in 35.20 seconds**, `python -m pytest -q --basetemp evidence/pytest_python_startup_full`. These offline tests are distinct from the live pass. The fresh work added automatic owner-focus restoration, atomic native Documents-search input, and Products-list Add selection. Fakturama's navigation New product command omits force-new and can cast the previous Debtor selection incorrectly; the list Add command explicitly forces a new Product, as confirmed by read-only installed-command metadata and live creation.

**Evidence:** `evidence/fresh_python_v3_20261017_execution/` contains the complete report, source extraction, screenshots and launcher manifest with exit 0, source hash and timestamps. Curated copies: `submission/evidence/fresh_python_verified_summary.json`, `fresh_python_execution_report.json`, `fresh_python_run_manifest.json`, `fresh_python_verified_pair.png` and `fresh_python_saved_order.png`.

Earlier fresh attempts are retained honestly: `fresh_euro_20261017_run_a` stopped at discovery with Preferences open; `_run_b` stopped at Payment selection after foreground loss, before any record Save. `fresh_python_20261017_execution` created Payment/Debtor/VAT and proved the automatic address repair, then stopped at Fakturama's navigation Product command. `fresh_python_v2_20261017_execution` stopped at initial search before creating records. These failures prompted tested code fixes; none is presented as the successful uninterrupted run.

## Historical verified euro continuation - 17 October 2026

**LIVE VERIFIED at 20:47:53 Cairo time.** In the authorized `euro_trial_20261017` workspace, the original image's source reference `WEB-2026-0714-A17` now has saved Order **PO000001** and its linked, fully paid Invoice **INV000001**. Both were reopened through Fakturama's Documents UI. This transaction shows the Order **open 678.30** and Invoice **paid 678.30**, with EUR amounts displayed throughout.

The saved Order retains 14 July 2026, the billing address and **Northstar Office Warehouse / Beusselstrasse 44 / 10553 Berlin / Germany**. Both persisted documents passed exact line checks: chair quantity 2, net unit 250.00, VAT 19%, discount 10%, line net 450.00; mat quantity 3, net unit 40.00, VAT 19%, discount 0%, line net 120.00. Net 570.00 + VAT 108.30 = gross 678.30; shipping and overall discount are zero. The reopened Invoice confirms Bank Transfer, PAID, paid date 18 July 2026 and full payment 678.30. Fakturama's proposed Invoice number, Invoice date and service date were retained (17 October 2026 for both dates).

Across all attempts in this euro workspace, execution reports contain exactly **one Payment Save, one Debtor Save, one VAT Save, two Product Saves (one per SKU), one Order Save and one Invoice Save**. Subsequent verification made zero saves. No corrective Order Save was used. Original and older test environments remain preserved.

This is a **verified resumed end-to-end execution**, with assisted investigation and several guarded Python continuations; it is **not one uninterrupted fresh autonomous pass**. Payment-before-Debtor remains the project owner's approved change to the PDF sequence. A fresh run of the newly integrated automatic delivery-binding repair has not yet been exercised; its equivalent UI sequence was tested on this draft and its address persisted after the only Order Save.

The delivery fix reselects the exact same Debtor while Delivery is active to initialize that tab's edit binding, then corrects and independently checks both addresses before Save. Further fixes cover euro monetary entry, distinct quantity/percentage formats, returning from master editors to the Order, selected-row VAT OCR, pointer hover, overlapping OCR header boxes and complete right-aligned Documents totals. Every meaningful change received targeted offline or captured-image regression tests and live continuation checks. The final full offline suite passed **103 tests in 34.39 seconds**, using `python -m pytest -q --basetemp evidence/pytest_euro_completed_final`. Offline tests are separate from the live results above.

Evidence:

- `evidence/euro_delivery_binding_test/`: correct pre-save addresses after tab switching.
- `evidence/euro_trial_e2e_c/` through `euro_trial_e2e_j/`: actual intermediate stops and fixes; no hidden success claim.
- `evidence/euro_trial_e2e_k/`: both lines and complete Order validation; only Order Save; persisted Order PASS; linked Invoice creation and only Invoice Save. Its final readback stopped on foreground loss.
- `evidence/euro_trial_verify_l/`: read-only reopened Order and Invoice fields; final list check exposed a clipped total crop.
- **`evidence/euro_trial_verify_m/execution_report.json`: final read-only linked-pair verification PASS, exit 0.**
- **`submission/evidence/euro_verified_summary.json`**: final snapshot and aggregated save counts; `euro_verified_pair.png` is the final application capture. Curated creation/reopen reports are alongside them.

To repeat verification of these saved records without edits or saves:

```powershell
python fakturama_full_workflow.py --json evidence/euro_trial_fresh_a/extracted_order.json --verify-existing --strict-assessment --expected-order-number PO000001 --workspace evidence/assessment_isolated/euro_trial_20261017/business --evidence evidence/euro_readonly_check
```

The remaining gap is fresh-run repeatability and literal PDF payment ordering, not completion or persistence of this saved pair. The earlier annotated PDF captures are historical evidence; the latest verified result is the euro capture/report above.

## Historical initial euro trial - superseded by verified continuation

**Live result: PARTIAL.** Bank Transfer was saved before Debtor creation. CUST000001 was saved once after exact contact/address-role/payment checks, then found again through the same Order selector. PO000001 remains unsaved: its warehouse street/postcode/country are correct, but the company is Northstar Office GmbH rather than Northstar Office Warehouse. The single-Order-Save guard stopped execution; no Order or Invoice Save occurred, and VAT/Product stages were not reached in this euro environment.

An initial transient Addresses-tab lookup stopped before Debtor Save. The tested fix scopes inner-tab discovery to the active Debtor, waits for a tab refresh and deduplicates wrappers with identical bounds while still rejecting distinct targets. The same Order/Debtor/address drafts were reused; this is a resumed trial, not an uninterrupted fresh end-to-end pass.

Evidence: `evidence/euro_trial_fresh_a/`, `evidence/euro_trial_resume_b/`, and final currency/save-count verification in `evidence/euro_trial_final/`. Curated reports and the delivery-stop screenshot are under `submission/evidence/euro_trial_*`. The euro profile remains open and in EUR. Older environments remain untouched.

At the project owner's request, a new isolated window uses `evidence/assessment_isolated/euro_trial_20261017/business`. It was launched with `python scripts/launch_assessment_workspace.py --name euro_trial_20261017 --euros`. All older windows, records and drafts remain preserved.

EUR/Germany (`de/DE`) was configured before startup and independently confirmed in the live Preferences UI before any Order existed: Example `-1.234,57 EUR` (euro symbol displayed in the app). Evidence: `evidence/euro_trial_setup/currency_verified.json` and its application screenshot. This environment is to remain in euros; no USD display fallback or currency conversion is part of this trial.

The fresh profile also enables SKU, Description, Picture and per-line Discount columns before the initial Order. Automatic sole-result Product insertion is disabled to keep selector confirmation explicit. The existing payment-before-Debtor sequence and strict single-Order-Save guard remain enabled.

Decimal-comma line verification now handles euro values such as `250,00 EUR` while retaining exact amounts and rejecting multiple amounts or incorrect values. The full offline suite passed **84 tests in 64.90 seconds**, using `python -m pytest -q --basetemp evidence/pytest_euro_final`. Live execution is recorded separately in `evidence/euro_trial_fresh_a/`; offline tests do not establish completion.

## Earlier payment-first test - 17 October 2026

**PARTIAL, with live master creation now verified.** In the separately authorized `payment_first_20261017` environment, the original image opened PO000001 and proved the Debtor absent through its selector. Bank Transfer was created/saved/verified **before** opening the Debtor. CUST000001 was then created with source contact fields, alias, billing and delivery roles, and Bank Transfer; contact fields and selected payment passed before its only Save.

VAT 19% was created and subsequently reopened/reused with rate 19 and code S (Standard rate). Both missing Products were created through UI actions after exact Order-selector absence checks. Each was later closed, reopened from the saved Products list and verified for exact SKU, Name, Description, VAT and gross master price: CHR-ERG-01 = 297.50; MAT-DESK-02 = 47.60. The final verification-only process exited 0.

**The Order remains unsaved, and no Invoice exists in this environment.** Delivery has Beusselstrasse 44 / 10553 Berlin / Germany, but its company line is Northstar Office GmbH instead of Northstar Office Warehouse. The project owner explicitly chose to retain the PDF's single-Order-Save requirement and declined the previously demonstrated save/reopen/correct/resave workaround. This guard remains enforced. Full source line entry, final Order totals, initial Order Save, linked Invoice and payment verification are still pending in this test.

The mat's saved-Product selector closed during a currency calculation error; its default quantity-1 row was inserted into the unsaved Order. That row is not a completed source line and must not be duplicated on recovery. The source Order number/reference and unsaved state were verified afterward.

A resumed run showed EUR formatting while the existing Order retained USD calculation state, producing Fakturama's Internal Error `Currency mismatch: EUR/USD`. This isolated profile's locale was restored through Preferences to United States (USD), consistent with the project owner's earlier authorization to ignore the symbol. Product numeric prices were unchanged. The runner now checks for Internal Error before classifying a closed selector; the new guard has offline regression coverage and the currency failure was not deliberately retriggered live.

The mat draft also survived a foreground interruption without a duplicate. New guards restore/check the owning process before text input, reject modal-blocked editors, and reuse a Product draft only when its exact SKU and entered source names match. Products-list OCR now anchors to the data area rather than the moving tab's x coordinate; the previous crop cut the SKU column when Products was the second tab.

Live evidence:

- `evidence/payment_first_fresh_a/execution_report.json`: original-image flow, payment-first creation, Debtor Save and strict delivery stop.
- `evidence/payment_first_masters_a/`: VAT/chair creation; preserved mat draft after foreground interruption.
- `evidence/payment_first_masters_b/`: same mat draft resumed and saved; captured currency error during reselect.
- **`evidence/payment_first_masters_verify_b/execution_report.json`: both persisted Products reopened and verified; original draft identity preserved; exit 0.** Earlier verification crop failure is retained in `evidence/payment_first_masters_verify/`.
- `submission/evidence/payment_first_*`: curated reports and application-only screenshots.

The targeted master test deliberately cannot save an Order or create an Invoice. Its `--verify-only` mode closes only unchanged saved Product editors, then reopens exact saved SKU rows and checks their fields. These are supplemental live tests, not an uninterrupted end-to-end pass.

## Earlier isolated test and submission

**Status: PARTIAL.** The project owner subsequently authorized creating/verifying the payment method before opening a missing Debtor. The runner now applies that order; this deliberately changes the PDF sequence to avoid the cached payment list. Existing stale Debtor drafts are preserved and still stop if the payment is unavailable. The revised sequence now passed live through payment-first Debtor creation in the later environment described above.

 The project owner authorized the original image in a separate Fakturama test workspace. The original saved records remain preserved. The strict test opened PO000001, filled its header, proved the Debtor absent through the Order selector, filled the same new Debtor (including contact fields, billing and warehouse delivery roles), and created/saved Bank Transfer while that Debtor remained open.

The test stopped before Debtor Save: Bank Transfer appears in the saved payment list, but the same open Debtor's native payment choices still offer only the old entries. The runner refuses to save with Pay Cash or replace the Debtor editor. Installed ContactEditor read-only bytecode inspection supports the cached-list diagnosis. Both unsaved drafts remain available; no Order or Invoice has been saved in this isolated workspace. Later strict VAT/Product/Order/Invoice branches remain unverified.

Strict mode also forbids the earlier delivery-company bypass and corrective second Order Save. Consequently, the successful AUTO-01 continuation below is useful execution evidence, but is not a literal fresh PDF-compliant end-to-end pass.

- `submission/design_document.pdf`: three-page code-free Part 1 design.
- `submission/annotated_execution.pdf`: two annotated application screenshots, clearly separating the successful continuation and strict stop.
- `submission/ASSESSMENT_STATUS.md`: requirement-by-requirement results.
- `submission/evidence/`: curated reports and screenshots. Full local diagnostic evidence and application data remain under ignored `evidence/`.
- `requirements-docs.txt`: optional dependencies for rebuilding the annotated evidence PDF with `scripts/build_annotated_evidence.py`.

The test business directory is `evidence/assessment_isolated/business`; its Eclipse profile is separate. `scripts/launch_assessment_workspace.py` launched the installed Fakturama with a unique database alias. It refuses to launch over an existing profile; reuse the already running test instance. Workspace-qualified receipts and exact window-title matching separate test records from the originals. No application database/API writes are used by the workflow.

After resolving the same-editor payment refresh issue, resume the existing drafts (do not use `--allow-new` again):

```powershell
python fakturama_full_workflow.py --image examples/source_order.png --execute --strict-assessment --expected-order-number PO000001 --workspace "E:/fakturama_image_to_cash_project_v1.4/fakturama_image_to_cash/evidence/assessment_isolated/business" --evidence evidence/assessment_strict_resume
```

Live reports: `evidence/assessment_strict_fresh_a/execution_report.json`, `evidence/assessment_strict_fresh_b/execution_report.json`, and `evidence/assessment_payment_return/execution_report.json`. The final targeted run reproduced the stale choices and stopped safely before Debtor Save. Offline passes do not establish live completion.

## Earlier actual results - 17 October 2026

**The original assisted transaction is preserved. A guarded Python continuation also completed the separate AUTO-01 fixture through saved Order, linked Invoice and verified full payment. The successful Python run resumed an existing draft; a single uninterrupted fresh-source run with missing masters is not claimed.**

| Record / check | Original transaction | Separate Python fixture |
|---|---|---|
| Order | PO000008, saved/reopened, open | PO000009, saved/reopened, open |
| Linked Invoice | INV000001, saved/reopened, paid | INV000002, saved/reopened, paid |
| Reference | WEB-2026-0714-A17 | WEB-2026-0714-A17-AUTO-01 |
| Order / carried Invoice Order date | 2026-07-14 | 2026-07-14 |
| Invoice / service dates | 2026-10-17, unchanged | 2026-10-17, unchanged |
| Billing | Northstar Office GmbH / Marta Klein / Friedrichstrasse 88 / 10117 Berlin / Germany | Same, verified after reopening |
| Delivery | Northstar Office Warehouse / Marta Klein / Beusselstrasse 44 / 10553 Berlin / Germany | Same, verified after reopening |
| Chair | CHR-ERG-01, qty 2, unit net 250.00, VAT 19%, discount 10%, line net 450.00 | Same, every cell verified |
| Mat | MAT-DESK-02, qty 3, unit net 40.00, VAT 19%, discount 0%, line net 120.00 | Same, every cell verified |
| Net / VAT / total | 570.00 / 108.30 / 678.30 | 570.00 / 108.30 / 678.30 |
| Overall discount / shipping | Zero / free shipping, zero | Same |
| Persisted payment | Bank Transfer, paid 2026-07-18, full value 678.30 | Same, verified after closing/reopening INV000002 |

The original Order/Invoice were not edited during the fixture runs. One test Order and one test Invoice were created across attempts a-g; all retries reused PO000009. No Delivery, Correction or Dunning document was created. Source currency is EUR; the project owner explicitly authorized ignoring Fakturama's dollar symbol. Numeric amounts were compared without conversion.

Existing CUST000002 and Bank Transfer were reused. The original assisted execution created the missing VAT 19% and both Products (master gross prices 297.50 and 47.60). The Python fixture reused these saved masters and did not create duplicates.

## Evidence and live execution

- `evidence/continuation_20261017/live_execution_report.json`: original assisted execution and its interventions. Final screenshots include `final_paid_invoice_reopened.png`, `final_invoice_billing_rows_payment.png`, `final_invoice_delivery_rows_payment.png` and `saved_order_delivery_verified.png`, with matching UIA text.
- `evidence/python_verify_original_20261017_l/`: Python reopened PO000008 and passed both addresses, reference/date, both complete rows, totals, zero discount/shipping. Its final Invoice linkage reader stopped on selected-row styling; that failure was preserved and fixed.
- `evidence/python_invoice_verify_final_20261017/`: final Python verification-only PASS for reopened INV000001, every payment/address/row field, and exact linked PO000008/open + INV000001/paid pair, both 678.30. No record edits occurred.
- `evidence/python_autotest_20261017_a/` through `_f/`: intermediate fixture stops, retained honestly. Attempt a created PO000009 and selected the existing customer/chair. Attempt e added the saved mat but stopped on clipboard restoration. The same draft was preserved through all fixes.
- **`evidence/python_autotest_20261017_g/execution_report.json`: successful Python continuation. Every executed stage passed; process exit 0 at 14:07:49 Cairo time.** It edited/verified both product rows, saved PO000009, reopened/repaired delivery company, saved the same Order again, reopened and verified all fields, created INV000002 through the Order follow-up button, entered payment, saved/reopened the Invoice, and proved the exact linked pair in This transaction. No manual desktop intervention occurred during attempt g.
- `evidence/python_autotest_20261017_g/20261017_140749_PASS_10_invoice_paid_and_verified.png`: final successful Invoice verification screenshot. `20261017_140748_Documents_This_transaction.png` shows the exact open/paid pair.
- `evidence/receipts/`: source-specific recovery identity hints. Both completed sources have phase `complete`; receipts never replace live persisted-record checks.
- `evidence/atomic_search_live_20261017/`: clipboard-free native product search verified with a nonexistent SKU; zero rows, selector cancelled, no item added.

Historical `evidence/full_run/` stops remain preserved; they are not the current completion status. A further repeat of the completed AUTO-01 fixture was deliberately not run.

## Fixes and application behavior

1. The old address reader excluded the real roughly 108-pixel multiline Edit because its maximum height was 95 pixels. It now grounds the Edit between Addresses and Items, rejects short/stale controls, polls after tab changes, and checks company, street, postcode, city and country. UIA value readback is preferred, with a read-only OCR fallback.
2. Saved CUST000002 has the expected billing and secondary warehouse address/roles. New Orders initially inherited the warehouse street under the billing company name. The Debtor was not changed. Its main E-Mail field was blank when inspected; source marta.klein@example.test remains a master-data omission.
3. Fakturama 2.2.0 has a secondary-address save defect: saving from Delivery raised DocumentEditor.doSave:412, NullPointerException. Read-only installed-class bytecode showed an item-count versus zero-based tab-index binding problem. Evidence: `evidence/continuation_20261017/DocumentEditor_bytecode.txt`, `order_save_error.png`, `order_save_error_uia.txt`, `order_save_error.log`.
4. The demonstrated workaround now passed in Python: save from verified Invoice address, close/reopen the same saved Order, correct delivery company with native multiline typing, explicitly focus Cust.Ref. (TAB inserts a literal tab), save that existing record, then close/reopen and verify persistence. This requires a corrective update, not a literal single Save click; only one Order record exists per source.
5. VAT accessibility SetValue initially displayed 19% but persisted 0%. The original assisted run corrected that same VAT with native numeric input/focus commit and verified 19.00% plus S (Standard rate). No duplicate VAT was created. Product VAT choices are `VAT 19%`, without the suffix shown in the grid. Product master gross prices exclude line discount.
6. Saved-document recovery discovers records before creation, verifies source identity/reference/date, reopens records and checks all persisted totals/addresses/line cells/payment fields. PO000008 and INV000001 are protected completed records. Source-specific receipts prevent silent replacement when a known record/draft is missing. Untracked Invoice drafts are rejected.
7. Documents OCR removes gray rules, handles white text on blue/gray selection backgrounds, and detects number-column ink independently of body OCR. It stops on unreadable/conflicting values or an obscured app. This workspace's six-digit PO/INV numbering is required. Table row opening uses the actual Document column. Known customer-history Information popups are dismissed only when their company and message match.
8. ProductListTable can automatically add the sole filtered product and close the popup when DOCUMENT_IMMEDIATELY_OVERTAKE_ITEMNUMBER_FROM_PRODUCTS_DIALOG is enabled. Installed bytecode proof: `evidence/reader_diagnostic/ProductListTable_bytecode.txt`. Search now sets the full SKU in one native operation, then requires exactly one expected newly added row if the popup closes. Missing, wrong or duplicate rows stop execution.
9. Owned SWT selectors can be absent from top-level UIA enumeration; native HWND discovery now handles recovery. Popup shadows are excluded from result rows. Normal/selected grid OCR is combined, bounded header-only Qty/Oty and Item/Itern glyph confusions are recognized, and only outer vertical rules are stripped from SKU readback. Exact SKU and monetary checks remain enforced.
10. Clipboard/OLE restoration failed in attempt e and in an isolated initialization probe. Clipboard use was removed from current address read/write and product-search paths. Native Search was independently live tested; native delivery typing and persisted readback passed in g.

## Offline tests versus live results

- Original baseline: 19 passed. Earlier address/preservation suite: 24 passed. An initial sandbox run had 5 temporary-directory setup errors; no application records were involved.
- **Latest full offline suite: 84 passed in 64.90 seconds**, using `python -m pytest -q --basetemp evidence/pytest_euro_final`. This includes payment creation ordering, exact address roles, workspace isolation, protected saves, foreground/modal guards, Product draft identity, application-error detection, and tab-independent list cropping.
- Tests cover Decimal/source arithmetic, dates, address grounding/preservation, receipt/source mismatch, duplicate records, strict persisted snapshots, every line cell, recorded Documents/grid OCR failures, missing body OCR tokens, selected rows, popup-shadow noise and owned-selector recovery. These tests do not verify the live SWT application.
- Both JSON preflights passed without desktop actions: 570.00 / 108.30 / 678.30.
- Live original-pair verification and successful AUTO-01 continuation are separate evidence, described above. The earlier fixture reused saved masters. Missing master creation was later tested separately in the payment-first environment; an uninterrupted fresh end-to-end run remains unverified.

## Fresh payment-first environment

At the project owner's request, a separate test environment was launched with `python scripts/launch_assessment_workspace.py --name payment_first_20261017`. It uses `evidence/assessment_isolated/payment_first_20261017/business`, its own Eclipse profile and unique HSQL alias. Both older environments and their records/drafts remain untouched.

The original-image run is logged in `evidence/payment_first_fresh_a/`. Bank Transfer creation and saved-list verification passed before the new Debtor editor opened. This is the agreed ordering change; other strict address/save guards remain enabled. See the execution report for subsequent results.
