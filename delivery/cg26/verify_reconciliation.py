#!/usr/bin/env python3
import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path


class ReconciliationError(Exception):
    def __init__(self, reason, detail=None):
        super().__init__(reason)
        self.reason = reason
        self.detail = detail


def require(condition, reason, detail=None):
    if not condition:
        raise ReconciliationError(reason, detail)


def load(path):
    return json.loads(Path(path).read_text())


def canonical_digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def reconciliation_core(value):
    return {k: v for k, v in value.items() if k != "reconciliationId"}


def git(root, *args, allow_failure=False):
    cp = subprocess.run(
        ["git", "-C", str(root), *args],
        check=False,
        capture_output=True,
        text=True,
    )
    if cp.returncode != 0 and not allow_failure:
        raise ReconciliationError(
            "GIT_READBACK_FAILED",
            {"args": list(args), "stderr": cp.stderr[-1200:]},
        )
    return cp.returncode, cp.stdout.strip(), cp.stderr.strip()


def text(root, path):
    p = root / path
    require(p.is_file(), "LIVE_AUTHORITY_FILE_MISSING", {"path": path})
    return p.read_text()


def exact_blob(root, path):
    return git(root, "rev-parse", "HEAD:" + path)[1]


def step_map(jobs_json, expected_job_id):
    jobs = jobs_json.get("jobs", [])
    candidates = [j for j in jobs if j.get("id") == expected_job_id]
    require(len(candidates) == 1, "HISTORICAL_JOB_NOT_FOUND", {"job": expected_job_id})
    job = candidates[0]
    require(job.get("name") == "qualify", "HISTORICAL_JOB_NAME_MISMATCH")
    require(job.get("status") == "completed", "HISTORICAL_JOB_NOT_COMPLETED")
    require(job.get("conclusion") == "success", "HISTORICAL_JOB_NOT_SUCCESS")
    return {s.get("name"): s.get("conclusion") for s in job.get("steps", [])}


def verify_source_bindings(protocol, cg24_protocol, cg24_plan, cg25_protocol, cg25_case, governance_root):
    require(protocol.get("schemaVersion") == 1, "PROTOCOL_SCHEMA_MISMATCH")
    require(protocol.get("id") == "CG26-BIZ-PLAN-CATALOG-SEMANTIC-RECONCILIATION", "WRONG_PROTOCOL")
    require(protocol.get("status") in {"CANDIDATE", "QUALIFIED"}, "UNEXPECTED_PROTOCOL_STATUS")

    source = protocol.get("source", {})
    cg24 = source.get("cg24", {})
    require(cg24.get("protocolId") == "CG24-REAL-CONSUMER-CONTROLLED-PLAN", "CG24_PROTOCOL_ID_MISMATCH")
    require(cg24_protocol.get("id") == cg24.get("protocolId"), "CG24_PROTOCOL_BINDING_MISMATCH")
    require(cg24_protocol.get("status") == "QUALIFIED", "CG24_NOT_QUALIFIED")
    require(cg24_plan.get("planId") == cg24.get("planId"), "CG24_PLAN_ID_MISMATCH")
    require(cg24_plan.get("decision") == "PLAN_READY_FOR_CONTROLLED_IMPLEMENTATION", "CG24_PLAN_DECISION_MISMATCH")
    require(cg24_plan.get("executionAuthorized") is False, "CG24_EXECUTION_ALREADY_AUTHORIZED")

    cg25 = source.get("cg25", {})
    require(cg25.get("protocolId") == "CG25-REAL-CONSUMER-AUTHORIZATION-READINESS", "CG25_PROTOCOL_ID_MISMATCH")
    require(cg25_protocol.get("id") == cg25.get("protocolId"), "CG25_PROTOCOL_BINDING_MISMATCH")
    require(cg25_protocol.get("status") == "QUALIFIED", "CG25_NOT_QUALIFIED")
    require(cg25_protocol.get("requiredDisposition") == cg25.get("requiredDisposition"), "CG25_DISPOSITION_MISMATCH")
    require(cg25_case.get("readinessId") == cg25.get("readinessId"), "CG25_READINESS_ID_MISMATCH")
    require(cg25_case.get("decision") == "AUTHORIZATION_NOT_GRANTED", "CG25_AUTHORIZATION_REFUSAL_MISSING")
    require(cg25_case.get("disposition") == "LIVE_RECONCILIATION_REQUIRED", "CG25_LIVE_RECONCILIATION_NOT_REQUIRED")

    for family in ("cg24", "cg25"):
        for item in source[family].get("authorities", []):
            actual = exact_blob(governance_root, item["path"])
            require(actual == item["blob"], "SOURCE_AUTHORITY_BLOB_MISMATCH", {
                "family": family, "path": item["path"], "expected": item["blob"], "actual": actual
            })


