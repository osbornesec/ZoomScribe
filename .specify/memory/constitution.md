<!--
SYNC IMPACT REPORT
- Version change: None -> 1.0.0
- Description: Initial creation of the project constitution.
- Sections Added: All (initial file creation)
- Sections Removed: None
- Templates Updated:
  - ✅ .specify/templates/plan-template.md
- Follow-up TODOs:
  - Performance Budgets: Define concrete targets for latency, memory, and throughput in section 12.
-->

/constitution # Project Constitution for an AI Coding Assistant
> Source of truth for non‑negotiable rules that govern how specifications become code.

**Version**: 1.0.0
_Last updated: 2025-09-28 • Maintainer: @michael_

---

## Constitution Update Checklist
When you amend this file, **synchronize the rest of the Spec Kit workflow** so the agent enforces it end‑to‑end:

- [ ] Re‑generate or adjust **planning templates** to include a “Constitution Compliance” section (your repo may keep these under `/templates` or `/.specify/templates`).
- [ ] Ensure `/specify`, `/plan`, and `/tasks` **commands/commands.md** for your agent (e.g., Copilot/Claude/Cursor) read `.specify/memory/constitution.md`.
- [ ] If your plan template prints a footer like “_Validated against .specify/memory/constitution.md_”, keep it intact so plans show they were checked.  
- [ ] If your repo has agent files (e.g., `CLAUDE.md`, `AGENTS.md`), reflect these rules there too.
- [ ] Re‑open the most recent feature branch and re‑run `/plan` if any rule meaningfully changed.

---

## 0) Purpose & Scope
This constitution defines **binding principles** for an AI coding assistant working in this repository. It governs:
- how to interpret specs,
- how to plan and implement,
- what “done” means,
- and what is **out of bounds**.

All later artifacts (specs, plans, tasks, code) **must comply** with this document. If anything conflicts, **the constitution wins**.

---

## 1) Decision Hierarchy
Order of authority for any change:

1. **This constitution** (non‑negotiable).
2. The **current feature spec** (`/specs/<feature>/spec.md`).
3. The **implementation plan** (`/specs/<feature>/plan.md`).
4. The **generated tasks** (`/specs/<feature>/tasks.md`).
5. Existing codebase conventions (when not in conflict).
6. Ask for clarification only when blocking; otherwise proceed with the safest, smallest change that honors 1–5.

---

## 2) North‑Star Principles (non‑negotiable)
- **Spec‑first**: Never start coding without a spec & plan anchored in this constitution.  
- **Small, reversible steps**: Prefer minimal diffs and short‑lived branches.  
- **TDD mindset**: Red → Green → Refactor. Write/extend tests **before** or alongside code.  
- **Secure by default**: Follow the security standard chosen below; never commit secrets.  
- **Accessible, reliable, observable**: Ship features that people can use, trust, and operate.  
- **Maintainability > novelty**: Favor boring, proven solutions over flashy complexity.  
- **No hallucinated facts**: If you’re uncertain, state the uncertainty, cite sources, or propose a safe fallback.  
- **License & IP respect**: Don’t copy/paste licensed code you cannot include; reference with attribution when allowed.

---

## 3) Quality Gates (Definition of Done)
A change is **mergeable** only if all gates are satisfied:

- **Spec & Plan**
  - [ ] The feature spec lists user‑visible behavior, acceptance criteria, and edge cases.
  - [ ] The plan enumerates risks and applies the Security Standard (below).

- **Tests**
  - [ ] Unit tests cover new logic (target: **≥ 80% lines**, **≥ 70% branches** for touched areas).
  - [ ] Integration/contract tests for I/O boundaries (APIs, DB, message bus).
  - [ ] Regression tests for any fixed bug.
  - [ ] Negative tests for failure paths & timeouts.

- **Static Analysis & Types**
  - [ ] Lint and formatter pass with zero errors.
  - [ ] Type‑checking passes (TS/Flow/Mypy/etc.). Enforce strict modes where feasible.

- **Security**
  - [ ] No hard‑coded secrets; use env/secrets manager.
  - [ ] Dep scan shows **no Critical/High vulns** (or documented exception with remediation plan).
  - [ ] Sensitive data flows audited; logs redact PII/secrets.
  - [ ] Input validation & authz decisions are covered by tests.

- **Performance & Reliability**
  - [ ] No algorithmic regressions for hot paths; Big‑O stays the same or better.
  - [ ] Long‑running operations have timeouts, retries (with jitter), and idempotency where needed.

- **Docs & Ops**
  - [ ] README/CHANGELOG updated; API/docstrings added where useful.
  - [ ] Observability: useful logs (no PII), metrics counters, and traces on key flows.
  - [ ] Migration/rollback steps documented for schema or config changes.

---

## 4) Security Standard (choose one or more)
To make security concrete, the assistant must align with at least one recognized standard:

- **OWASP ASVS**: Target **L2** for internet‑exposed applications; **L1** otherwise.  
  - Apply relevant chapters (authn/authz, data validation, crypto, error handling, API, etc.).  
