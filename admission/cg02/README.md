# CG-02 External Tool Admission

Status: **QUALIFIED / READY TO PUBLISH**

CG-02 qualifies external tools against the immutable CG-01 IoT SQLite startup corpus. External tools are admitted only as **advisory static detectors**; the authoritative proof for the defect remains the CG-01 held-lock runtime regression.

## Decision

### ast-grep 0.45.3

**Admission:** `QUALIFIED_DEFAULT_ADVISORY_STATIC_DETECTOR`

Qualified matrix:

| Sample | Expected | Result |
|---|---:|---:|
| buggy | 1 | 1 |
| fixed | 0 | 0 |
| M1 mutation | 1 | 1 |
| legal/unrelated control | 0 | 0 |

Why default: for structural source guards that both candidates can prove, ast-grep has the smaller operational surface. The qualified Linux/amd64 release asset is checksum-locked, the real `ast-grep` binary is used instead of the deprecated `sg` wrapper, and JSON evidence is compared semantically rather than by object-key byte order.

### go-ruleguard v0.4.5 + DSL v0.3.22

**Admission:** `QUALIFIED_CONDITIONAL_GO_SPECIFIC_STATIC_DETECTOR`

It passes the same `1/0/1/0` matrix and produces semantically deterministic evidence. It is not the default for this class of structural rule because this pinned release has a materially larger integration surface:

- full Go package/type environment must be available;
- the DSL is a separately versioned module;
- dynamic rule loading requires a legacy GOPATH package archive bootstrap for the pinned release;
- package/test-package duplicate diagnostics must be normalized by source position and message.

Use ruleguard when Go-specific package/type-aware matching materially improves the rule and justifies that integration cost.

## Frozen qualification evidence

- Run: `34557750571`
- Job: `103133935337`
- Workflow head: `f065a7e94a8e17945a04772d44f885c69627eec5`
- Artifact: `10183203042`
- Artifact SHA-256: `c49bce878405c451c52e3553577bacb6760b55a16d794870056961faa66fcc22`
- Artifact `SHA256SUMS`: replayed successfully after download
- Go: `1.25.13`
- CG-01 corpus blob: `5cba82314a027f4ee6284741518a45238b2b6fdc`
- Yunka pin: `057ebcf88a87303eb633eb6e604d306f633dfac0`, unchanged

Consumer tracked source and dependency locks were unchanged.

## Authority boundary

A static detector may find or block the known source shape. It must not claim that the SQLite runtime defect is repaired without the CG-01 behavior regression. This separation is intentional:

```text
static detector -> fast recurrence prevention
behavior regression -> authoritative functional proof
```

## Publication boundary

Stable publication for CG-02 consists only of this decision and `decision.json` under `admission/cg02/`. Branch-only qualification workflows and one-off harness code are not part of governance `main`.