def verify_live_identity(protocol, reconciliation, biz_root, issue):
    live = protocol["liveConsumer"]
    require(reconciliation.get("liveConsumer", {}).get("repository") == live["repository"], "RECONCILIATION_REPOSITORY_MISMATCH")
    head = git(biz_root, "rev-parse", "HEAD")[1]
    tree_sha = git(biz_root, "rev-parse", "HEAD^{tree}")[1]
    require(head == live["sha"], "LIVE_CONSUMER_SHA_MISMATCH", {"expected": live["sha"], "actual": head})
    require(tree_sha == live["tree"], "LIVE_CONSUMER_TREE_MISMATCH", {"expected": live["tree"], "actual": tree_sha})
    require(reconciliation["liveConsumer"]["sha"] == live["sha"], "RECONCILIATION_LIVE_SHA_MISMATCH")
    require(reconciliation["liveConsumer"]["tree"] == live["tree"], "RECONCILIATION_LIVE_TREE_MISMATCH")

    require(issue.get("number") == live["sourceIssue"], "SOURCE_ISSUE_NUMBER_MISMATCH")
    require(issue.get("state") == live["requiredIssueState"], "SOURCE_ISSUE_STATE_MISMATCH")

    for item in live.get("authorities", []):
        actual = exact_blob(biz_root, item["path"])
        require(actual == item["blob"], "LIVE_AUTHORITY_BLOB_MISMATCH", {
            "path": item["path"], "expected": item["blob"], "actual": actual
        })


def verify_historical_projection(protocol, reconciliation, biz_root, run_json, jobs_json):
    h = protocol["historicalQualification"]
    require(run_json.get("id") == h["run"], "HISTORICAL_RUN_ID_MISMATCH")
    require(run_json.get("name") == h["workflow"], "HISTORICAL_WORKFLOW_NAME_MISMATCH")
    require(run_json.get("head_sha") == h["headSha"], "HISTORICAL_RUN_HEAD_MISMATCH")
    require(run_json.get("status") == "completed", "HISTORICAL_RUN_NOT_COMPLETED")
    require(run_json.get("conclusion") == "success", "HISTORICAL_RUN_NOT_SUCCESS")

    steps = step_map(jobs_json, h["job"])
    for required_step in (
        "Verify deterministic contract generation",
        "Qualify plan authority and regressions",
        "Seed trusted browser identities",
        "Start first-party OIDC IdP",
        "Start Biz BFF resource server",
        "Run plan catalog browser authority chain",
        "Verify clean candidate",
    ):
        require(steps.get(required_step) == "success", "HISTORICAL_REQUIRED_STEP_NOT_SUCCESS", {
            "step": required_step, "conclusion": steps.get(required_step)
        })

    rc, _, _ = git(biz_root, "merge-base", "--is-ancestor", h["headSha"], "HEAD", allow_failure=True)
    require(rc == 0, "HISTORICAL_HEAD_NOT_ANCESTOR_OF_LIVE")

    _, diff, _ = git(biz_root, "diff", "--name-only", h["headSha"], "HEAD")
    changed = [line for line in diff.splitlines() if line]
    closure = protocol["semanticClosure"]
    touched = []
    for path in changed:
        if path in closure.get("exactPaths", []) or any(path.startswith(prefix) for prefix in closure.get("prefixes", [])):
            touched.append(path)
    require(not touched, "SEMANTIC_CLOSURE_CHANGED_SINCE_QUALIFIED_HEAD", {
        "touched": touched, "changedCount": len(changed)
    })

    live_rec = reconciliation["liveConsumer"]
    require(live_rec.get("historicalQualifiedHead") == h["headSha"], "RECONCILIATION_HISTORICAL_HEAD_MISMATCH")
    require(live_rec.get("historicalRun") == h["run"], "RECONCILIATION_HISTORICAL_RUN_MISMATCH")
    require(live_rec.get("historicalJob") == h["job"], "RECONCILIATION_HISTORICAL_JOB_MISMATCH")
    return steps, changed


