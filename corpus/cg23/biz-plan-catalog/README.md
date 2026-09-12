# CG-23 — Biz plan catalog real-consumer qualification

This case binds the current open Biz requirement in `hvritual/biz#62` to exact consumer source evidence.

The current Biz contract owns plan/version facts but exposes no authoritative platform plan-code discovery read surface. Existing `commercial.plan.list` only lists versions for a caller-supplied `plan_code`.

Qualification is read-only. It requires the exact pinned Biz commit/tree and source blobs, verifies issue #62 remains open, proves the existing version-list contract, and proves that exact `GET /v1/platform/plans` discovery is absent from both the proto contract and generated operation plans.

A successful receipt means only `REAL_CONSUMER_PROBLEM_QUALIFIED` and `CONTROLLED_PLAN_REQUIRED`. It does not authorize a Biz patch, branch, PR, merge, framework change, or consumer adoption.

The next governance step must produce a bounded implementation plan before any consumer mutation.
