# Task 2 — CLOSED — CONDITIONAL ACCEPTANCE

Closure authorized by the user on 2026-10-12. This is the hierarchy implementation/integration milestone, not full application production readiness.

## Accepted hierarchy

Domain → Module → Knowledge → Document Type → Version → Source Type → Source Document → Source Files.

Document Type owns Version. Stable document-version identity is used by knowledge, test cases, workbooks, automation ownership, storage and retrieval filters.

## Hierarchy acceptance evidence

| Criterion | Result |
|---|---|
| Canonical database identities, foreign keys and independent document-type versions | PASS |
| Transactional administration, dependency checks and failure rollback | PASS |
| Managed/legacy filesystem resolution and consistent owner paths | PASS |
| Vector ownership and exact vector content/embedding preservation | PASS — 297 original vectors |
| Test Case and canonical workbook ownership | PASS — 75 Test Cases, six workbooks |
| Manual Upload and reviewed Smart Upload workflows | PASS — real integrated-source Chromium/API tests |
| Manage Knowledge hierarchy and safe source administration | PASS |
| QA Engineering and independent revision imports | PASS — V1/V2 reuse of logical TC IDs |
| QA Automation, Reports and Assistant hierarchy cascades | PASS |
| PSW Core and WeBOC safe-data hierarchy/TC workflows | PASS |
| Desktop backend/offscreen hierarchy controls | PASS — interactive use separately deferred |
| Adjacent calendar/recorder/runner/API/SQL/import/publishing fixtures | PASS — 14 regression scripts |
| Hierarchy unit/integration/migration fixtures | PASS — 35 tests in the integrated-source verification environment |

Evidence is retained locally in ignored `AI-Web/Output/task2-integration/20261012-014720-PKT/`, `AI-Web/Output/task2-acceptance-20261012/`, and the isolated verification worktree. These locations contain runtime/test evidence and must not be committed.

No integration/migration is repeated by closure. Existing user data, integration settings, recordings, managed files, vectors and recovery backups are preserved.

## Checkpoint scope

The checkpoint contains Task 2 hierarchy source deltas and portable required regression tests only. Pre-existing concurrent changes remain in the working tree rather than being swept into this commit. Verification-only runtime data, secrets, generated logs, backups and isolated worktrees are excluded.

GLOBAL's separately verified Wikipedia fallback remains functional in the active working tree. It is an encyclopedia-only fallback, not comprehensive web search; it does not change the accepted hierarchy or LOCAL/RAG scope rules.

See [deferred acceptance](deferred-acceptance.md) for the explicit closure conditions. No full Desktop parity, remote cloud capability, live external publishing or production-readiness claim is made.
