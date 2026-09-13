#!/usr/bin/env python3
import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path


class ReadinessError(Exception):
    def __init__(self, reason, detail=None):
        super().__init__(reason)
        self.reason = reason
        self.detail = detail


def require(condition, reason, detail=None):
    if not condition:
        raise ReadinessError(reason, detail)


def load(path):
    return json.loads(Path(path).read_text())


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def case_core(case):
    return {key: value for key, value in case.items() if key != "readinessId"}


def git(root, *args):
    cp = subprocess.run(
        ["git", "-C", str(root), *args],
        check=False,
        capture_output=True,
        text=True,
    )
    if cp.returncode != 0:
        raise ReadinessError(
            "GIT_READBACK_FAILED",
            {"args": list(args), "stderr": cp.stderr[-1000:]},
        )
    return cp.stdout.strip()


def read(root, path):
    p = Path(root) / path
    require(p.is_file(), "LIVE_IMPLEMENTATION_ARTIFACT_MISSING", {"path": path})
    return p.read_text()


def find_operation(operation_plans, operation_id):
    matches = [
        item
        for item in operation_plans.get("operations", [])
        if item.get("operationId") == operation_id
    ]
    require(len(matches) == 1, "LIVE_GENERATED_OPERATION_NOT_UNIQUE", {"operationId": operation_id, "count": len(matches)})
    return matches[0]


