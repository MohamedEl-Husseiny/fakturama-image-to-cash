# Assessment status - 8 November 2026

**Result.** The complete Order-first flow runs uninterrupted from the supplied image to a saved Order and a saved, paid, linked Invoice in a freshly created, empty Fakturama 2.2 EUR workspace, with exactly one Save per record (Payment 1 / Debtor 1 / VAT 1 / Product 2 / Order 1 / Invoice 1). Latest pass: `seq5`, 8 Nov 2026, exit code 0; evidence in `evidence/sequence_faithful_*` next to this file and five annotated frames in `annotated_execution.pdf`. Earlier uninterrupted passes: 7 Nov (`post_refactor_b`, `post_split_a`, `bon2` - the last with a screen-share banner covering the Documents table) and 17 Oct 2026.

**Sequence.** The task PDF's steps 1.3-5.5 are followed as written, with two documented deviations (README section 3): the payment method is created before the Debtor editor opens, because the open ContactEditor caches its payment list; and, only when Fakturama 2.2.0 shows the billing company on the Order's Delivery tab after selection, the same Debtor is re-selected once with that tab active. Nothing is reopened after a Save on the fresh path; persistence is confirmed from the Data > Documents rows as 4.5 and 5.5 prescribe. Re-runs, recovery and `--verify-existing` keep the stronger reopen-and-reverify path.

**Safety.** Every stage is fail-closed; each live stop recorded in `docs/CHANGELOG.md` was a guard refusing to proceed before a wrong Save (a date widget landing on the wrong segment, a Documents node not unique, keystrokes captured by another window). A durable save-attempt marker precedes every Save; on restart the exact saved number must be rediscovered or the run stops. An OS-level desktop lease prevents a second runner.

**Reading the application.** UI Automation for every field, tab, button and combo; OCR only for Fakturama's custom tables (items grid, selector dialogs, Documents list) and taken from the window's own rendered content, so overlays from other applications are not in the image.

**Not done / known limits.** Extractor tuned to this document family; live coverage is one source image and one window layout (`docs/VERIFICATION_MATRIX.md`); a few relative pixel offsets remain inside discovered controls; Save-interruption recovery is proven offline only; run time is 9-10 minutes on an idle machine. Details and the 3-more-hours answer: README sections 5 and 6.

**Offline tests.** `python -m pytest -q`: 183 passed on Windows (170 on any OS with Tesseract).
