# Broader verification cases

Offline tests, recorded-image regressions and live Fakturama runs provide different evidence. A passing pure parser test does not establish that a desktop widget accepts that input. Proposed live cases below are pending unless specifically marked verified. Each future destructive/interruption experiment belongs in an isolated test workspace, preserving current completed records.

> Updated 8 Nov 2026: the latest uninterrupted fresh pass is `seq5` (PDF-faithful sequence, exit 0; `submission/evidence/sequence_faithful_*`). Rows below that cite `fresh_python_v3_20261017` / `safety_e2e_20261017` are also satisfied by `seq5`. Everything in the "Next verification" column is still open.

## Priority 1: identity and save safety

| Case | Current evidence | Next verification and acceptance |
|---|---|---|
| Fresh original image, all required masters missing, EUR, full payment | Uninterrupted Python-only live PASS in `fresh_python_v3_20261017`; latest safety code completed resumed `safety_e2e_20261017` with exactly one Save per document | Repeat with a separate source fixture; one source Order and linked Invoice, one Save per document, exact persisted fields |
| All masters already present | Earlier live continuations reused masters; identity/duplicate guards have offline tests | A complete independent live reuse scenario; zero new master Saves and correct selection from the Order |
| Mixed master availability | Not a complete live matrix | Exercise missing Payment only, existing Debtor with incompatible/missing required Payment, one missing Product, and missing VAT; never silently retain the wrong method or create duplicates |
| Duplicate customer/SKU/reference matches | Ambiguity and duplicate-document guards have offline coverage | Seed ambiguous records in an isolated fixture; stop before selecting/saving and retain drafts |
| Repeat same source after success | Source/workspace receipts and discovery tested; new launcher refuses existing workspaces | Main recovery runner discovers and verifies the existing pair; no new records/Saves |
| Interrupt just before/after Order or Invoice Save | Typed attempt markers now persist before Save input; offline tests verify marker ordering for both document types, block Save input on marker-write failure, and verify reload, exact-number resolution and stop-before-replacement. No controlled live termination matrix | In an explicitly isolated write-flow fixture, test actual boundaries; discover/reopen first, verify a single pair and never blindly reissue Save |
| Evidence write interrupted/disk unavailable | Atomic journal failure tests now preserve the last report | Isolated integration test around a Save; missing evidence must never be treated as proof that the Save failed |
| Two workers targeting the same workspace/source | Shared Runner desktop-driver OS lease implemented; actual child-process contention/crash-release tests pass; competing live CLI invocation refused before desktop initialization | Keep concurrency rejected across workspaces too, because input is shared. Unrelated external automation and human focus changes still require separate guards |
| Wrong-workspace or corrupted receipt | Workspace/source identity guards and atomic receipt writes exist | Verify rejection before record edits; neither a receipt nor an event log is proof that a record persisted |

## Priority 2: different business data

| Case | Current evidence | Next verification and acceptance |
|---|---|---|
| Billing equals delivery, or different company/contact/address | Original distinct warehouse address verified live | Same-address and alternate-contact fixtures; no cross-tab corruption before or after Save |
| One item, several items, long item list requiring scrolling | Two-item source verified live | Exact row counts/order and independent verification across scrolling; hidden rows cannot be mistaken for absent rows |
| Fractional quantities and rounding boundaries | Decimal arithmetic and parser precision tested offline | Widget entry with 0.5 and rounding-sensitive prices; three-decimal quantities need an explicit supported precision policy before claiming support |
| Discount 0%, 100%; zero-priced line; mixed VAT rates | Source/domain arithmetic guards; live baseline 10% and 0%, both VAT 19% | Verify master gross versus discounted net, VAT rounding and document totals independently |
| Unpaid Invoice | Source model and workflow contain a branch; no broad live proof | Persist unpaid state without inventing payment date/value |
| Partial payment | Not implemented as a supported source/payment model | Define the amount/open-balance model before desktop automation or tests |
| Nonzero shipping, overall discount, repeated SKU lines | Zero shipping/overall discount enforced; repeated SKU source rejected | Explicitly scope/extend the model and grid identity before live use; current code must not be presented as supporting these cases |

## Priority 3: desktop and extraction conditions

| Case | Current evidence | Next verification and acceptance |
|---|---|---|
| German EUR money with dot-decimal quantity/discount | Fresh live PASS; captured selected-row/header and field-codec tests | Preserve exact values across each destination widget; no currency conversion |
| Other grouping/sign styles, accounting parentheses, nonbreaking-space thousands | Some signed and grouped parser cases covered; ambiguous formats rejected | Explicit format policy and fixtures; unsupported formats stop rather than guess. Non-EUR source currency currently remains rejected |
| Focus loss, window grouping preview, multiple Fakturama instances | Startup/owner-focus fixes and exact workspace matching; latest grid/cell captures check exact foreground before/after and scrolling restores owner. Resumed live pass after captured focus-loss stop; all failures retained | Controlled focus changes at safe checkpoints; restore the exact owner or stop before input, never edit a different workspace |
| Smaller windows, display scaling, moved/resized columns, more tabs | Some recorded layout and grounding regressions; broad live matrix pending | Test actual layouts/scales; uniquely ground headers/fields and refuse clipped or ambiguous controls |
| Slow UI, delayed tab/popup rendering, unexpected modal/application error | Existing refresh/focus/error guards; specific historical live failures | Bounded read retries only; recognize modal/application faults without treating a missing popup as successful insertion |
| Different image layouts, blur/skew, OCR confusion, Unicode customer/address text | Original image and selected recorded extraction cases covered | Independent images with known truth; reject missing/ambiguous source fields and arithmetic mismatch before desktop writes |

## Success criteria for each future live case

Record the exact source fixture/hash, workspace, code version, expected records and supported behavior before execution. Verify creation/reuse decisions, save counts and record identities. Reopen persisted documents and independently check addresses, lines, totals and payment/open state. Record all stops and intervention honestly. Keep each attempt's evidence separate; offline results do not replace live proof.
