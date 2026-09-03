# QA AI Studio --- Agent Instructions

## 1. Purpose of This File

This file contains the permanent project-level instructions for AI
coding agents working on **QA AI Studio**, including Roo Code, Codex,
and future coding agents.

Read this file before analyzing, modifying, generating, refactoring, or
testing project code.

These are persistent architecture and engineering rules. A task-specific
prompt may add requirements, but it must not silently override the core
product boundaries in this file.

------------------------------------------------------------------------

## 2. Product Vision

**QA AI Studio** is an enterprise QA intelligence platform intended to
help QA teams understand systems, manage knowledge, derive QA artifacts,
automate testing, execute tests, analyze results, and provide
AI-assisted QA intelligence.

The product should behave like an experienced enterprise QA/SQA engineer
that understands:

-   applications and system functionality;
-   business domains and modules;
-   requirements and change requests;
-   workflows and validations;
-   UI controls and stable locators;
-   APIs and integrations;
-   test scenarios and test cases;
-   automation assets and execution results;
-   QA evidence, traceability, coverage, risk, and reporting.

The application is not a collection of disconnected demos. Features must
use real application data, real persistence, real backend services, and
real workflows wherever the architecture supports them.

------------------------------------------------------------------------

## 3. Repository and Architecture

The repository contains an established application. Inspect the actual
repository before making assumptions.

Important application areas include:

``` text
QA AI Agent/
├── AGENTS.md
├── AI/                       # Desktop application / established functional reference
│   ├── App/
│   ├── Core/
│   ├── Database/
│   ├── Repository/
│   └── ...
│
├── AI-Web/                   # Web application
│   ├── Core/
│   ├── Database/
│   ├── Frontend/
│   ├── Repository/
│   ├── Web/
│   └── ...
│
└── ...
```

### Desktop and Web Relationship

The existing **Desktop application is the primary functional reference**
for functionality that is being migrated or reproduced in the Web
application.

For Desktop-to-Web work:

1.  Inspect the relevant Desktop implementation first.
2.  Identify its actual controls, actions, workflows, validations,
    services, persistence, dialogs, and edge cases.
3.  Inspect the current Web implementation.
4.  Reuse existing Core/backend functionality wherever practical.
5.  Implement Web-equivalent behavior appropriate for a browser
    application.
6.  Do not mechanically copy PySide UI code into Web code.
7.  Preserve valid Web functionality that already exists.
8.  Do not claim Desktop parity until the required workflow has been
    verified.

Desktop is a **functional baseline**, not a requirement to reproduce
every visual pixel or desktop-specific technical mechanism.

------------------------------------------------------------------------

## 4. Core Product Modules and Boundaries

Keep responsibilities separated. Do not move functionality into the
wrong module merely because it is convenient.

### 4.1 Knowledge Hub

Knowledge Hub is responsible for:

``` text
Discover
→ Analyze
→ Classify
→ Structure
→ Review
→ Store
→ Search
```

Its purpose is to build trusted knowledge about applications, systems,
requirements, business processes, integrations, documents, APIs, and
related sources.

Knowledge Hub may capture and store information such as:

-   Domain
-   Module
-   Knowledge Name
-   Version
-   Document Type
-   Source Type
-   Source Location
-   Summary
-   Tags
-   confidence/classification information
-   hierarchy
-   documents and source artifacts
-   pages/screens
-   workflows
-   fields
-   controls
-   validations
-   stable locators
-   API/system information
-   discovered application knowledge

#### Knowledge Hub MUST NOT

Knowledge Hub must not:

-   generate test cases as part of knowledge ingestion;
-   generate test scenarios as part of knowledge ingestion;
-   generate automation scripts as part of knowledge ingestion;
-   execute QA tests;
-   mix Test Case Studio or QA Automation responsibilities into the
    Knowledge Hub.

Knowledge captured here becomes an input for downstream QA capabilities.

------------------------------------------------------------------------

### 4.2 Website / URL Knowledge Discovery

URL ingestion is a **Knowledge Hub discovery capability**, not a test
execution feature.

The intended long-term workflow is approximately:

``` text
URL / Application
→ Access
→ Authentication when required
→ Browser discovery
→ User-guided or controlled navigation
→ Capture screens/pages
→ Discover hierarchy
→ Discover modules/workflows
→ Discover fields/controls
→ Discover validations
→ Capture stable locators where appropriate
→ Review
→ Store structured system knowledge
```

