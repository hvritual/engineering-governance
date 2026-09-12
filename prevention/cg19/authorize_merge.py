#!/usr/bin/env python3
import argparse
import hashlib
import json
import os
import re
import sys
from pathlib import Path

CG18_DIR = Path(__file__).resolve().parents[1] / "cg18"
if str(CG18_DIR) not in sys.path:
    sys.path.insert(0, str(CG18_DIR))

import qualify_pull_request as cg18_qualify
import review_state as cg18_review_state


class MergeAuthorizationError(Exception):
    def __init__(self, reason, detail=None):
        super().__init__(reason)
        self.reason = reason
        self.detail = detail


def require(condition, reason, detail=None):
    if not condition:
        raise MergeAuthorizationError(reason, detail)


def load(path):
    return json.loads(Path(path).read_text())


def canonical_digest(value):
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def qualification_core(attestation):
    return {k: v for k, v in attestation.items() if k not in {"status", "decision", "qualificationAttestationId"}}


def decision_core(decision):
    return {k: v for k, v in decision.items() if k != "decisionId"}


def authorization_core(authorization):
    return {k: v for k, v in authorization.items() if k not in {"status", "decision", "authorizationId"}}


def sorted_unique_strings(value, reason):
    require(isinstance(value, list), reason)
    require(all(isinstance(item, str) and item for item in value), reason)
    require(len(value) == len(set(value)), reason)
    return sorted(value)