def find_live_operation(biz_root, operation_id):
    manifest = load(biz_root / "contracts/generated/operation-plans.json")
    operations = manifest.get("operations", [])
    matches = [op for op in operations if op.get("operationId") == operation_id]
    require(len(matches) == 1, "LIVE_GENERATED_OPERATION_IDENTITY_NOT_UNIQUE", {
        "operationId": operation_id, "count": len(matches)
    })
    return matches[0]


def verify_semantic_surface(protocol, reconciliation, biz_root):
    contract = protocol["semanticContract"]
    planned = protocol["plannedIdentity"]
    accepted = protocol["acceptedLiveIdentity"]
    identity = reconciliation["identity"]

    require(identity.get("plannedOperationId") == planned["operationId"], "PLANNED_OPERATION_ID_MISMATCH")
    require(identity.get("plannedUseCase") == planned["useCase"], "PLANNED_USE_CASE_MISMATCH")
    require(identity.get("liveOperationId") == accepted["operationId"], "LIVE_OPERATION_ID_MISMATCH")
    require(identity.get("liveUseCase") == accepted["useCase"], "LIVE_USE_CASE_MISMATCH")
    require(identity.get("identityDiverged") is True, "IDENTITY_DIVERGENCE_MUST_BE_EXPLICIT")
    require(identity.get("identityOnlyPatchRequired") is False, "IDENTITY_ONLY_PATCH_INFERENCE_FORBIDDEN")

    op = find_live_operation(biz_root, accepted["operationId"])
    require(op.get("useCase") == accepted["useCase"], "LIVE_GENERATED_USE_CASE_MISMATCH")

    security = op.get("security", {})
    require(sorted(security.get("authentication", [])) == sorted(contract["authentication"]), "LIVE_AUTHENTICATION_MISMATCH")
    require(security.get("permissions") == [contract["permission"]], "LIVE_PERMISSION_MISMATCH")
    require(security.get("tenantRequired", False) is contract["tenantRequired"], "LIVE_TENANT_SCOPE_MISMATCH")

    execution = op.get("execution", {})
    require(execution.get("transaction") == contract["transaction"], "LIVE_TRANSACTION_MISMATCH")
    require(execution.get("idempotency") == contract["idempotency"], "LIVE_IDEMPOTENCY_MISMATCH")

    http_bindings = op.get("bindings", {}).get("http", [])
    require(any(
        b.get("method") == contract["httpMethod"] and b.get("path") == contract["httpPath"]
        for b in http_bindings
    ), "LIVE_HTTP_BINDING_MISMATCH")

    proto = text(biz_root, "contracts/proto/commercial/v1/plan.proto")
    for needle in (
        "message PlanCatalogEntryDTO",
        "message ListPlansRequest",
        "message ListPlansResponse",
        "rpc ListPlans(ListPlansRequest) returns (ListPlansResponse)",
        'get: "/v1/platform/plans"',
        'id: "commercial.plan.discover"',
        'use_case: "list_plans"',
        'permissions: "platform.plan.read"',
        "tenant_required: false",
        "AUTHENTICATION_API_KEY",
        "AUTHENTICATION_WEB_SESSION",
        "TRANSACTION_READ_ONLY",
        "IDEMPOTENCY_NONE",
    ):
        require(needle in proto, "LIVE_PROTO_SEMANTIC_REQUIREMENT_MISSING", {"needle": needle})

    req = reconciliation["semanticRequirements"]
    require(req.get("httpBinding") == contract["httpMethod"] + " " + contract["httpPath"], "RECONCILIATION_HTTP_BINDING_MISMATCH")
    require(sorted(req.get("authentication", [])) == sorted(contract["authentication"]), "RECONCILIATION_AUTH_MISMATCH")
    require(req.get("permission") == contract["permission"], "RECONCILIATION_PERMISSION_MISMATCH")
    require(req.get("tenantRequired") is contract["tenantRequired"], "RECONCILIATION_TENANT_MISMATCH")
    require(req.get("transaction") == contract["transaction"], "RECONCILIATION_TRANSACTION_MISMATCH")
    require(req.get("idempotency") == contract["idempotency"], "RECONCILIATION_IDEMPOTENCY_MISMATCH")
    require(req.get("paginationKey") == contract["paginationKey"], "RECONCILIATION_PAGINATION_KEY_MISMATCH")
    require(req.get("defaultPageSize") == contract["defaultPageSize"], "RECONCILIATION_DEFAULT_PAGE_SIZE_MISMATCH")
    require(req.get("maximumPageSize") == contract["maximumPageSize"], "RECONCILIATION_MAX_PAGE_SIZE_MISMATCH")
    require(req.get("authoritativeTables") == contract["authoritativeTables"], "RECONCILIATION_AUTHORITY_TABLES_MISMATCH")
    for field in ("newAuthoritativeStore", "browserCatalog", "subscriptionInference", "generatedHandEdit", "frameworkModification"):
        require(req.get(field) is False, "RECONCILIATION_FORBIDDEN_SEMANTIC_ENABLED", {"field": field})