Playwright may be used for browser discovery where the architecture
requires it.

Do not confuse URL knowledge discovery with Playwright automated test
execution.

Never fake website discovery by storing only the supplied URL when the
requirement is to analyze the accessible application.

Authentication credentials must be handled securely and must not be
logged, committed, or unnecessarily persisted.

------------------------------------------------------------------------

### 4.3 Test Case Studio / QA Engineering

This area owns QA artifact creation and management, including as
applicable:

``` text
Requirement
→ Traceability / RTM
→ Test Scenario
→ Test Case
→ Review
→ Version / Status
```

It may use trusted Knowledge Hub content and uploaded requirement
documents as context.

Test cases should remain traceable to their originating requirements and
business knowledge wherever the feature supports traceability.

Do not put these responsibilities into Knowledge Hub.

------------------------------------------------------------------------

### 4.4 QA Automation

QA Automation owns automation-related functionality such as:

-   UI automation;
-   API automation;
-   automation scripts;
-   Playwright test scripts;
-   API collections;
-   Git automation where supported;
-   execution;
-   background jobs;
-   execution status;
-   evidence;
-   logs;
-   results;
-   pass/fail information.

Knowledge discovery and automation execution are separate concerns even
if both use Playwright.

Do not make Knowledge Hub execute QA automation.

------------------------------------------------------------------------

### 4.5 AI Assistant / RAG

The AI Assistant should answer using the application's trusted knowledge
and QA data.

Where applicable, use the established RAG architecture rather than
bypassing it.

Existing project capabilities may include:

-   document chunking;
-   embeddings;
-   vector storage;
-   retrieval;
-   reranking;
-   context building;
-   prompt building;
-   response validation;
-   memory/orchestration.

Prefer reuse and integration over duplicate AI pipelines.

The assistant must not pretend retrieved information exists when it was
not actually retrieved.

------------------------------------------------------------------------

### 4.6 Dashboard and Reporting

Dashboard/reporting should reflect actual stored or executed data.

Typical concerns include:

-   coverage;
-   execution;
-   pass/fail;
-   risk;
-   recent activity;
-   knowledge statistics;
-   management reporting.

Never populate production dashboard functionality with fabricated
statistics merely to make the screen look complete.

------------------------------------------------------------------------

## 5. Knowledge Hierarchy

Where applicable, Knowledge Hub should preserve a meaningful hierarchy
such as:

``` text
Domain
└── Module
    └── Knowledge Name
        └── Version
            └── Document Type / Source
```

Hierarchy must come from real application/database data.

Do not hardcode business domains or modules in frontend code when they
are intended to be managed dynamically.

After successful create/upload/edit/delete operations, refresh the
affected hierarchy or list so the UI reflects persisted state.

------------------------------------------------------------------------

## 6. Knowledge Metadata Rules

Use the project's established metadata schema. Common Knowledge Hub
metadata includes:

-   Domain
-   Module
-   Knowledge Name
-   Version
-   Document Type
-   Source Type
-   Source Location
-   Summary
-   Tags
-   Confidence Score / classification confidence where applicable

Do not reintroduce deprecated metadata concepts merely because older
code contains them.

When Desktop and Web metadata differ, inspect the current
backend/database model and current product requirement before changing
schema.

Avoid schema migrations unless they are genuinely necessary.

------------------------------------------------------------------------

## 7. Dynamic Domain and Module Rules

Domain and Module are managed application data.

### Domain

-   Load existing Domains from the real backend/database.
-   Do not hardcode dropdown values.
-   Support creation where the relevant workflow requires it.
-   Prevent invalid empty values.
-   Handle duplicates correctly.

### Module

-   Modules are dependent on Domain where the data model requires this
    relationship.
-   Refresh Module values when Domain changes.
-   Support creation under the selected Domain where required.
-   Do not hardcode Module values.

A Web implementation should use proper controls and application modals
rather than browser `prompt()` for primary enterprise workflows.

------------------------------------------------------------------------

## 8. Upload and Source Handling

Knowledge upload may support multiple source types depending on the
implemented backend.

Examples found in the product direction/Desktop implementation include:

-   Files
-   Folder
-   Images
-   URL
-   API Collection
-   SQL Script
-   Database Metadata
-   Git Repository
-   Release Notes
-   Test Cases
-   SOP Documents

Rules:

1.  Do not claim a source type works unless its real processing path
    works.
2.  If a source type is not implemented, clearly disable or identify it
    as unavailable.
