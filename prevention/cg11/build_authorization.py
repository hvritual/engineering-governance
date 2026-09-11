#!/usr/bin/env python3
import argparse
import hashlib
import json
import re
import sys
from pathlib import Path, PurePosixPath


SHA40 = re.compile(r"^[0-9a-f]{40}$")
WILDCARD_CHARS = set("*?[]{}")


class AuthorizationBuildError(Exception):
    def __init__(self, reason, detail=None):
        super().__init__(reason)
        self.reason = reason
        self.detail = detail


def require(condition, reason, detail=None):
    if not condition:
        raise AuthorizationBuildError(reason, detail)


def load(path):
    return json.loads(Path(path).read_text())


def canonical_plan_sha(plan):
    encoded = json.dumps(plan, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def canonical_paths(paths, forbidden_prefixes):
    require(isinstance(paths, list), "PATH_ALLOWLIST_INVALID")
    result = []
    for item in paths:
        require(isinstance(item, str) and item, "PATH_ALLOWLIST_INVALID", {"path": item})
        require(not any(ch in item for ch in WILDCARD_CHARS), "NON_EXACT_PATH_ALLOWLIST", {"path": item})
        path = PurePosixPath(item)
        require(not path.is_absolute(), "PATH_ALLOWLIST_INVALID", {"path": item})
        require(".." not in path.parts, "PATH_ALLOWLIST_INVALID", {"path": item})
        require(path.as_posix() == item, "PATH_ALLOWLIST_INVALID", {"path": item})
        for prefix in forbidden_prefixes:
            require(not item.startswith(prefix), "FORBIDDEN_PATH", {"path": item, "prefix": prefix})
        result.append(item)
    require(len(result) == len(set(result)), "DUPLICATE_ALLOWED_PATH")
    return sorted(result)


def plan_by_case(plans_doc):
    plans = plans_doc.get("plans", [])
    mapping = {plan.get("caseId"): plan for plan in plans}
    require(len(mapping) == len(plans), "DUPLICATE_PLAN_CASE_ID")
    return mapping


def build_one(protocol, plan, request):
    case_id = request.get("caseId")
    require(case_id == plan.get("caseId"), "CASE_ID_BINDING_MISMATCH")
    plan_sha = canonical_plan_sha(plan)
    fallback = protocol["fallback"]

    if plan.get("planKind") == fallback["sourcePlanKind"] or plan.get("disposition") == fallback["sourcePlanDisposition"]:
        require(request.get("target") is None, "FALLBACK_EXECUTION_REQUEST_FORBIDDEN")
        require(request.get("requestedChangeClasses") == [], "FALLBACK_EXECUTION_REQUEST_FORBIDDEN")
        require(request.get("allowedPaths") == [], "FALLBACK_EXECUTION_REQUEST_FORBIDDEN")
        require(request.get("requiredVerificationClasses") == [], "FALLBACK_EXECUTION_REQUEST_FORBIDDEN")
        return {
            "caseId": case_id,
            "planSha256": plan_sha,
            "strategy": plan.get("strategy"),
            "status": fallback["authorizationStatus"],
            "disposition": fallback["disposition"],
            "grant": fallback["grant"],
            "target": None,
            "requestedChangeClasses": [],
            "allowedPaths": [],
            "requiredVerificationClasses": [],
            "consumerAdoption": fallback["consumerAdoption"],
            "directMainUpdate": False,
            "mergeAuthorization": "NOT_GRANTED",
            "repositoryAdministration": False,
            "missingEvidence": plan.get("missingEvidence", []),
        }

    auth = protocol["authorization"]
    require(plan.get("planKind") == auth["requiredSourcePlanKind"], "SOURCE_PLAN_KIND_NOT_AUTHORIZABLE")
    require(plan.get("disposition") == auth["requiredSourcePlanDisposition"], "SOURCE_PLAN_DISPOSITION_NOT_AUTHORIZABLE")
    require(plan.get("executionAuthorized") is False, "SOURCE_PLAN_ALREADY_EXECUTION_AUTHORIZED")
    require(plan.get("consumerAdoption") == "NOT_ADOPTED", "SOURCE_PLAN_CONSUMER_ADOPTION_INVALID")

    target = request.get("target")
    require(isinstance(target, dict), "TARGET_REQUIRED")
    repository = target.get("repository")
    base_branch = target.get("baseBranch")
    base_sha = target.get("baseSha")
    work_branch = target.get("workBranch")
    require(isinstance(repository, str) and repository.count("/") == 1, "TARGET_REPOSITORY_INVALID")
    require(base_branch == auth["baseBranch"], "TARGET_BASE_BRANCH_INVALID")
    require(isinstance(base_sha, str) and SHA40.fullmatch(base_sha), "TARGET_BASE_SHA_INVALID")
    require(isinstance(work_branch, str) and work_branch, "WORK_BRANCH_REQUIRED")
    require(work_branch != base_branch and work_branch != "main", "WORK_BRANCH_MUST_DIFFER_FROM_BASE")

    requested_classes = request.get("requestedChangeClasses")
    require(isinstance(requested_classes, list) and requested_classes, "REQUESTED_CHANGE_CLASSES_REQUIRED")
    require(len(requested_classes) == len(set(requested_classes)), "DUPLICATE_CHANGE_CLASS")
    allowed_classes = set(plan.get("futureAllowedChangeClasses", []))
    unknown = sorted(set(requested_classes) - allowed_classes)
    require(not unknown, "CHANGE_CLASS_NOT_ALLOWED_BY_PLAN", {"classes": unknown})
    forbidden_mutations = set(auth.get("forbiddenMutationClasses", []))
    forbidden_requested = sorted(set(requested_classes) & forbidden_mutations)
    require(not forbidden_requested, "FORBIDDEN_MUTATION_CLASS", {"classes": forbidden_requested})

    allowed_paths = canonical_paths(request.get("allowedPaths"), auth.get("forbiddenPathPrefixes", []))
    require(allowed_paths, "EXACT_PATH_ALLOWLIST_REQUIRED")

    required_verification = request.get("requiredVerificationClasses")
    require(isinstance(required_verification, list), "VERIFICATION_CLASSES_INVALID")
    require(len(required_verification) == len(set(required_verification)), "DUPLICATE_VERIFICATION_CLASS")
    plan_verification = plan.get("verificationClasses", [])
    require(required_verification == plan_verification, "VERIFICATION_CLASS_DROPPED", {
        "expected": plan_verification,
        "actual": required_verification,
    })

    material = {
        "caseId": case_id,
        "planSha256": plan_sha,
        "repository": repository,
        "baseBranch": base_branch,
        "baseSha": base_sha,
        "workBranch": work_branch,
        "requestedChangeClasses": requested_classes,
        "allowedPaths": allowed_paths,
        "requiredVerificationClasses": required_verification,
    }
    authorization_id = hashlib.sha256(
        json.dumps(material, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()

    return {
        "authorizationId": authorization_id,
        "caseId": case_id,
        "planSha256": plan_sha,
        "strategy": plan.get("strategy"),
        "status": "AUTHORIZED",
        "disposition": "PATCH_BRANCH_AUTHORIZED",
        "grant": auth["grant"],
        "target": {
            "repository": repository,
            "baseBranch": base_branch,
            "baseSha": base_sha,
            "workBranch": work_branch,
            "baseMustRemainExact": auth["requireExactLiveBase"],
            "staleWhenBaseMoves": auth["staleWhenBaseMoves"],
        },
        "requestedChangeClasses": requested_classes,
        "allowedPaths": allowed_paths,
        "requiredVerificationClasses": required_verification,
        "consumerAdoption": auth["consumerAdoption"],
        "directMainUpdate": auth["directMainUpdate"],
        "mergeAuthorization": auth["mergeAuthorization"],
        "repositoryAdministration": auth["repositoryAdministration"],
        "singleUseSemanticIntent": auth["singleUseSemanticIntent"],
    }


def build_document(protocol, plans_doc, requests_doc):
    require(protocol.get("schemaVersion") == 1, "PROTOCOL_SCHEMA_MISMATCH")
    require(protocol.get("id") == "CG11-CONTROLLED-IMPLEMENTATION-AUTHORIZATION", "WRONG_PROTOCOL")
    require(protocol.get("status") in {"CANDIDATE", "QUALIFIED"}, "UNEXPECTED_PROTOCOL_STATUS")
    require(requests_doc.get("schemaVersion") == 1, "REQUEST_SCHEMA_MISMATCH")
    require(requests_doc.get("protocolId") == protocol.get("id"), "REQUEST_PROTOCOL_MISMATCH")
    plans = plan_by_case(plans_doc)
    requests = requests_doc.get("authorizationRequests", [])
    require(isinstance(requests, list) and requests, "NO_AUTHORIZATION_REQUESTS")
    result = []
    for request in requests:
        case_id = request.get("caseId")
        require(case_id in plans, "SOURCE_PLAN_NOT_FOUND", {"caseId": case_id})
        result.append(build_one(protocol, plans[case_id], request))
    return {
        "schemaVersion": 1,
        "protocolId": protocol["id"],
        "status": "BUILT",
        "authorizations": result,
    }


def parse_args():
    parser = argparse.ArgumentParser(description="CG-11 deterministic implementation authorization builder")
    parser.add_argument("--protocol", required=True)
    parser.add_argument("--plans", required=True)
    parser.add_argument("--requests", required=True)
    return parser.parse_args()


def main():
    args = parse_args()
    try:
        doc = build_document(load(args.protocol), load(args.plans), load(args.requests))
        print(json.dumps(doc, indent=2, sort_keys=True))
        return 0
    except AuthorizationBuildError as exc:
        value = {
            "schemaVersion": 1,
            "status": "REJECTED",
            "decision": "IMPLEMENTATION_AUTHORIZATION_NOT_BUILT",
            "reason": exc.reason,
        }
        if exc.detail is not None:
            value["detail"] = exc.detail
        print(json.dumps(value, indent=2, sort_keys=True))
        return 2


if __name__ == "__main__":
    sys.exit(main())