def verify_implementation_semantics(protocol, biz_root):
    persistence = text(biz_root, "internal/commercial/infrastructure/persistence/plan_catalog.go")
    for needle in (
        'Table("biz_commercial_plans AS p")',
        'JOIN biz_commercial_plan_versions AS v',
        'v.version = p.latest_version',
        'p.latest_version > 0',
        'p.plan_code > ?',
        'Order("p.plan_code ASC")',
        'Limit(limit)',
    ):
        require(needle in persistence, "AUTHORITATIVE_CATALOG_QUERY_REQUIREMENT_MISSING", {"needle": needle})
    require("subscription" not in persistence.lower(), "SUBSCRIPTION_INFERENCE_DETECTED_IN_CATALOG_QUERY")

    usecase = text(biz_root, "internal/commercial/application/planmanagement/internal/usecase/catalog.go")
    for needle in (
        "req.PageSize > 100",
        "size = 20",
        "size+1",
        "req.AfterPlanCode",
        "NextAfterPlanCode",
        "scope.Repositories().Plans.Catalog",
    ):
        require(needle in usecase, "CATALOG_USECASE_REQUIREMENT_MISSING", {"needle": needle})

    integration = text(biz_root, "integration/ce13_plan_catalog_mysql_test.go")
    for needle in (
        "TestCE13PlanCatalogMySQLDeterministicPaginationAndAuthority",
        "PageSize: 1",
        "NextAfterPlanCode",
        'publishedV1.State != "PUBLISHED"',
        'publishedV1.Name != "Beta"',
        "PermissionDenied",
        "InvalidArgument",
        "/v1/platform/plans?",
    ):
        require(needle in integration, "CATALOG_INTEGRATION_EVIDENCE_MISSING", {"needle": needle})

    seed = text(biz_root, "integration/ce13_platform_web_session_seed_test.go")
    for needle in (
        '"platform.plan.read"',
        "BindOIDCPlatformIdentity",
        "AllowedAPIKey",
        "AllowedSubject",
        "DeniedSubject",
        "TenantID",
    ):
        require(needle in seed, "WEB_SESSION_SEED_EVIDENCE_MISSING", {"needle": needle})

    browser = text(biz_root, "web/tests/ce13-plan-catalog/ce13-plan-catalog.spec.ts")
    for needle in (
        "TestCE13PlanCatalogTrustedPlatformDiscovery",
        'credentials: "include"',
        "allowed_api_key",
        "expect(tenantCatalog.status, tenantCatalog.text).toBe(403)",
        "expect(deniedCatalog.status, deniedCatalog.text).toBe(403)",
        "expect(firstResult.status, firstResult.text).toBe(200)",
        "nextAfterPlanCode",
    ):
        require(needle in browser, "BROWSER_AUTHORITY_EVIDENCE_MISSING", {"needle": needle})

    workflow = text(biz_root, ".github/workflows/ce13-plan-catalog-qualification.yml")
    for needle in (
        "mysql:8.4",
        "make -C biz check",
        "make -C biz generate",
        "git -C biz status --porcelain",
        "TestCE13PlanCatalogMySQLDeterministicPaginationAndAuthority",
        "TestCE07MySQL",
        "TestCE13PlatformWebSessionSeed",
        "Run plan catalog browser authority chain",
        "playwright.ce13-plan-catalog.config.ts",
    ):
        require(needle in workflow, "HISTORICAL_QUALIFICATION_WORKFLOW_REQUIREMENT_MISSING", {"needle": needle})
    require(workflow.index("make -C biz check") < workflow.index("make -C biz generate"), "YUNKA_CHECK_ORDER_INVALID")


