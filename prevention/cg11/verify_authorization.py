#!/usr/bin/env python3
import argparse
import importlib.util
import json
import sys
from pathlib import Path


class VerificationError(Exception):
    def __init__(self, reason, detail=None):
        super().__init__(reason)
        self.reason = reason
        self.detail = detail


def require(condition, reason, detail=None):
    if not condition:
        raise VerificationError(reason, detail)


def load(path):
    return json.loads(Path(path).read_text())


def load_builder(path):
    spec = importlib.util.spec_from_file_location("cg11_build_authorization", path)
    require(spec is not None and spec.loader is not None, "BUILDER_LOAD_FAILED")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def observed_by_target(observed):
    targets = observed.get("targets", [])
    mapping = {}
    for item in targets:
        key = (item.get("repository"), item.get("baseBranch"))
        require(key not in mapping, "DUPLICATE_OBSERVED_TARGET", {"target": key})
        mapping[key] = item
    return mapping


def verify_document(protocol, plans_doc, requests_doc, observed, authorizations_doc, builder_module):
    require(protocol.get("schemaVersion") == 1, "PROTOCOL_SCHEMA_MISMATCH")
    require(protocol.get("id") == "CG11-CONTROLLED-IMPLEMENTATION-AUTHORIZATION", "WRONG_PROTOCOL")
    require(protocol.get("status") in {"CANDIDATE", "QUALIFIED"}, "UNEXPECTED_PROTOCOL_STATUS")
    require(authorizations_doc.get("schemaVersion") == 1, "AUTHORIZATION_SCHEMA_MISMATCH")
    require(authorizations_doc.get("protocolId") == protocol.get("id"), "AUTHORIZATION_PROTOCOL_MISMATCH")
    require(authorizations_doc.get("status") == "BUILT", "AUTHORIZATION_DOCUMENT_NOT_BUILT")

    plans = builder_module.plan_by_case(plans_doc)
    requests = requests_doc.get("authorizationRequests", [])
    actuals = authorizations_doc.get("authorizations", [])
    require(len(requests) == len(actuals), "AUTHORIZATION_COUNT_MISMATCH")
    observed_map = observed_by_target(observed)

    authorized_count = 0
    fallback_count = 0
    strategies = set()

    for request, actual in zip(requests, actuals):
        case_id = request.get("caseId")
        require(case_id in plans, "SOURCE_PLAN_NOT_FOUND", {"caseId": case_id})
        plan = plans[case_id]

        require(actual.get("caseId") == case_id, "AUTHORIZATION_CASE_ID_MISMATCH")
        require(actual.get("planSha256") == builder_module.canonical_plan_sha(plan), "PLAN_SHA_MISMATCH")
        require(actual.get("directMainUpdate") is False, "DIRECT_MAIN_UPDATE_FORBIDDEN")
        require(actual.get("mergeAuthorization") == "NOT_GRANTED", "MERGE_AUTHORIZATION_FORBIDDEN")
        require(actual.get("repositoryAdministration") is False, "REPOSITORY_ADMINISTRATION_FORBIDDEN")
        require(actual.get("consumerAdoption") == "NOT_ADOPTED", "CONSUMER_ADOPTION_FORBIDDEN")

        try:
            expected = builder_module.build_one(protocol, plan, request)
        except builder_module.AuthorizationBuildError as exc:
            raise VerificationError(exc.reason, exc.detail) from exc

        require(actual == expected, "AUTHORIZATION_REQUEST_BINDING_MISMATCH", {"caseId": case_id})

        if actual.get("status") == "AUTHORIZED":
            authorized_count += 1
            strategies.add(actual.get("strategy"))
            require(actual.get("grant") == "PATCH_BRANCH_ONLY", "AUTHORIZATION_GRANT_INVALID")
            target = actual.get("target") or {}
            key = (target.get("repository"), target.get("baseBranch"))
            require(key in observed_map, "LIVE_TARGET_OBSERVATION_MISSING", {"target": key})
            obs = observed_map[key]
            require(obs.get("liveSha") == target.get("baseSha"), "LIVE_BASE_SHA_MISMATCH", {
                "repository": key[0],
                "expected": target.get("baseSha"),
                "actual": obs.get("liveSha"),
            })
            existing = set(obs.get("existingPaths", []))
            missing = sorted(set(actual.get("allowedPaths", [])) - existing)
            require(not missing, "AUTHORIZED_PATH_NOT_PRESENT_AT_BASE", {"repository": key[0], "paths": missing})
            require(target.get("workBranch") != target.get("baseBranch"), "WORK_BRANCH_MUST_DIFFER_FROM_BASE")
            require(target.get("baseMustRemainExact") is True, "LIVE_BASE_BINDING_REQUIRED")
            require(target.get("staleWhenBaseMoves") is True, "STALE_BASE_INVALIDATION_REQUIRED")
        elif actual.get("status") == "NOT_AUTHORIZED":
            fallback_count += 1
            require(actual.get("grant") == "NONE", "FALLBACK_GRANT_FORBIDDEN")
            require(actual.get("target") is None, "FALLBACK_TARGET_FORBIDDEN")
            require(actual.get("allowedPaths") == [], "FALLBACK_EXECUTABLE_PATH_FORBIDDEN")
            require(actual.get("requestedChangeClasses") == [], "FALLBACK_CHANGE_CLASS_FORBIDDEN")
        else:
            raise VerificationError("AUTHORIZATION_STATUS_INVALID", {"status": actual.get("status")})

    qual = protocol.get("qualification", {})
    require(authorized_count >= qual.get("minimumAuthorizedRealPlans", 0), "AUTHORIZED_REAL_PLAN_COUNT_TOO_LOW")
    if qual.get("requireFallbackNotAuthorized"):
        require(fallback_count >= 1, "FALLBACK_NOT_AUTHORIZED_MISSING")

    return {
        "schemaVersion": 1,
        "status": "PASS",
        "decision": "CONTROLLED_IMPLEMENTATION_AUTHORIZATIONS_VERIFIED",
        "authorizationCount": len(actuals),
        "authorizedRealPlanCount": authorized_count,
        "fallbackNotAuthorizedCount": fallback_count,
        "strategies": sorted(strategies),
        "grant": "PATCH_BRANCH_ONLY",
        "mergeAuthorization": False,
        "directMainUpdate": False,
        "repositoryAdministration": False,
        "consumerAdoption": False,
        "repositoryBlocking": False,
    }


def parse_args():
    parser = argparse.ArgumentParser(description="CG-11 deterministic implementation authorization verifier")
    parser.add_argument("--protocol", required=True)
    parser.add_argument("--plans", required=True)
    parser.add_argument("--requests", required=True)
    parser.add_argument("--observed", required=True)
    parser.add_argument("--authorizations", required=True)
    parser.add_argument("--builder", required=True)
    return parser.parse_args()


def main():
    args = parse_args()
    try:
        value = verify_document(
            load(args.protocol),
            load(args.plans),
            load(args.requests),
            load(args.observed),
            load(args.authorizations),
            load_builder(args.builder),
        )
        print(json.dumps(value, indent=2, sort_keys=True))
        return 0
    except VerificationError as exc:
        value = {
            "schemaVersion": 1,
            "status": "REJECTED",
            "decision": "CONTROLLED_IMPLEMENTATION_AUTHORIZATION_NOT_PROVEN",
            "reason": exc.reason,
        }
        if exc.detail is not None:
            value["detail"] = exc.detail
        print(json.dumps(value, indent=2, sort_keys=True))
        return 2


if __name__ == "__main__":
    sys.exit(main())
