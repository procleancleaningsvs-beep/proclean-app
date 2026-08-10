---
name: proclean-task-router
description: Classify every ProClean-App feature, bugfix, refactor, UI, document, import, data, permission, infrastructure, deployment, or other code-related task as L1 SURGICAL, L2 CONTROLLED, or L3 CRITICAL. Use before edits, generated files, mutating commands, branches, worktrees, commits, pushes, deployments, or data operations to run the mandatory read-only preflight and select approval, planning, and verification gates.
---

# Route ProClean tasks by operational risk

Classify from repository evidence and possible downstream effects. Do not infer low risk from a small request.

## Perform the read-only preflight

Before any mutation:

1. Restate the requested outcome, acceptance criteria already given, and explicit exclusions.
2. Run `git status -sb`; confirm the branch and upstream state.
3. Record pre-existing modified and untracked files. Do not read sensitive contents or alter unrelated work.
4. Inspect the smallest relevant set of files, call sites, routes, templates, shared assets, tests, persistence paths, permissions, and recent related changes.
5. Identify the smallest plausible change surface and unresolved impact.
6. Read [references/protected-surfaces.md](references/protected-surfaces.md).
7. Assign the highest level triggered by intent, likely files, downstream effects, reversibility, production impact, and verification burden.
8. Read only the selected section of [references/level-playbooks.md](references/level-playbooks.md).

If material evidence is unavailable, ask for it or classify L3. Never guess downward.

## Classify

### L1 SURGICAL

Assign L1 only when every condition holds:

- presentation-only change in one local template, stylesheet, or component;
- no route, behavior, JavaScript flow, persistence, calculation, permission, document, export, dependency, shared asset, or production effect;
- trivially reversible;
- targeted visual verification is sufficient.

### L2 CONTROLLED

Assign L2 for localized behavior in one non-protected module when:

- a local route, form, validation rule, filter, modal-data flow, or module JavaScript may change;
- existing data contracts, persistence semantics, calculations, permissions, and shared behavior remain unchanged;
- targeted automated and manual verification can contain the risk.

### L3 CRITICAL

Assign L3 when any protected surface is modified or plausibly affected, multiple modules or shared files are involved, production or rollback risk is material, or impact remains ambiguous. Protected surfaces trigger L3 regardless of line count.

## Announce routing

Output this block before implementation or any other mutation:

```text
PROCLEAN ROUTING
Level: <L1 SURGICAL | L2 CONTROLLED | L3 CRITICAL>
Reason: <repository evidence and downstream effect>
Requested outcome: <concise>
Explicit exclusions: <concise>
Intended files: <paths or unresolved>
Pre-existing changes: <paths to preserve>
Protected surfaces: <unchanged, plausibly affected, or affected>
Workflow: <selected playbook, diagnosis, tests, and verification>
Codex setup: Mode=<Default|Plan>; Context=<focused|module|full relevant>; Reasoning=<recommended>; Fast=<on|off>
Approval gate: <request sufficient | design approval | design and plan approval>
```

Recommend settings; do not change user settings implicitly.

## Enforce gates

- L1: require no extra design ceremony unless evidence escalates the task.
- L2: diagnose, define acceptance criteria, present a compact design, and obtain design approval before planning and implementation.
- L3: complete a read-only impact audit, record invariants and rollback, obtain explicit design approval, then explicit implementation-plan approval.
- Require separate explicit authorization for a branch or worktree.
- Do not treat approval of design or plan as authorization to commit, push, deploy, or mutate production.

## Escalate at runtime

Re-evaluate after diagnosis and from the final diff. If a protected path, shared dependency, persistence effect, broader scope, unexpected file, or insufficient verification appears:

1. Stop before further mutation.
2. Announce the higher level and evidence.
3. Revise the change surface, test burden, rollback, and approval gate.
4. Resume only after the higher gate is satisfied.

Do not preserve a lower level because implementation has started. Do not downgrade L3 without evidence resolving every trigger and explicit user acceptance.