def validate_source(protocol, source):
    require(protocol.get("schemaVersion") == 1, "PROTOCOL_SCHEMA_MISMATCH")
    require(protocol.get("id") == "CG19-CONTROLLED-MERGE-AUTHORIZATION", "WRONG_PROTOCOL")
    require(protocol.get("status") in {"CANDIDATE", "QUALIFIED"}, "UNEXPECTED_PROTOCOL_STATUS")
    require(isinstance(source, dict), "CG18_QUALIFICATION_ATTESTATION_REQUIRED")
    require(source.get("protocolId") == protocol.get("sourceProtocol"), "CG18_PROTOCOL_MISMATCH")
    require(source.get("status") == protocol.get("requiredSourceStatus"), "CG18_QUALIFICATION_STATUS_INVALID")
    require(source.get("decision") == protocol.get("requiredSourceDecision"), "CG18_QUALIFICATION_DECISION_INVALID")
    require(canonical_digest(qualification_core(source)) == source.get("qualificationAttestationId"), "CG18_QUALIFICATION_ATTESTATION_ID_MISMATCH")
    require(source.get("provider") == protocol["authorizationPolicy"]["provider"], "PROVIDER_MISMATCH")
    require(re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", source.get("repository", "")) is not None, "GITHUB_REPOSITORY_SLUG_REQUIRED")
    require(isinstance(source.get("pullRequestNumber"), int) and source["pullRequestNumber"] > 0, "PULL_REQUEST_NUMBER_INVALID")
    for field in ("baseSha", "headSha"):
        require(re.fullmatch(r"[0-9a-f]{40}", source.get(field, "")) is not None, "CG18_GIT_ID_INVALID", {"field": field})
    require(isinstance(source.get("baseBranch"), str) and source["baseBranch"], "BASE_BRANCH_REQUIRED")
    require(isinstance(source.get("headBranch"), str) and source["headBranch"], "HEAD_BRANCH_REQUIRED")
    require(source["baseSha"] != source["headSha"], "BASE_HEAD_MUST_DIFFER")
    changed_paths = sorted_unique_strings(source.get("changedPaths"), "CG18_CHANGED_PATHS_INVALID")
    require(changed_paths, "CG18_CHANGED_PATHS_EMPTY")
    verification_classes = sorted_unique_strings(source.get("verificationClasses"), "CG18_VERIFICATION_CLASSES_INVALID")
    require(verification_classes, "CG18_VERIFICATION_CLASSES_EMPTY")
    require(source.get("qualificationReasons") == [], "CG18_QUALIFICATION_REASONS_NOT_EMPTY", source.get("qualificationReasons"))
    require(source.get("mergeable") is True, "CG18_SOURCE_NOT_MERGEABLE")
    require(source.get("mergeAuthorization") == "NOT_GRANTED", "SOURCE_MERGE_AUTHORITY_INVALID")
    for field in ("reviewSubmissionAuthorization", "pullRequestUpdateAuthorization", "autoMergeAuthorization"):
        require(source.get(field) == "NOT_GRANTED", "DOWNSTREAM_AUTHORITY_ESCALATION", {"field": field})
    require(source.get("directMainUpdate") is False, "DIRECT_MAIN_UPDATE_FORBIDDEN")
    require(source.get("repositoryAdministration") is False, "REPOSITORY_ADMINISTRATION_FORBIDDEN")
    require(source.get("consumerAdoption") is False, "CONSUMER_ADOPTION_FORBIDDEN")
    return changed_paths, verification_classes


def validate_human_decision(protocol, source, decision):
    require(isinstance(decision, dict), "HUMAN_DECISION_REQUIRED")
    policy = protocol["authorizationPolicy"]
    require(decision.get("schemaVersion") == 1, "HUMAN_DECISION_SCHEMA_MISMATCH")
    require(decision.get("decisionType") == "HUMAN_MERGE_DECISION", "HUMAN_DECISION_TYPE_INVALID")
    require(decision.get("decisionOrigin") == policy["requiredDecisionOrigin"], "HUMAN_DECISION_ORIGIN_INVALID")
    require(decision.get("action") == policy["requiredHumanAction"], "HUMAN_DECISION_ACTION_INVALID")
    authorizer = decision.get("authorizer")
    require(isinstance(authorizer, str) and authorizer.strip() == authorizer and 1 <= len(authorizer) <= 128, "HUMAN_AUTHORIZER_INVALID")
    require(re.fullmatch(r"[A-Za-z0-9_.@:-]+", authorizer) is not None, "HUMAN_AUTHORIZER_INVALID")
    nonce = decision.get("nonce")
    require(isinstance(nonce, str) and re.fullmatch(policy["decisionNoncePattern"], nonce) is not None, "HUMAN_DECISION_NONCE_INVALID")
    require(decision.get("mergeMethod") in policy["allowedMergeMethods"], "MERGE_METHOD_NOT_ALLOWED")
    bindings = {
        "repository": source["repository"],
        "pullRequestNumber": source["pullRequestNumber"],
        "baseSha": source["baseSha"],
        "headSha": source["headSha"],
        "sourceQualificationAttestationId": source["qualificationAttestationId"],
    }
    for field, expected in bindings.items():
        require(decision.get(field) == expected, "HUMAN_DECISION_BINDING_MISMATCH", {"field": field, "expected": expected, "actual": decision.get(field)})
    require(decision.get("mergeAuthorizationRequested") == policy["grant"], "HUMAN_DECISION_GRANT_MISMATCH")
    require(decision.get("autoMerge") is False, "AUTO_MERGE_FORBIDDEN")
    require(decision.get("directMainUpdate") is False, "DIRECT_MAIN_UPDATE_FORBIDDEN")
    require(canonical_digest(decision_core(decision)) == decision.get("decisionId"), "HUMAN_DECISION_ID_MISMATCH")
    return authorizer, nonce


def live_requalification(cg18_protocol, source, token, api_url="https://api.github.com"):
    require(cg18_protocol.get("id") == "CG18-PULL-REQUEST-QUALIFICATION-DECISION", "CG18_PROTOCOL_REQUIRED")
    require(cg18_protocol.get("status") == "QUALIFIED", "CG18_PROTOCOL_NOT_QUALIFIED")
    try:
        client = cg18_qualify.GitHubReader(token, api_url)
        snapshot = cg18_qualify.read_snapshot(cg18_protocol, source, client)
        decision = cg18_review_state.qualify_snapshot(cg18_protocol["qualificationPolicy"], snapshot)
    except cg18_qualify.PullRequestQualificationError as exc:
        raise MergeAuthorizationError(exc.reason, exc.detail)
    require(decision.get("disposition") == "PR_QUALIFIED_FOR_HUMAN_MERGE_REVIEW", "LIVE_CG18_REQUALIFICATION_FAILED", decision.get("reasons"))
    require(decision.get("reasons") == [], "LIVE_CG18_REQUALIFICATION_FAILED", decision.get("reasons"))
    return snapshot


def authorize(protocol, cg18_protocol, source, human_decision, token, api_url="https://api.github.com"):
    changed_paths, verification_classes = validate_source(protocol, source)
    authorizer, nonce = validate_human_decision(protocol, source, human_decision)
    snapshot = live_requalification(cg18_protocol, source, token, api_url)
    require(snapshot.get("changedPaths") == changed_paths, "LIVE_CHANGED_PATHS_MISMATCH")
    policy = protocol["authorizationPolicy"]
    core = {
        "schemaVersion": 1,
        "protocolId": protocol["id"],
        "caseId": source["caseId"],
        "repository": source["repository"],
        "provider": policy["provider"],
        "pullRequestNumber": source["pullRequestNumber"],
        "baseBranch": source["baseBranch"],
        "baseSha": source["baseSha"],
        "headBranch": source["headBranch"],
        "headSha": source["headSha"],
        "expectedHeadSha": source["headSha"],
        "changedPaths": changed_paths,
        "verificationClasses": verification_classes,
        "sourceQualificationAttestationId": source["qualificationAttestationId"],
        "sourceQualificationAttestationSha256": canonical_digest(source),
        "liveQualificationSnapshotSha256": canonical_digest(snapshot),
        "humanDecisionId": human_decision["decisionId"],
        "humanDecisionSha256": canonical_digest(human_decision),
        "authorizer": authorizer,
        "decisionNonce": nonce,
        "mergeMethod": human_decision["mergeMethod"],
        "mergeAuthorization": policy["grant"],
        "authorizationUseLimit": policy["authorizationUseLimit"],
        "consumptionState": policy["consumptionStateAtCreation"],
        "oneTimeEnforcementBoundary": policy["oneTimeEnforcementBoundary"],
        "executesMerge": False,
        "reviewSubmissionAuthorization": "NOT_GRANTED",
        "pullRequestUpdateAuthorization": "NOT_GRANTED",
        "autoMergeAuthorization": "NOT_GRANTED",
        "directMainUpdate": False,
        "repositoryAdministration": False,
        "consumerAdoption": False,
        "repositoryBlocking": False,
    }
    return {
        **core,
        "status": protocol["outputs"]["status"],
        "decision": protocol["outputs"]["decision"],
        "authorizationId": canonical_digest(core),
    }


def parse_args():
    parser = argparse.ArgumentParser(description="CG-19 controlled merge authorization")
    parser.add_argument("--protocol", required=True)
    parser.add_argument("--cg18-protocol", required=True)
    parser.add_argument("--source-qualification", required=True)
    parser.add_argument("--human-decision", required=True)
    parser.add_argument("--api-url", default=os.environ.get("GITHUB_API_URL", "https://api.github.com"))
    return parser.parse_args()


def main():
    args = parse_args()
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or ""
    try:
        out = authorize(load(args.protocol), load(args.cg18_protocol), load(args.source_qualification), load(args.human_decision), token, args.api_url)
        print(json.dumps(out, indent=2, sort_keys=True))
        return 0
    except MergeAuthorizationError as exc:
        out = {"schemaVersion": 1, "status": "REJECTED", "decision": "MERGE_AUTHORIZATION_NOT_GRANTED", "reason": exc.reason}
        if exc.detail is not None:
            out["detail"] = exc.detail
        print(json.dumps(out, indent=2, sort_keys=True))
        return 2


if __name__ == "__main__":
    sys.exit(main())