def verify_verification_classes(protocol, reconciliation):
    required = protocol["requiredVerificationClasses"]
    require(len(required) == 16 and len(set(required)) == 16, "PROTOCOL_VERIFICATION_CLASS_SET_INVALID")
    results = reconciliation.get("verificationResults", [])
    by_class = {entry.get("class"): entry for entry in results}
    require(len(by_class) == len(results), "DUPLICATE_VERIFICATION_CLASS_RESULT")
    require(set(by_class) == set(required), "VERIFICATION_CLASS_SET_MISMATCH", {
        "missing": sorted(set(required) - set(by_class)),
        "extra": sorted(set(by_class) - set(required)),
    })
    for name in required:
        result = by_class[name]
        require(result.get("status") == "PASS", "VERIFICATION_CLASS_NOT_PASS", {"class": name})
        evidence = result.get("evidence")
        require(isinstance(evidence, list) and evidence and all(isinstance(x, str) and x for x in evidence),
                "VERIFICATION_CLASS_EVIDENCE_REQUIRED", {"class": name})

    vd = reconciliation.get("verificationDisposition", {})
    require(vd.get("requiredClassCount") == len(required), "VERIFICATION_CLASS_COUNT_MISMATCH")
    require(vd.get("allRequiredClassesSatisfied") is True, "ALL_REQUIRED_CLASSES_SATISFIED_REQUIRED")
    require(vd.get("historicalQualificationProjectedToLive") is True, "HISTORICAL_PROJECTION_REQUIRED")
    require(vd.get("semanticClosureChangedSinceQualifiedHead") is False, "SEMANTIC_CLOSURE_DRIFT_FORBIDS_NO_CHANGE")
    require(vd.get("consumerMutationRequired") is False, "CONSUMER_MUTATION_NOT_REQUIRED")


