# ProClean risk-level playbooks

Read only the section selected during routing.

## L1 SURGICAL

Purpose: deliver a fast, reversible, presentation-only local change.

1. Confirm one local target and explicit no-touch boundaries.
2. Recommend Default mode, focused context, low or medium reasoning, and Fast on when available.
3. Make the smallest change without dependencies or global selectors.
4. Inspect the complete task diff and every created file.
5. Render the affected view; verify relevant desktop/mobile states, reduced motion when applicable, and unchanged action behavior.
6. Run `proclean-change-verification` and report evidence.

Exit only when the local visual result is correct and no protected or shared surface changed.

## L2 CONTROLLED

Purpose: contain localized behavior in one non-protected module.

1. Diagnose the current flow and define measurable acceptance criteria.
2. Recommend Plan mode for design/planning, module context, medium or high reasoning, and Fast off.
3. Present a compact design with alternatives when architecture is not predetermined.
4. Obtain explicit design approval and write a concise implementation/test plan.
5. Add or update targeted tests before implementation where viable.
6. Implement in small steps while preserving contracts, permissions, persistence semantics, and shared behavior.
7. Exercise success, empty, validation, unauthorized, and error states as applicable.
8. Review the final diff, reclassify, and run `proclean-change-verification`.

Exit only when acceptance criteria pass and no L3 surface is involved.

## L3 CRITICAL

Purpose: contain operational, legal, financial, identity, document, persistence, or production risk.

1. Perform a read-only audit and dependency/impact map.
2. Recommend Plan mode, full relevant context, high or highest practical reasoning, and Fast off.
3. Record invariants, exact acceptance criteria, exclusions, rollback, and evidence requirements.
4. Present real alternatives and obtain explicit design approval.
5. Write a detailed implementation and verification plan; obtain explicit plan approval.
6. Ask separately before creating a branch or worktree. Never operate on production data.
7. Use fixtures or verified copies and test-driven development where viable. Preserve before/after controls.
8. Implement bounded tasks with scope and quality review at plan checkpoints.
9. Run every relevant profile in `proclean-change-verification`.
10. Review the complete diff for scope drift, secrets, permissions, persistence, shared effects, and rollback credibility.
11. Present commit, push, deployment, and production smoke steps separately; execute each only with explicit authorization.

Exit only when all governed evidence is complete, rollback is credible, and every invariant is accounted for.