3.  Do not fake successful ingestion.
4.  Preserve selected-source state correctly.
5.  Support actual removal/clear actions where the UI exposes them.
6.  Show actual processing results.
7.  Persist through the established upload/indexing pipeline.
8.  Do not silently replace existing selected sources when the workflow
    supports multiple sources.

------------------------------------------------------------------------

## 9. AI Smart Upload Rules

AI Smart Upload is an analysis-and-review workflow.

Preferred behavior:

``` text
Select Source(s)
→ Analyze
→ Suggest Metadata
→ Show AI Analysis
→ User Reviews
→ User Accepts/Edits
→ Confirm Upload
→ Persist/Index
→ Refresh Knowledge
```

AI suggestions may include:

-   Domain
-   Module
-   Knowledge Name
-   Version
-   Document Type
-   Summary
-   Tags
-   Confidence

Rules:

-   AI analysis does not equal successful upload.
-   Never automatically persist merely because analysis completed unless
    the explicit product workflow requires it.
-   Allow user review/edit before confirmation.
-   Preserve user overrides.
-   Show real errors for failed analysis.
-   Do not fabricate confidence or analysis results.
-   For multiple sources, do not discard successful results solely
    because another source fails.

------------------------------------------------------------------------

## 10. Reuse Before Rewrite

Before creating new code, search for existing implementations.

Prefer this order:

``` text
Reuse existing service
→ Extend existing service
→ Add a small compatible abstraction
→ Create new implementation only when necessary
```

Before adding an endpoint:

-   inspect existing routers;
-   inspect existing repository/service methods;
-   inspect the Desktop/Core implementation;
-   confirm an equivalent endpoint does not already exist.

Before adding an AI pipeline:

-   inspect existing analyzer/upload/RAG/vector components.

Before adding database structures:

-   inspect current schema and metadata managers.

Do not duplicate working logic merely to make a task easier.

------------------------------------------------------------------------

## 11. Backend and API Rules

-   Preserve existing API contracts unless a change is required.
-   Avoid unnecessary endpoints.
-   Validate request data.
-   Return truthful HTTP status codes and error messages.
-   Keep routing, business logic, repository/data access, and UI
    concerns appropriately separated.
-   Do not put substantial database logic directly into frontend
    JavaScript.
-   Do not bypass established services without a clear reason.
-   Maintain backward compatibility where practical.

When adding a new API, document why existing APIs could not satisfy the
requirement.

------------------------------------------------------------------------

## 12. Authentication, Authorization, and Security

Existing authentication must remain functional.

Do not break:

-   login;
-   logout;
-   Bearer-token authentication;
-   session/token validation;
-   role/permission checks;
-   user administration.

Never commit:

-   passwords;
-   API keys;
-   authentication tokens;
-   secret keys;
-   private credentials.

Do not log credentials.

Do not expose secrets in frontend source.

Treat application credentials used for URL discovery as sensitive.

Do not weaken authentication or authorization merely to make development
easier.

------------------------------------------------------------------------

## 13. Database, Vector Store, and Runtime Files

Runtime/generated data should not normally be committed to Git.

Examples include:

``` text
AI-Web/Database/metadata.db
AI-Web/Database/chroma_db/
AI-Web/Database/web_secret.key
AI-Web/Models/
downloaded Hugging Face model caches
__pycache__/
virtual environments
```

Respect `.gitignore`.

Do not delete or reset user data during normal feature development.

When modifying persistence:

1.  understand the existing database;
2.  preserve existing records;
3.  avoid destructive migrations;
4.  provide migration logic when a schema change is unavoidable;
5.  verify persistence after application restart/reload.

Vector storage and metadata storage must remain consistent where the
upload pipeline uses both.

------------------------------------------------------------------------

## 14. Frontend / UX Rules

QA AI Studio should feel like an enterprise QA application.

Prefer:

-   real dropdowns for controlled values;
-   dependent controls where data is relational;
-   proper modals;
-   clear validation;
-   loading states;
-   disabled states during processing;
-   progress indicators tied to real work;
-   confirmation dialogs for destructive actions;
-   success/error feedback;
-   useful empty states;
-   responsive layouts where practical.

Do not create visible buttons that do nothing.

Do not use fake UI data to imply functionality.

Avoid browser `alert()` and `prompt()` for primary workflows when an
application modal is appropriate.

Preserve the established QA AI Studio visual language unless the task
explicitly requests redesign.