def verify(
    protocol,
    case,
    cg24_protocol,
    cg24_plan,
    governance_root,
    biz_root,
    issue,
    historical_run,
):
    require(protocol.get("schemaVersion") == 1, "PROTOCOL_SCHEMA_MISMATCH")
    require(protocol.get("id") == "CG25-REAL-CONSUMER-AUTHORIZATION-READINESS", "WRONG_PROTOCOL")
    require(protocol.get("status") in {"CANDIDATE", "QUALIFIED"}, "UNEXPECTED_PROTOCOL_STATUS")

    source = protocol.get("source", {})
    require(source.get("protocolId") == "CG24-REAL-CONSUMER-CONTROLLED-PLAN", "CG24_PROTOCOL_ID_MISMATCH")
    for authority in source.get("authorities", []):
        path = authority.get("path")
        blob = authority.get("blob")
        require(path and blob, "CG24_AUTHORITY_INVALID")
        require(
            git(governance_root, "rev-parse", "HEAD:" + path) == blob,
            "CG24_AUTHORITY_BLOB_MISMATCH",
            {"path": path},
        )

    require(cg24_protocol.get("id") == source["protocolId"], "CG24_PROTOCOL_ID_MISMATCH")
    require(cg24_protocol.get("status") == "QUALIFIED", "CG24_PROTOCOL_NOT_QUALIFIED")
    require(cg24_plan.get("protocolId") == source["protocolId"], "CG24_PLAN_PROTOCOL_MISMATCH")
    require(cg24_plan.get("planId") == source.get("planId"), "CG24_PLAN_ID_MISMATCH")
    require(cg24_plan.get("decision") == "PLAN_READY_FOR_CONTROLLED_IMPLEMENTATION", "CG24_PLAN_DECISION_MISMATCH")
    require(cg24_plan.get("executionAuthorized") is False, "CG24_EXECUTION_ALREADY_AUTHORIZED")

    planned_base = source.get("plannedConsumerBase", {})
    require(planned_base.get("repository") == "hvritual/biz", "PLANNED_REPOSITORY_MISMATCH")
    require(planned_base.get("sha") == cg24_plan.get("consumer", {}).get("baseSha"), "PLANNED_BASE_SHA_MISMATCH")
    require(planned_base.get("tree") == cg24_plan.get("consumer", {}).get("baseTree"), "PLANNED_BASE_TREE_MISMATCH")
    require(planned_base.get("sha") == cg24_protocol.get("consumer", {}).get("baseSha"), "CG24_PROTOCOL_BASE_SHA_MISMATCH")

    planned_surface = source.get("plannedSurface", {})
    cg24_surface = cg24_plan.get("surface", {})
    for field in ("operationId", "useCase", "httpMethod", "httpPath"):
        require(planned_surface.get(field) == cg24_surface.get(field), "PLANNED_SURFACE_MISMATCH", {"field": field})

    live = protocol.get("liveConsumer", {})
    require(live.get("repository") == "hvritual/biz", "LIVE_REPOSITORY_MISMATCH")
    require(git(biz_root, "rev-parse", "HEAD") == live.get("sha"), "LIVE_CONSUMER_SHA_MISMATCH")
    require(git(biz_root, "rev-parse", "HEAD^{tree}") == live.get("tree"), "LIVE_CONSUMER_TREE_MISMATCH")
    require(issue.get("number") == live.get("sourceIssue"), "SOURCE_ISSUE_NUMBER_MISMATCH")
    require(issue.get("state") == live.get("requiredIssueState"), "SOURCE_ISSUE_STATE_MISMATCH")

    authorities = live.get("authorities", [])
    require(len(authorities) >= 5, "LIVE_AUTHORITY_SET_INCOMPLETE")
    for authority in authorities:
        path = authority.get("path")
        blob = authority.get("blob")
        require(path and blob, "LIVE_AUTHORITY_INVALID")
        require(
            git(biz_root, "rev-parse", "HEAD:" + path) == blob,
            "LIVE_AUTHORITY_BLOB_MISMATCH",
            {"path": path},
        )

    observed = live.get("observedSurface", {})
    proto = read(biz_root, "contracts/proto/commercial/v1/plan.proto")
    ports = read(biz_root, "internal/commercial/ports/plan.go")
    persistence = read(biz_root, "internal/commercial/infrastructure/persistence/plan_catalog.go")
    usecase = read(biz_root, "internal/commercial/application/planmanagement/internal/usecase/catalog.go")
    operation_plans = json.loads(read(biz_root, "contracts/generated/operation-plans.json"))

    required_proto_markers = [
        "rpc ListPlans(ListPlansRequest) returns (ListPlansResponse)",
        'get: "/v1/platform/plans"',
        'id: "' + observed.get("operationId", "") + '"',
        'use_case: "' + observed.get("useCase", "") + '"',
        'permissions: "platform.plan.read"',
        "authentication: AUTHENTICATION_API_KEY",
        "authentication: AUTHENTICATION_WEB_SESSION",
        "tenant_required: false",
        "transaction: TRANSACTION_READ_ONLY",
        "idempotency: IDEMPOTENCY_NONE",
    ]
    for marker in required_proto_markers:
        require(marker in proto, "LIVE_CONTRACT_MARKER_MISSING", {"marker": marker})

    require("Catalog(context.Context, string, int) ([]plan.Version, error)" in ports, "LIVE_REPOSITORY_CATALOG_PORT_MISSING")
    require('Table("biz_commercial_plans AS p")' in persistence, "LIVE_PLAN_HEAD_AUTHORITY_MISSING")
    require("biz_commercial_plan_versions AS v" in persistence, "LIVE_PLAN_VERSION_AUTHORITY_MISSING")
    require("v.version = p.latest_version" in persistence, "LIVE_LATEST_VERSION_JOIN_MISSING")
    require('Order("p.plan_code ASC")' in persistence, "LIVE_PLAN_CODE_ORDER_MISSING")
    require("func (s *service) ListPlans" in usecase, "LIVE_LIST_PLANS_USECASE_MISSING")
    require("Plans.Catalog" in usecase, "LIVE_CATALOG_REPOSITORY_CALL_MISSING")
    require("req.PageSize > 100" in usecase and "size = 20" in usecase, "LIVE_PAGINATION_BOUNDARY_MISSING")
    require("NextAfterPlanCode" in usecase, "LIVE_CURSOR_RESPONSE_MISSING")

    operation = find_operation(operation_plans, observed.get("operationId"))
    require(operation.get("useCase") == observed.get("useCase"), "LIVE_GENERATED_USECASE_MISMATCH")
    security = operation.get("security", {})
    require(sorted(security.get("authentication", [])) == sorted(observed.get("authentication", [])), "LIVE_GENERATED_AUTHENTICATION_MISMATCH")
    require(security.get("permissions", []) == observed.get("permissions"), "LIVE_GENERATED_PERMISSION_MISMATCH")
    require(security.get("tenantRequired", False) is observed.get("tenantRequired"), "LIVE_GENERATED_TENANT_SCOPE_MISMATCH")
    execution = operation.get("execution", {})
    require(execution.get("transaction") == observed.get("transaction"), "LIVE_GENERATED_TRANSACTION_MISMATCH")
    require(execution.get("idempotency") == observed.get("idempotency"), "LIVE_GENERATED_IDEMPOTENCY_MISMATCH")
    http = operation.get("bindings", {}).get("http", [])
    require(
        any(item.get("method") == observed.get("httpMethod") and item.get("path") == observed.get("httpPath") for item in http),
        "LIVE_GENERATED_HTTP_BINDING_MISMATCH",
    )

    historical = protocol.get("historicalEvidence", {})
    require(historical.get("semanticEquivalenceClaimed") is False, "HISTORICAL_EVIDENCE_OVERCLAIM")
    require(historical_run.get("id") == historical.get("run"), "HISTORICAL_RUN_ID_MISMATCH")
    require(historical_run.get("name") == historical.get("workflow"), "HISTORICAL_WORKFLOW_MISMATCH")
    require(historical_run.get("head_sha") == historical.get("headSha"), "HISTORICAL_HEAD_SHA_MISMATCH")
    require(historical_run.get("status") == historical.get("status"), "HISTORICAL_RUN_STATUS_MISMATCH")
    require(historical_run.get("conclusion") == historical.get("conclusion"), "HISTORICAL_RUN_CONCLUSION_MISMATCH")

    require(planned_base.get("sha") != live.get("sha"), "CONSUMER_BASE_MOVEMENT_NOT_PROVEN")
    require(observed.get("operationId") != planned_surface.get("operationId"), "OPERATION_IDENTITY_DIVERGENCE_NOT_PROVEN")
    require(observed.get("useCase") != planned_surface.get("useCase"), "USECASE_IDENTITY_DIVERGENCE_NOT_PROVEN")

    require(case.get("schemaVersion") == 1, "CASE_SCHEMA_MISMATCH")
    require(case.get("protocolId") == protocol.get("id"), "CASE_PROTOCOL_MISMATCH")
    require(digest(case_core(case)) == case.get("readinessId"), "READINESS_ID_MISMATCH")

    source_case = case.get("sourcePlan", {})
    require(source_case.get("protocolId") == source.get("protocolId"), "CASE_SOURCE_PROTOCOL_MISMATCH")
    require(source_case.get("planId") == source.get("planId"), "CASE_SOURCE_PLAN_ID_MISMATCH")
    require(source_case.get("consumerBaseSha") == planned_base.get("sha"), "CASE_SOURCE_BASE_SHA_MISMATCH")
    require(source_case.get("consumerBaseTree") == planned_base.get("tree"), "CASE_SOURCE_BASE_TREE_MISMATCH")
    require(source_case.get("operationId") == planned_surface.get("operationId"), "CASE_SOURCE_OPERATION_MISMATCH")
    require(source_case.get("useCase") == planned_surface.get("useCase"), "CASE_SOURCE_USECASE_MISMATCH")

    live_case = case.get("liveConsumer", {})
    require(live_case.get("repository") == live.get("repository"), "CASE_LIVE_REPOSITORY_MISMATCH")
    require(live_case.get("sha") == live.get("sha"), "CASE_LIVE_SHA_MISMATCH")
    require(live_case.get("tree") == live.get("tree"), "CASE_LIVE_TREE_MISMATCH")
    require(live_case.get("sourceIssue") == live.get("sourceIssue"), "CASE_LIVE_ISSUE_MISMATCH")
    require(live_case.get("sourceIssueState") == issue.get("state"), "CASE_LIVE_ISSUE_STATE_MISMATCH")
    require(live_case.get("operationId") == observed.get("operationId"), "CASE_LIVE_OPERATION_MISMATCH")
    require(live_case.get("useCase") == observed.get("useCase"), "CASE_LIVE_USECASE_MISMATCH")
    require(live_case.get("httpBinding") == observed.get("httpMethod") + " " + observed.get("httpPath"), "CASE_LIVE_HTTP_BINDING_MISMATCH")

    observations = case.get("observations", {})
    require(observations.get("consumerBaseMoved") is True, "BASE_MOVED_OBSERVATION_REQUIRED")
    require(observations.get("targetImplementationPresent") is True, "IMPLEMENTATION_PRESENT_OBSERVATION_REQUIRED")
    require(observations.get("operationIdentityDiverged") is True, "IDENTITY_DIVERGENCE_OBSERVATION_REQUIRED")
    require(observations.get("issueOpenDoesNotAuthorizeExecution") is True, "OPEN_ISSUE_AUTHORITY_BOUNDARY_REQUIRED")
    require(observations.get("semanticEquivalenceDecided") is False, "SEMANTIC_EQUIVALENCE_MUST_REMAIN_UNDECIDED")

    require(case.get("decision") == protocol.get("requiredDecision"), "READINESS_DECISION_INVALID")
    require(case.get("disposition") == protocol.get("requiredDisposition"), "READINESS_DISPOSITION_INVALID")
    require(case.get("reasons") == protocol.get("requiredReasons"), "READINESS_REASON_SET_MISMATCH")

    case_historical = case.get("historicalEvidence", {})
    require(case_historical.get("run") == historical.get("run"), "CASE_HISTORICAL_RUN_MISMATCH")
    require(case_historical.get("workflow") == historical.get("workflow"), "CASE_HISTORICAL_WORKFLOW_MISMATCH")
    require(case_historical.get("headSha") == historical.get("headSha"), "CASE_HISTORICAL_HEAD_MISMATCH")
    require(case_historical.get("conclusion") == historical.get("conclusion"), "CASE_HISTORICAL_CONCLUSION_MISMATCH")
    require(case_historical.get("role") == historical.get("role"), "CASE_HISTORICAL_ROLE_MISMATCH")
    require(case_historical.get("semanticEquivalenceClaimed") is False, "HISTORICAL_EVIDENCE_OVERCLAIM")

    false_fields = (
        "authorizationGranted",
        "createsConsumerBranch",
        "modifiesConsumer",
        "authorizesPatch",
        "authorizesCommit",
        "authorizesPush",
        "authorizesPullRequest",
        "authorizesMerge",
        "modifiesFramework",
        "repositoryAdministration",
        "consumerAdoption",
        "repositoryBlocking",
    )
    authority = case.get("authority", {})
    ceiling = protocol.get("authorityCeiling", {})
    for field in false_fields:
        require(authority.get(field) is False, "AUTHORIZATION_ESCALATION", {"field": field})
        require(ceiling.get(field) is False, "PROTOCOL_AUTHORITY_CEILING_INVALID", {"field": field})

    return {
        "schemaVersion": 1,
        "status": "AUTHORIZATION_READINESS_QUALIFIED",
        "decision": "AUTHORIZATION_NOT_GRANTED",
        "disposition": "LIVE_RECONCILIATION_REQUIRED",
        "readinessId": case["readinessId"],
        "sourcePlanId": source["planId"],
        "repository": live["repository"],
        "plannedBaseSha": planned_base["sha"],
        "liveBaseSha": live["sha"],
        "liveBaseTree": live["tree"],
        "sourceIssue": live["sourceIssue"],
        "sourceIssueState": issue["state"],
        "plannedOperationId": planned_surface["operationId"],
        "liveOperationId": observed["operationId"],
        "plannedUseCase": planned_surface["useCase"],
        "liveUseCase": observed["useCase"],
        "reasons": protocol["requiredReasons"],
        "authorizationGranted": False,
        "consumerMutation": False,
        "semanticEquivalenceDecided": False,
        "historicalQualificationCorroborated": True,
        "nextDisposition": "LIVE_RECONCILIATION_REQUIRED",
    }


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--protocol", required=True)
    p.add_argument("--case", required=True)
    p.add_argument("--cg24-protocol", required=True)
    p.add_argument("--cg24-plan", required=True)
    p.add_argument("--governance-root", required=True)
    p.add_argument("--biz-root", required=True)
    p.add_argument("--issue-json", required=True)
    p.add_argument("--historical-run-json", required=True)
    args = p.parse_args()
    try:
        result = verify(
            load(args.protocol),
            load(args.case),
            load(args.cg24_protocol),
            load(args.cg24_plan),
            Path(args.governance_root),
            Path(args.biz_root),
            load(args.issue_json),
            load(args.historical_run_json),
        )
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    except ReadinessError as exc:
        result = {
            "schemaVersion": 1,
            "status": "REJECTED",
            "decision": "AUTHORIZATION_NOT_GRANTED",
            "reason": exc.reason,
        }
        if exc.detail is not None:
            result["detail"] = exc.detail
        print(json.dumps(result, indent=2, sort_keys=True))
        return 2


if __name__ == "__main__":
    sys.exit(main())
