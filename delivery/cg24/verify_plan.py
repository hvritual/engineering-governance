#!/usr/bin/env python3
import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path, PurePosixPath


class PlanVerificationError(Exception):
    def __init__(self, reason, detail=None):
        super().__init__(reason)
        self.reason = reason
        self.detail = detail


def require(condition, reason, detail=None):
    if not condition:
        raise PlanVerificationError(reason, detail)


def load(path):
    return json.loads(Path(path).read_text())


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")).hexdigest()


def plan_core(plan):
    return {k: v for k, v in plan.items() if k != "planId"}


def git(root, *args):
    cp = subprocess.run(["git", "-C", str(root), *args], check=False, capture_output=True, text=True)
    if cp.returncode != 0:
        raise PlanVerificationError("GIT_READBACK_FAILED", {"args": list(args), "stderr": cp.stderr[-1000:]})
    return cp.stdout.strip()


def canonical_paths(values, reason):
    require(isinstance(values, list) and values, reason)
    out = []
    for value in values:
        require(isinstance(value, str) and value, reason, {"path": value})
        p = PurePosixPath(value)
        require(not p.is_absolute() and ".." not in p.parts and p.as_posix() == value, reason, {"path": value})
        out.append(value)
    require(len(out) == len(set(out)), reason, {"problem": "duplicate"})
    return out