Functional correctness is more important than cosmetic changes.

------------------------------------------------------------------------

## 15. Desktop-to-Web Parity Rules

For any task described as "Desktop parity", use this process:

### Step 1 --- Inspect Desktop

Identify:

-   fields;
-   dropdown values;
-   buttons;
-   dialogs;
-   tables;
-   workflows;
-   workers;
-   services;
-   validations;
-   success/error behavior;
-   persistence;
-   special cases.

### Step 2 --- Inspect Web

Identify:

-   already working functionality;
-   missing functionality;
-   partial functionality;
-   backend support already available;
-   browser-specific limitations.

### Step 3 --- Create a Gap List

Classify each feature:

``` text
WORKING
PARTIAL
MISSING
NOT APPLICABLE TO WEB
DEFERRED WITH REASON
```

### Step 4 --- Implement Minimum Necessary Changes

Do not rewrite unrelated code.

### Step 5 --- Verify

Do not declare parity based only on code presence.

Verify actual behavior.

------------------------------------------------------------------------

## 16. No Fake Completion

This is a critical project rule.

An agent must never report:

-   "fully working";
-   "fully integrated";
-   "100% parity";
-   "verified";
-   "end-to-end tested";

unless the claimed behavior was actually verified to the extent stated.

If browser execution was not performed, say:

`Code implemented; browser verification remains required.`

If an API was not called, do not claim the API passed.

If a feature is visually present but its backend path is incomplete,
classify it as partial.

If provider/tool limits prevent verification, report the limitation.

Truthful incomplete status is preferable to a false completion report.

------------------------------------------------------------------------

## 17. Definition of Done

A feature is considered DONE only when all applicable items are
satisfied:

``` text
Requirement understood
+
Existing architecture inspected
+
Implementation completed
+
No unnecessary duplication
+
Backend/API works
+
Frontend workflow works
+
Persistence verified
+
Error handling verified
+
Relevant existing functionality regression-checked
+
No obvious console/runtime errors
+
Actual results reported truthfully
=
DONE
```

For UI-only or backend-only tasks, apply the relevant subset and clearly
state what was not applicable.

------------------------------------------------------------------------

## 18. Testing Requirements

For every meaningful change:

### Python

Check syntax/import/runtime behavior for affected modules.

### API

Verify affected endpoints where practical:

-   expected success response;
-   validation failure;
-   authentication behavior where applicable.

### Web

Verify:

-   control loads;
-   button works;
-   modal works;
-   API call succeeds;
-   loading/error states behave correctly;
-   persisted result appears after refresh;
-   browser console has no new relevant errors.

### Persistence

For create/update/delete/upload:

-   verify database state through the application/API;
-   reload or revisit the screen;
-   confirm state remains correct.

### Regression

Check adjacent working functionality affected by the change.

Do not spend time running unrelated tests unless risk warrants it.

------------------------------------------------------------------------

## 19. Error Handling

Errors must be actionable.

Avoid:

-   swallowing exceptions;
-   always returning success;
-   fake fallback data;
-   hiding backend errors behind generic "Done" messages.

Frontend should show a useful message without exposing sensitive
internal details.

Backend logs may contain diagnostic context but must not contain
secrets.

------------------------------------------------------------------------

## 20. Performance

QA AI Studio uses local AI/RAG components that may be computationally
expensive.

Rules:

-   avoid loading large models repeatedly when an existing reusable
    instance/lifecycle is available;
-   avoid unnecessary embedding/reindexing;
-   avoid duplicate API calls;
-   use controlled concurrency;
-   preserve responsiveness during long operations;
-   use background workers/jobs where architecture requires them;
-   do not optimize by bypassing correctness.

Performance changes should be measurable where practical.

------------------------------------------------------------------------

## 21. Code Quality

Follow the style already established in the affected area.

Prefer:

-   small focused functions;
-   descriptive names;
-   clear separation of concerns;
-   minimal duplication;
-   comments for non-obvious decisions, not obvious syntax;
-   compatibility with existing project patterns.

Do not perform broad refactoring during a focused feature task unless
required to complete it safely.

Do not replace working code solely because another design appears
cleaner.

------------------------------------------------------------------------

## 22. Change Safety

Before editing:

1.  read relevant files;
2.  understand call paths;
3.  search for references;
4.  identify dependencies;
5.  preserve public method/API names unless change is required.

During editing:

-   modify the minimum necessary files;
-   do not delete functionality unrelated to the task;
-   do not silently change business behavior;
-   do not reset databases;
-   do not overwrite user data.

After editing:

-   inspect the diff;
-   run relevant verification;
-   report files changed.

------------------------------------------------------------------------

## 23. Git Rules

The main active development branch is expected to be `develop` unless
the user/task specifies otherwise.

Before risky work, confirm repository state.

Do not automatically:

-   force push;
-   reset hard;
-   rewrite shared history;
-   delete branches;
-   commit secrets/runtime databases/models.

Prefer small meaningful commits after stable milestones.

Do not commit automatically unless the user explicitly requests it or
the active task explicitly authorizes it.

------------------------------------------------------------------------

## 24. Scope Discipline

A task should solve the requested feature without opportunistically
changing unrelated modules.

Example:

If working on Knowledge Hub Manual Upload, do not also redesign:

-   Dashboard;
-   User Management;
-   Test Case Studio;
-   QA Automation;
-   AI Assistant.

If another module must change because of a genuine dependency, explain
why.

------------------------------------------------------------------------

## 25. Agent Working Method

For each development task:

``` text
1. Read AGENTS.md.
2. Read the task.
3. Inspect relevant existing source.
4. Inspect reference Desktop implementation when parity is involved.
5. Search for reusable backend/Core functionality.
6. Produce an internal gap/implementation plan.
7. Make minimum necessary changes.
8. Run relevant checks/tests.
9. Inspect the resulting diff.
10. Report verified results and remaining limitations.
```

Do not repeatedly ask the user for information that can be discovered
from the repository.

Do not spend tokens restating large portions of this file in completion
reports.

------------------------------------------------------------------------

## 26. Completion Report Format

Keep completion reports concise and factual.

Use:

``` text
Files changed:
- ...

Implemented:
- ...

Reused:
- ...

Verified:
- ...

Not verified / Remaining:
- ...
```

If there are no remaining limitations, say so only after relevant
verification.

Do not write marketing-style completion claims.

------------------------------------------------------------------------

## 27. Current Strategic Development Direction

Unless a task explicitly changes priority, the general development
direction is:

``` text
1. Knowledge Hub Desktop → Web functional parity
2. Browse / Manage Knowledge parity
3. AI Smart Upload parity and hardening
4. URL / Website Knowledge Discovery
5. Test Case Studio / QA Engineering
6. QA Automation
7. AI Assistant / RAG integration
8. Dashboard and management reporting
9. Security, roles, performance and production hardening
```

This is strategic guidance, not permission to implement future phases
during the current task.

------------------------------------------------------------------------

## 28. Knowledge Hub Quality Principle

Knowledge Hub is the foundation for downstream QA intelligence.

Stored knowledge should be:

-   structured;
-   traceable;
-   reviewable;
-   searchable;
-   version-aware where applicable;
-   based on actual sources;
-   suitable for later RAG and QA artifact generation.

Poor or fabricated Knowledge Hub data will degrade Test Case Studio,
Automation, AI Assistant, and reporting. Prioritize correctness of
captured knowledge.

------------------------------------------------------------------------

## 29. Final Non-Negotiable Rules

1.  **Inspect before editing.**
2.  **Desktop is the functional reference for Desktop-to-Web parity.**
3.  **Reuse before rewriting.**
4.  **Do not hardcode dynamic business data.**
5.  **Do not fake AI results, uploads, execution, discovery, or
    dashboard data.**
6.  **Do not mix Knowledge Hub, Test Case Studio, and QA Automation
    responsibilities.**
7.  **Preserve authentication and existing working functionality.**
8.  **Never commit secrets, runtime databases, vector databases, or
    downloaded models.**
9.  **Verify before claiming completion.**
10. **Report limitations truthfully.**
11. **Prefer minimum safe changes over broad rewrites.**
12. **Keep QA AI Studio enterprise-grade, maintainable, and
    data-driven.**

------------------------------------------------------------------------

## 30. Instruction Precedence

When instructions conflict, use this order:

1.  Explicit current user/task instruction.
2.  Safety/security and repository integrity.
3.  Core product boundaries in this `AGENTS.md`.
4.  Established current architecture and API contracts.
5.  Desktop reference behavior for parity work.
6.  Existing coding style and implementation patterns.

A task-specific request may legitimately change product behavior, but do
not infer such a change from vague wording. If a requested change would
violate a major architectural boundary, identify the conflict before
implementing it.
