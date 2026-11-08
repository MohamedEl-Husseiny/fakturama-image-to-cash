# Incremental architecture work

## Current boundaries

| Responsibility | Module | Desktop access |
|---|---|---|
| Source data, dates, Decimal arithmetic, source validation | `fakturama/models.py` | None |
| Source-image extraction | `fakturama/ocr.py` | None |
| Numeric field parsing and destination-format entry | `fakturama/fields.py` | None |
| Header/grid grounding and independent cell verification | `fakturama/order_ui.py`, `fakturama/grid.py` | OCR readers accept images; workflow captures live regions |
| Window/control operations and owner focus | `fakturama/ui.py` | UI Automation/native desktop input |
| Recovery identity and persisted-data assertions | `fakturama/execution.py` | None |
| Durable per-attempt event journal | `fakturama/evidence.py` | None |
| Exclusive process-owned desktop driver lease | `fakturama/lease.py` | OS file locking only; no window connection |
| Runner lifecycle, `stage()` guard, `run()` sequence, CLI | `fakturama_full_workflow.py` | Composes the stage mixins; ~500 lines |
| Per-stage UI orchestration (order header, debtor, payment, product, invoice, documents, selectors, controls) | `fakturama/stages/*.py` | UI Automation / native input / OCR through `fakturama/ui.py` and `grid.py`; shared helpers in `stages/common.py` |
| New isolated EUR environment and execution | `scripts/run_fresh_assessment.py` | Launches the existing desktop application and workflow |

## Completed first refactor

Numeric codecs are shared between the workflow and grid verifier. Parsing preserves quantity precision; monetary callers explicitly apply the domain's cent rounding. Multiple numeric runs, unreadable values and ambiguous sign placement fail closed. Entry formatting still follows the observed destination field, including Fakturama's mixed money versus quantity/percentage formats. Compatibility functions remain available from the main script.

ExecutionJournal owns event construction, JSON serialization and atomic report replacement. The event schema remains compatible with existing reports. Failed replacement leaves the last persisted report and in-memory event list intact. Existing execution reports cannot be overwritten: use a new evidence directory for each attempt or read-only verification. Recovery receipts remain a separate identity hint and retain their existing behavior.

This is a first extraction, not a completed architectural rewrite. It does not add save retries, change the single-save rules, change payment ordering, reset workspaces or alter saved records.

## Recovery and driver ownership increment

Runner acquires an OS-backed DesktopLease before initializing UI Automation. Its main CLI uses a context manager and releases the lease on success or exception; supplemental short-lived scripts retain process-exit cleanup. Explicit `close()` is available to embedding callers. A process crash releases OS ownership, so a leftover lock file is never interpreted as a live owner and is not deleted to bypass another process. Constructor failure releases ownership immediately.

The lease is shared by this project's Runner-based workflows across all workspaces, including read-only verification: physical mouse/keyboard input is shared. This is stricter than a source/workspace-only lock and prevents two cooperating workflows from fighting for input. It does not lock out a human or unrelated automation programs; foreground/identity guards remain necessary.

RecoveryPhase defines the existing persisted phases plus Order/Invoice Save-attempt phases, preserving legacy string receipts. Receipt updates validate phase and document identity, prevent identity reassignment, and update in-memory state only after atomic disk replacement. These are explicit phase and invariant checks, not a complete formal state machine: read-only verification can revisit an earlier verification phase.

Immediately before Order/Invoice Save input, the receipt records a durable Save-attempt marker. Failure to persist that marker prevents the input. On a later run, UI document discovery must find the exact attempted number. If it cannot, the outcome is unresolved and the workflow stops before replacement creation or another Save. Finding the number permits independent reopen/field verification; the receipt itself is never proof of persistence. No automatic Save retry or marker-clearing override is provided. Master-record interruption handling still uses existing UI lookup/draft guards.

## Next extractions, in order

1. Move address/contact, Product/grid and document/payment operations into focused adapters with small observed-data return types. Keep the top-level sequence easy to inspect. Introduce interfaces at those actual boundaries rather than a generic automation framework.
2. Further separate workflow transition decisions from desktop actions and consolidate state transitions. The document Save-attempt policy now exists; extend its tests and consider master-record boundaries without introducing blind Save retries.
3. Distinguish transient discovery/focus failures, ambiguous targets, source/data mismatches, application faults and evidence-write failures. Bound retries to safe discovery/read operations; stop on identity conflicts and unknown save outcomes.
4. Split fast pure tests, recorded-image regressions and opt-in live scenarios in isolated workspaces. Keep live pass/fail reports separate from offline test counts.

Existing fresh execution evidence is the behavioral baseline. After the first extraction, a read-only live verification checks the existing completed pair without another Save. A new write-flow verification is a separate future run, not implied by offline or read-only checks.

## Current limits that should remain explicit

The live baseline is one original EUR image with two distinct Products, VAT 19%, distinct billing/warehouse delivery and full payment. Other currencies are rejected by source validation. Shipping and overall discount are fixed to zero by the workflow. Partial payments, three-decimal quantity entry, repeated SKU lines, long scrolling item lists, alternative locale group separators and general image layouts are not established end-to-end capabilities. See `VERIFICATION_MATRIX.md` before expanding scope.