def verify(protocol, plan, cg23_case, biz_root, issue):
    require(protocol.get("schemaVersion") == 1, "PROTOCOL_SCHEMA_MISMATCH")
    require(protocol.get("id") == "CG24-REAL-CONSUMER-CONTROLLED-PLAN", "WRONG_PROTOCOL")
    require(protocol.get("status") in {"CANDIDATE", "QUALIFIED"}, "UNEXPECTED_PROTOCOL_STATUS")
    require(protocol.get("sourceCase") == "CG23-BIZ-PLAN-CATALOG-DISCOVERABILITY", "SOURCE_CASE_MISMATCH")

    require(isinstance(cg23_case, dict), "CG23_CASE_REQUIRED")
    require(cg23_case.get("caseId") == protocol["sourceCase"], "CG23_CASE_ID_MISMATCH")
    require(cg23_case.get("status") == "QUALIFIED", "CG23_CASE_NOT_QUALIFIED")
    require(cg23_case.get("decisionBoundary", {}).get("nextDisposition") == "CONTROLLED_PLAN_REQUIRED", "CG23_DISPOSITION_MISMATCH")
    require(cg23_case.get("decisionBoundary", {}).get("frameworkDefectClaimed") is False, "UNSUPPORTED_FRAMEWORK_DEFECT_CLAIM")

    expected_consumer = protocol["consumer"]
    require(git(biz_root, "rev-parse", "HEAD") == expected_consumer["baseSha"], "CONSUMER_BASE_SHA_MISMATCH")
    require(git(biz_root, "rev-parse", "HEAD^{tree}") == expected_consumer["baseTree"], "CONSUMER_BASE_TREE_MISMATCH")
    require(issue.get("number") == expected_consumer["sourceIssue"], "SOURCE_ISSUE_NUMBER_MISMATCH")
    require(issue.get("state") == expected_consumer["requiredIssueState"], "SOURCE_ISSUE_NOT_OPEN")

    for item in protocol.get("consumerAuthorities", []):
        path, expected_blob = item["path"], item["blob"]
        require(git(biz_root, "rev-parse", "HEAD:" + path) == expected_blob, "CONSUMER_AUTHORITY_BLOB_MISMATCH", {"path": path})

    require(plan.get("schemaVersion") == 1, "PLAN_SCHEMA_MISMATCH")
    require(plan.get("protocolId") == protocol["id"], "PLAN_PROTOCOL_MISMATCH")
    require(plan.get("sourceCase") == protocol["sourceCase"], "PLAN_SOURCE_CASE_MISMATCH")
    require(plan.get("decision") == protocol["requiredDecision"], "PLAN_DECISION_INVALID")
    require(plan.get("executionAuthorized") is False, "EXECUTION_AUTHORIZATION_FORBIDDEN")
    require(digest(plan_core(plan)) == plan.get("planId"), "PLAN_ID_MISMATCH")

    consumer = plan.get("consumer", {})
    require(consumer.get("repository") == expected_consumer["repository"], "PLAN_CONSUMER_REPOSITORY_MISMATCH")
    require(consumer.get("baseSha") == expected_consumer["baseSha"], "PLAN_CONSUMER_BASE_MISMATCH")
    require(consumer.get("baseTree") == expected_consumer["baseTree"], "PLAN_CONSUMER_TREE_MISMATCH")
    require(consumer.get("sourceIssue") == expected_consumer["sourceIssue"], "PLAN_SOURCE_ISSUE_MISMATCH")

    policy = protocol["planningPolicy"]
    surface = plan.get("surface", {})
    require(surface.get("operationId") == policy["requiredOperationId"], "CATALOG_OPERATION_ID_MISMATCH")
    require(surface.get("useCase") == policy["requiredUseCase"], "CATALOG_USE_CASE_MISMATCH")
    require(surface.get("httpMethod") == policy["requiredHttpMethod"], "CATALOG_HTTP_METHOD_MISMATCH")
    require(surface.get("httpPath") == policy["requiredHttpPath"], "CATALOG_HTTP_PATH_MISMATCH")
    require(surface.get("tenantRequired") is policy["tenantRequired"], "CATALOG_TENANT_SCOPE_MISMATCH")
    require(sorted(surface.get("authentication", [])) == sorted(policy["requiredAuthentication"]), "CATALOG_AUTHENTICATION_MISMATCH")
    require(surface.get("permissions") == [policy["requiredPermission"]], "CATALOG_PERMISSION_MISMATCH")
    require(surface.get("transaction") == policy["transaction"], "CATALOG_TRANSACTION_MISMATCH")
    require(surface.get("idempotency") == policy["idempotency"], "CATALOG_IDEMPOTENCY_MISMATCH")

    pagination = plan.get("pagination", {})
    require(pagination.get("cursorField") == "after_plan_code", "PAGINATION_CURSOR_MISMATCH")
    require(pagination.get("ordering") == ["plan_code ASC"], "PAGINATION_ORDER_MISMATCH")
    require(pagination.get("defaultPageSize") == policy["defaultPageSize"], "PAGINATION_DEFAULT_SIZE_MISMATCH")
    require(pagination.get("maximumPageSize") == policy["maximumPageSize"], "PAGINATION_MAX_SIZE_MISMATCH")

    projection = plan.get("projection", {})
    require(projection.get("sourceOfTruth") == "existing plan aggregate facts", "CATALOG_SOT_MISMATCH")
    require(projection.get("authoritativeTables") == policy["authoritativeTables"], "AUTHORITATIVE_TABLE_SET_MISMATCH")
    require(projection.get("identityTable") == "biz_commercial_plans", "PLAN_IDENTITY_TABLE_MISMATCH")
    require(projection.get("detailTable") == "biz_commercial_plan_versions", "PLAN_DETAIL_TABLE_MISMATCH")
    require(projection.get("newAuthoritativeStore") is False, "SECOND_CATALOG_SOT_FORBIDDEN")
    require(projection.get("browserCatalog") is False, "BROWSER_CATALOG_FORBIDDEN")
    require(projection.get("subscriptionInference") is False, "SUBSCRIPTION_INFERENCE_FORBIDDEN")
    require(projection.get("preservePublishedVersionImmutability") is True, "PUBLISHED_VERSION_IMMUTABILITY_REQUIRED")

    handwritten = canonical_paths(plan.get("handwrittenPaths"), "HANDWRITTEN_PATH_SET_INVALID")
    expected_handwritten = [
        "contracts/proto/commercial/v1/plan.proto",
        "internal/commercial/ports/plan.go",
        "internal/commercial/infrastructure/persistence/plan.go",
        "internal/commercial/application/planmanagement/internal/usecase/dto.go",
        "internal/commercial/application/planmanagement/internal/usecase/service.go",
        "internal/commercial/application/planmanagement/internal/usecase/catalog_test.go",
        "internal/commercial/infrastructure/persistence/plan_catalog_test.go",
    ]
    require(handwritten == expected_handwritten, "HANDWRITTEN_SCOPE_DRIFT")
    for path in handwritten:
        require(not path.startswith("contracts/generated/"), "GENERATED_HAND_EDIT_SCOPE_FORBIDDEN", {"path": path})
        require(not path.startswith("contracts/gen/"), "GENERATED_HAND_EDIT_SCOPE_FORBIDDEN", {"path": path})
        require("/zz_yunka_" not in path, "GENERATED_HAND_EDIT_SCOPE_FORBIDDEN", {"path": path})

    generated = plan.get("generatedOwnership", [])
    require("contracts/generated/**" in generated and "contracts/gen/commercial/v1/**" in generated, "GENERATED_OWNERSHIP_INCOMPLETE")
    generated_policy = plan.get("generatedPolicy", {})
    require(generated_policy.get("handEdit") is False, "GENERATED_HAND_EDIT_FORBIDDEN")
    require(generated_policy.get("canonicalGenerationRequired") is True, "CANONICAL_GENERATION_REQUIRED")
    require(generated_policy.get("secondGenerationZeroTrackedDrift") is True, "SECOND_GENERATION_DRIFT_CHECK_REQUIRED")
    require(generated_policy.get("actualGeneratedDiffMustBeReconciledBeforeAuthorization") is True, "GENERATED_DIFF_RECONCILIATION_REQUIRED")

    required_classes = set(protocol["requiredVerificationClasses"])
    actual_classes = set(plan.get("verificationClasses", []))
    require(required_classes == actual_classes, "VERIFICATION_CLASS_SET_MISMATCH", {"missing": sorted(required_classes - actual_classes), "extra": sorted(actual_classes - required_classes)})

    authority = plan.get("authority", {})
    require(authority.get("planningOnly") is True, "PLANNING_ONLY_REQUIRED")
    for field in ("createsConsumerBranch", "modifiesConsumer", "authorizesPatch", "authorizesCommit", "authorizesPush", "authorizesPullRequest", "authorizesMerge", "modifiesFramework", "repositoryAdministration", "consumerAdoption", "repositoryBlocking"):
        require(authority.get(field) is False, "PLAN_AUTHORITY_ESCALATION", {"field": field})

    ceiling = protocol["authorityCeiling"]
    require(ceiling.get("planningOnly") is True and ceiling.get("executionAuthorized") is False, "PROTOCOL_AUTHORITY_CEILING_INVALID")
    require(policy.get("frameworkModificationAllowed") is False, "FRAMEWORK_MODIFICATION_NOT_JUSTIFIED")
    require(policy.get("generatedHandEditAllowed") is False, "GENERATED_HAND_EDIT_NOT_ALLOWED")

    return {
        "schemaVersion": 1,
        "status": "CONTROLLED_PLAN_QUALIFIED",
        "decision": "PLAN_READY_FOR_CONTROLLED_IMPLEMENTATION",
        "caseId": protocol["sourceCase"],
        "repository": expected_consumer["repository"],
        "consumerBaseSha": expected_consumer["baseSha"],
        "consumerBaseTree": expected_consumer["baseTree"],
        "sourceIssue": expected_consumer["sourceIssue"],
        "planId": plan["planId"],
        "operationId": surface["operationId"],
        "httpBinding": surface["httpMethod"] + " " + surface["httpPath"],
        "handwrittenPathCount": len(handwritten),
        "verificationClassCount": len(actual_classes),
        "executionAuthorized": False,
        "consumerMutation": False,
        "frameworkModificationAuthorized": False,
        "nextDisposition": "CONTROLLED_IMPLEMENTATION_AUTHORIZATION_REQUIRED"
    }


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--protocol", required=True)
    p.add_argument("--plan", required=True)
    p.add_argument("--cg23-case", required=True)
    p.add_argument("--biz-root", required=True)
    p.add_argument("--issue-json", required=True)
    args = p.parse_args()
    try:
        out = verify(load(args.protocol), load(args.plan), load(args.cg23_case), Path(args.biz_root), load(args.issue_json))
        print(json.dumps(out, indent=2, sort_keys=True))
        return 0
    except PlanVerificationError as exc:
        out = {"schemaVersion": 1, "status": "REJECTED", "decision": "CONTROLLED_PLAN_NOT_QUALIFIED", "reason": exc.reason}
        if exc.detail is not None:
            out["detail"] = exc.detail
        print(json.dumps(out, indent=2, sort_keys=True))
        return 2


if __name__ == "__main__":
    sys.exit(main())