def verify_decision_and_authority(protocol, reconciliation):
    require(reconciliation.get("decision") == protocol["requiredDecision"], "RECONCILIATION_DECISION_MISMATCH")
    require(reconciliation.get("patchRequired") is False, "PATCH_WHEN_SEMANTICALLY_SATISFIED_FORBIDDEN")
    require(reconciliation.get("reason") == protocol["requiredReason"], "RECONCILIATION_REASON_MISMATCH")
    require(canonical_digest(reconciliation_core(reconciliation)) == reconciliation.get("reconciliationId"),
            "RECONCILIATION_ID_MISMATCH")

    ceiling = protocol["authorityCeiling"]
    authority = reconciliation.get("authority", {})
    for field, expected in ceiling.items():
        require(expected is False, "PROTOCOL_AUTHORITY_CEILING_INVALID", {"field": field})
        require(authority.get(field) is False, "RECONCILIATION_AUTHORITY_ESCALATION", {"field": field})


def verify(protocol, reconciliation, cg24_protocol, cg24_plan, cg25_protocol, cg25_case,
           governance_root, biz_root, issue, run_json, jobs_json):
    verify_source_bindings(protocol, cg24_protocol, cg24_plan, cg25_protocol, cg25_case, governance_root)
    verify_live_identity(protocol, reconciliation, biz_root, issue)
    _, changed = verify_historical_projection(protocol, reconciliation, biz_root, run_json, jobs_json)
    verify_semantic_surface(protocol, reconciliation, biz_root)
    verify_implementation_semantics(protocol, biz_root)
    verify_verification_classes(protocol, reconciliation)
    verify_decision_and_authority(protocol, reconciliation)

    return {
        "schemaVersion": 1,
        "status": "SEMANTIC_RECONCILIATION_QUALIFIED",
        "decision": "NO_CHANGE_REQUIRED",
        "reason": protocol["requiredReason"],
        "repository": protocol["liveConsumer"]["repository"],
        "liveSha": protocol["liveConsumer"]["sha"],
        "liveTree": protocol["liveConsumer"]["tree"],
        "historicalQualifiedHead": protocol["historicalQualification"]["headSha"],
        "postQualificationChangedFileCount": len(changed),
        "semanticClosureChangedFileCount": 0,
        "verificationClassCount": len(protocol["requiredVerificationClasses"]),
        "identityDiverged": True,
        "identityOnlyPatchRequired": False,
        "patchRequired": False,
        "consumerMutation": False,
        "authorizationGranted": False,
        "reconciliationId": reconciliation["reconciliationId"],
        "nextDisposition": "NO_CHANGE_REQUIRED"
    }


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--protocol", required=True)
    p.add_argument("--reconciliation", required=True)
    p.add_argument("--cg24-protocol", required=True)
    p.add_argument("--cg24-plan", required=True)
    p.add_argument("--cg25-protocol", required=True)
    p.add_argument("--cg25-case", required=True)
    p.add_argument("--governance-root", required=True)
    p.add_argument("--biz-root", required=True)
    p.add_argument("--issue-json", required=True)
    p.add_argument("--run-json", required=True)
    p.add_argument("--jobs-json", required=True)
    args = p.parse_args()
    try:
        out = verify(
            load(args.protocol),
            load(args.reconciliation),
            load(args.cg24_protocol),
            load(args.cg24_plan),
            load(args.cg25_protocol),
            load(args.cg25_case),
            Path(args.governance_root),
            Path(args.biz_root),
            load(args.issue_json),
            load(args.run_json),
            load(args.jobs_json),
        )
        print(json.dumps(out, indent=2, sort_keys=True))
        return 0
    except ReconciliationError as exc:
        out = {
            "schemaVersion": 1,
            "status": "REJECTED",
            "decision": "SEMANTIC_RECONCILIATION_NOT_QUALIFIED",
            "reason": exc.reason,
        }
        if exc.detail is not None:
            out["detail"] = exc.detail
        print(json.dumps(out, indent=2, sort_keys=True))
        return 2


if __name__ == "__main__":
    sys.exit(main())
