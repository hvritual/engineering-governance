#!/usr/bin/env python3
import argparse
import json
import subprocess
import sys
from pathlib import Path


class QualificationError(Exception):
    def __init__(self, reason, detail=None):
        super().__init__(reason)
        self.reason = reason
        self.detail = detail


def require(condition, reason, detail=None):
    if not condition:
        raise QualificationError(reason, detail)


def load_json(path):
    return json.loads(Path(path).read_text())


def git(root, *args):
    cp = subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=True,
    )
    require(cp.returncode == 0, "GIT_COMMAND_FAILED", {"args": list(args), "stderr": cp.stderr[-2000:]})
    return cp.stdout.strip()


def exact_http_bindings(operation):
    bindings = operation.get("bindings") or {}
    items = bindings.get("http") or []
    out = []
    for item in items:
        if isinstance(item, dict):
            out.append((item.get("method"), item.get("path")))
    return out


def qualify(case, biz_root, issue):
    baseline = case["consumerBaseline"]
    files = case["authoritativeFiles"]

    actual_head = git(biz_root, "rev-parse", "HEAD")
    require(actual_head == baseline["commit"], "CONSUMER_BASE_SHA_MISMATCH", {
        "expected": baseline["commit"], "actual": actual_head,
    })
    actual_tree = git(biz_root, "rev-parse", "HEAD^{tree}")
    require(actual_tree == baseline["tree"], "CONSUMER_BASE_TREE_MISMATCH", {
        "expected": baseline["tree"], "actual": actual_tree,
    })

    for name, spec in files.items():
        actual_blob = git(biz_root, "rev-parse", f"HEAD:{spec['path']}")
        require(actual_blob == spec["gitBlobSha"], "AUTHORITATIVE_FILE_BLOB_MISMATCH", {
            "name": name, "path": spec["path"], "expected": spec["gitBlobSha"], "actual": actual_blob,
        })

    origin = case["origin"]
    require(issue.get("number") == origin["issue"], "SOURCE_ISSUE_NUMBER_MISMATCH")
    require(issue.get("state") == origin["issueStateRequired"], "SOURCE_ISSUE_NOT_OPEN", {
        "expected": origin["issueStateRequired"], "actual": issue.get("state"),
    })
    require("plan catalog" in str(issue.get("title", "")).lower(), "SOURCE_ISSUE_IDENTITY_MISMATCH")

    plan_path = biz_root / files["planContract"]["path"]
    plan_text = plan_path.read_text()
    require("rpc ListPlanVersions(" in plan_text, "EXISTING_VERSION_LIST_RPC_MISSING")
    require('id: "commercial.plan.list"' in plan_text, "EXISTING_VERSION_LIST_OPERATION_MISSING")
    require('get: "/v1/platform/plans/{plan_code}/versions"' in plan_text, "EXISTING_VERSION_LIST_HTTP_MISSING")
    require('permissions: "platform.plan.read"' in plan_text, "EXISTING_PLAN_READ_PERMISSION_MISSING")
    require("authentication: AUTHENTICATION_WEB_SESSION" in plan_text, "EXISTING_WEB_SESSION_AUTH_MISSING")

    # The authoritative contract currently has POST /v1/platform/plans for create,
    # but no exact GET /v1/platform/plans discovery binding.
    require('get: "/v1/platform/plans"' not in plan_text, "PLAN_CATALOG_ALREADY_PRESENT_IN_CONTRACT")

    plans = load_json(biz_root / files["operationPlans"]["path"])
    operations = plans.get("operations")
    require(isinstance(operations, list), "OPERATION_PLANS_INVALID")
    by_id = {op.get("operationId"): op for op in operations if isinstance(op, dict) and op.get("operationId")}
    existing = by_id.get(case["existingSurface"]["operationId"])
    require(isinstance(existing, dict), "EXISTING_VERSION_LIST_PLAN_MISSING")
    require(existing.get("useCase") == case["existingSurface"]["useCase"], "EXISTING_VERSION_LIST_USECASE_DRIFT")
    require(
        (case["existingSurface"]["httpMethod"], case["existingSurface"]["httpPath"]) in exact_http_bindings(existing),
        "EXISTING_VERSION_LIST_BINDING_DRIFT",
    )
    security = existing.get("security") or {}
    require(sorted(security.get("authentication") or []) == sorted(case["existingSurface"]["authentication"]), "EXISTING_AUTHENTICATION_DRIFT")
    require(sorted(security.get("permissions") or []) == sorted(case["existingSurface"]["permissions"]), "EXISTING_PERMISSION_DRIFT")

    catalog_matches = []
    for op in operations:
        if not isinstance(op, dict):
            continue
        for method, path in exact_http_bindings(op):
            if method == case["missingSurface"]["requiredHttpMethod"] and path == case["missingSurface"]["requiredHttpPath"]:
                catalog_matches.append(op.get("operationId"))
    require(not catalog_matches, "PLAN_CATALOG_ALREADY_PRESENT_IN_GENERATED_PLAN", {"operations": catalog_matches})
    require("commercial.plan.catalog" not in by_id, "PLAN_CATALOG_OPERATION_ALREADY_PRESENT")

    return {
        "schemaVersion": 1,
        "caseId": case["caseId"],
        "status": "REAL_CONSUMER_PROBLEM_QUALIFIED",
        "decision": "CURRENT_BIZ_CONSUMER_REQUIRES_CONTROLLED_PLAN",
        "classification": case["classification"],
        "repository": origin["repository"],
        "issue": origin["issue"],
        "issueState": issue["state"],
        "consumerBaseSha": actual_head,
        "consumerBaseTree": actual_tree,
        "planContractBlob": files["planContract"]["gitBlobSha"],
        "operationPlansBlob": files["operationPlans"]["gitBlobSha"],
        "existingVersionListOperation": existing.get("operationId"),
        "existingVersionListUseCase": existing.get("useCase"),
        "missingHttpMethod": case["missingSurface"]["requiredHttpMethod"],
        "missingHttpPath": case["missingSurface"]["requiredHttpPath"],
        "qualifiedInvariant": case["qualifiedInvariant"],
        "frameworkDefectClaimed": False,
        "consumerPatchAuthorized": False,
        "consumerBranchCreationAuthorized": False,
        "consumerPullRequestAuthorized": False,
        "consumerMutation": False,
        "nextDisposition": case["decisionBoundary"]["nextDisposition"],
    }


def parse_args():
    parser = argparse.ArgumentParser(description="CG-23 real Biz consumer qualification")
    parser.add_argument("--case", required=True)
    parser.add_argument("--biz-root", required=True)
    parser.add_argument("--issue-json", required=True)
    return parser.parse_args()


def main():
    args = parse_args()
    try:
        out = qualify(load_json(args.case), Path(args.biz_root), load_json(args.issue_json))
        print(json.dumps(out, indent=2, sort_keys=True))
        return 0
    except QualificationError as exc:
        out = {
            "schemaVersion": 1,
            "status": "REJECTED",
            "decision": "REAL_CONSUMER_PROBLEM_NOT_QUALIFIED",
            "reason": exc.reason,
            "consumerMutation": False,
        }
        if exc.detail is not None:
            out["detail"] = exc.detail
        print(json.dumps(out, indent=2, sort_keys=True))
        return 2


if __name__ == "__main__":
    sys.exit(main())
