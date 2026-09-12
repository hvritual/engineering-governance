#!/usr/bin/env python3
import argparse
import json
import os
import sys
from pathlib import Path

from authorize_merge import (
    MergeAuthorizationError,
    authorization_core,
    authorize,
    canonical_digest,
    load,
)


class MergeAuthorizationVerificationError(Exception):
    def __init__(self, reason, detail=None):
        super().__init__(reason)
        self.reason = reason
        self.detail = detail


def require(condition, reason, detail=None):
    if not condition:
        raise MergeAuthorizationVerificationError(reason, detail)


def verify(protocol, cg18_protocol, source, human_decision, authorization, token, api_url="https://api.github.com"):
    require(isinstance(authorization, dict), "MERGE_AUTHORIZATION_REQUIRED")
    require(authorization.get("protocolId") == protocol.get("id"), "MERGE_AUTHORIZATION_PROTOCOL_MISMATCH")
    require(authorization.get("status") == protocol["outputs"]["status"], "MERGE_AUTHORIZATION_STATUS_INVALID")
    require(authorization.get("decision") == protocol["outputs"]["decision"], "MERGE_AUTHORIZATION_DECISION_INVALID")
    require(canonical_digest(authorization_core(authorization)) == authorization.get("authorizationId"), "MERGE_AUTHORIZATION_ID_MISMATCH")
    require(authorization.get("mergeAuthorization") == "GRANTED_ONCE", "MERGE_AUTHORIZATION_GRANT_INVALID")
    require(authorization.get("authorizationUseLimit") == 1, "MERGE_AUTHORIZATION_USE_LIMIT_INVALID")
    require(authorization.get("consumptionState") == "NOT_CONSUMED", "MERGE_AUTHORIZATION_ALREADY_CONSUMED")
    require(authorization.get("executesMerge") is False, "MERGE_EXECUTION_AUTHORITY_FORBIDDEN")
    require(authorization.get("autoMergeAuthorization") == "NOT_GRANTED", "AUTO_MERGE_AUTHORIZATION_FORBIDDEN")
    require(authorization.get("pullRequestUpdateAuthorization") == "NOT_GRANTED", "PULL_REQUEST_UPDATE_AUTHORIZATION_FORBIDDEN")
    require(authorization.get("reviewSubmissionAuthorization") == "NOT_GRANTED", "REVIEW_SUBMISSION_AUTHORIZATION_FORBIDDEN")
    require(authorization.get("directMainUpdate") is False, "DIRECT_MAIN_UPDATE_FORBIDDEN")
    require(authorization.get("repositoryAdministration") is False, "REPOSITORY_ADMINISTRATION_FORBIDDEN")
    require(authorization.get("consumerAdoption") is False, "CONSUMER_ADOPTION_FORBIDDEN")
    require(authorization.get("repositoryBlocking") is False, "REPOSITORY_BLOCKING_AUTHORITY_FORBIDDEN")

    try:
        expected = authorize(protocol, cg18_protocol, source, human_decision, token, api_url)
    except MergeAuthorizationError as exc:
        raise MergeAuthorizationVerificationError(exc.reason, exc.detail)

    fields = [
        "caseId", "repository", "pullRequestNumber", "baseBranch", "baseSha", "headBranch", "headSha",
        "expectedHeadSha", "changedPaths", "verificationClasses", "sourceQualificationAttestationId",
        "sourceQualificationAttestationSha256", "humanDecisionId", "humanDecisionSha256", "authorizer",
        "decisionNonce", "mergeMethod", "mergeAuthorization", "authorizationUseLimit", "consumptionState",
        "oneTimeEnforcementBoundary", "executesMerge", "reviewSubmissionAuthorization",
        "pullRequestUpdateAuthorization", "autoMergeAuthorization", "directMainUpdate", "repositoryAdministration",
        "consumerAdoption", "repositoryBlocking"
    ]
    for field in fields:
        require(authorization.get(field) == expected.get(field), "MERGE_AUTHORIZATION_BINDING_MISMATCH", {"field": field, "expected": expected.get(field), "actual": authorization.get(field)})
    require(authorization.get("liveQualificationSnapshotSha256") == expected.get("liveQualificationSnapshotSha256"), "LIVE_QUALIFICATION_SNAPSHOT_DRIFT")
    require(authorization.get("authorizationId") == expected.get("authorizationId"), "MERGE_AUTHORIZATION_RECOMPUTE_MISMATCH")

    core = {
        "schemaVersion": 1,
        "protocolId": protocol["id"],
        "authorizationId": authorization["authorizationId"],
        "authorizationSha256": canonical_digest(authorization),
        "repository": authorization["repository"],
        "pullRequestNumber": authorization["pullRequestNumber"],
        "baseSha": authorization["baseSha"],
        "headSha": authorization["headSha"],
        "mergeMethod": authorization["mergeMethod"],
        "mergeAuthorization": "GRANTED_ONCE",
        "consumptionState": "NOT_CONSUMED",
        "executesMerge": False,
    }
    return {
        **core,
        "status": "MERGE_AUTHORIZATION_VERIFIED",
        "decision": "CONTROLLED_MERGE_AUTHORIZATION_VERIFICATION_PASSED",
        "verificationId": canonical_digest(core),
    }


def parse_args():
    parser = argparse.ArgumentParser(description="CG-19 controlled merge authorization verifier")
    parser.add_argument("--protocol", required=True)
    parser.add_argument("--cg18-protocol", required=True)
    parser.add_argument("--source-qualification", required=True)
    parser.add_argument("--human-decision", required=True)
    parser.add_argument("--authorization", required=True)
    parser.add_argument("--api-url", default=os.environ.get("GITHUB_API_URL", "https://api.github.com"))
    return parser.parse_args()


def main():
    args = parse_args()
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or ""
    try:
        out = verify(load(args.protocol), load(args.cg18_protocol), load(args.source_qualification), load(args.human_decision), load(args.authorization), token, args.api_url)
        print(json.dumps(out, indent=2, sort_keys=True))
        return 0
    except MergeAuthorizationVerificationError as exc:
        out = {"schemaVersion": 1, "status": "REJECTED", "decision": "MERGE_AUTHORIZATION_VERIFICATION_FAILED", "reason": exc.reason}
        if exc.detail is not None:
            out["detail"] = exc.detail
        print(json.dumps(out, indent=2, sort_keys=True))
        return 2


if __name__ == "__main__":
    sys.exit(main())