- **NIST SSDF (SP 800‑218)**: Integrate practices across Prepare→Protect→Produce→Respond into planning & tasks.

**Agent behaviors mandated by this section**
- Prefer framework‑provided auth/session primitives over custom crypto.
- Validate all untrusted inputs at trust boundaries; encode on output.
- Default‑deny authorization checks at the boundary layer, with unit tests.
- Never log secrets or raw tokens. Prefer structured logs with explicit redaction.
- Add threat notes in the plan for new endpoints, secrets, or elevated data flows.

---

## 5) Dependency & Supply‑Chain Policy
- Use the smallest dependency set that satisfies the spec. Remove unused deps.
- Pin versions; record lockfiles; prefer reproducible builds/containers.
- Block licenses incompatible with this repo’s license policy (define allow/deny list).
- Prefer first‑party SDKs and actively maintained libraries; avoid abandonware.
- Add SBOM generation to CI when feasible.

---

## 6) Git & Review Policy
- **Branching**: `spec/<id>`, `feat/<id>-<slug>`, `fix/<id>-<slug>`.
- **Commits**: Conventional Commits style (e.g., `feat:`, `fix:`, `docs:`). Small, cohesive commits only.
- **PRs**: Link the spec & plan. Describe testing, risks, rollbacks. Seek human review for sensitive changes.

---

## 7) Observability & Operability
- Emit structured logs at INFO for normal flow; DEBUG sparingly; ERROR for actionable failures.
- Record metrics for throughput, latency, errors of new endpoints/jobs.
- Add traces/spans for cross‑service operations where tracing exists.

---

## 8) Accessibility & Internationalization
- Respect platform accessibility guidelines (e.g., WCAG for web; OS standards for desktop/mobile).
- Provide alt text, focus order, keyboard support, and color‑contrast norms in UI work.

---

## 9) Documentation Requirements
- Update or create: `README.md`, feature docs under `/docs` or the feature folder, and code‑adjacent docstrings.
- Include examples and minimal quickstart steps for new modules and CLI flags.
- If copying patterns from external sources, link the reference in the plan.

---

## 10) AI Assistant Operating Rules
- **Context discipline**: Read this constitution first, then the current spec, plan, and tasks before proposing changes.
- **Ask‑when‑blocked**: If a requirement is ambiguous and materially affects design, propose 2–3 safe options and ask.
- **No speculative code sprawl**: Do not create files or services not justified by the spec/plan.
- **Citations**: When bringing in algorithms or patterns from external sources, cite them in `research.md` or the plan.
- **Respect repo tooling**: Use existing linters, formatters, test runners, and CI scripts.
- **Safety boundaries**: Decline requests that would introduce vulnerabilities, violate licenses, or bypass tests.

---

## 11) Per‑Stack Profiles (fill in what applies; keep others N/A)

### 11.1 TypeScript/Node
- Types: `strict` mode; no `any` without justification.
- Testing: Vitest/Jest; aim for fast, isolated unit tests.
- Lint/Format: ESLint + Prettier; fix warnings unless justified.
- Runtime: Keep Node LTS; avoid ESM/CJS mixing unless required.

### 11.2 Python
- Types: Mypy strict for new modules; pyproject‑based config.
- Testing: Pytest with fixtures; property‑based tests where useful (Hypothesis).
- Lint/Format: Ruff + Black; no flake8‑ignored errors.

### 11.3 Go
- Testing: `go test -race`; benchmark hot paths when changed.
- Lint/Format: `go vet` + `gofmt` + `staticcheck`.
- Errors: Wrap with context; no panics in library code.

*(Add Java/Kotlin/Swift/C# profiles similarly as needed.)*

---

## 12) Performance Budgets (customize per repo)
- Hot path handlers ≤ **[TODO: Define p50 latency] ms** p50 / **[TODO: Define p95 latency] ms** p95 under expected load.
- New endpoint memory overhead within **[TODO: Define memory overhead] MB** steady‑state.
- For batch jobs, document throughput targets (e.g., **[TODO: Define throughput target] items/sec**) and SLA.

---

## 13) Migration & Data Policy
- Schema changes: backward compatible migrations; provide rollback steps.
- PII handling: data minimization; encryption in transit & at rest; retention documented.
- Export/erase user data paths must be covered by tests when applicable.

---

## 14) Exceptions Process
- If a rule must be waived: open an issue linked to the PR and document:
  - The rule, the justification, the risk, the time‑boxed remediation.
- Merge only with maintainer approval.

---

## 15) “Constitution Compliance” (for templates & CI)
Planners and CI should assert:

- [ ] Requirements in the plan trace to this constitution’s gates (Security, Tests, Docs).
- [ ] The plan lists the chosen Security Standard (ASVS/NIST SSDF) and how it’s applied.
- [ ] Tasks explicitly include tests, lint/type checks, dep scan, and rollback steps.
- [ ] Any exceptions are linked and time‑boxed.

---

_This document is intentionally short on tech‑stack dogma but strict on **outcomes**. Expand the per‑stack profiles to encode your team’s idioms._
