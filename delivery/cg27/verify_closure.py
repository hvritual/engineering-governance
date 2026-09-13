#!/usr/bin/env python3
import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path


class ClosureVerificationError(Exception):
    def __init__(self, reason, detail=None):
        super().__init__(reason)
        self.reason = reason
        self.detail = detail


def require(condition, reason, detail=None):
    if not condition:
        raise ClosureVerificationError(reason, detail)


def load(path):
    return json.loads(Path(path).read_text())


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def git(root, *args, check=True):
    cp = subprocess.run(
        ["git", "-C", str(root), *args],
        check=False,
        capture_output=True,
        text=True,
    )
    if check and cp.returncode != 0:
        raise ClosureVerificationError(
            "GIT_READBACK_FAILED",
            {"args": list(args), "stderr": cp.stderr[-1000:], "returncode": cp.returncode},
        )
    return cp


def closure_core(receipt):
    return {k: v for k, v in receipt.items() if k != "closureId"}


def semantic_path(path, semantic_closure):
    if path in set(semantic_closure.get("exactPaths", [])):
        return True
    return any(path.startswith(prefix) for prefix in semantic_closure.get("prefixes", []))


def authority_zero(authority, reason):
    required_false = (
        "sourceIssueClosureAuthorized",
        "sourceIssueCommentAuthorized",
        "modifiesConsumer",
        "createsConsumerBranch",
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
    for field in required_false:
        require(authority.get(field) is False, reason, {"field": field})


def verify(protocol, receipt, cg26_protocol, cg26_reconciliation, governance_root, biz_root, issue):
    require(protocol.get("schemaVersion") == 1, "PROTOCOL_SCHEMA_MISMATCH")
    require(protocol.get("id") == "CG27-BIZ-SOURCE-ISSUE-CLOSURE-READINESS", "WRONG_PROTOCOL")
    require(protocol.get("status") in {"CANDIDATE", "QUALIFIED"}, "UNEXPECTED_PROTOCOL_STATUS")

    source = protocol.get("source", {}).get("cg26", {})
    require(source.get("protocolId") == "CG26-BIZ-PLAN-CATALOG-SEMANTIC-RECONCILIATION", "CG26_PROTOCOL_ID_MISMATCH")
    require(source.get("reconciliationId") == "bb8deb18c55868384363af5f12aadae55a07516651f54ed2ae44e1bc1db24c9e", "CG26_RECONCILIATION_ID_MISMATCH")
    for item in source.get("authorities", []):
        actual = git(governance_root, "rev-parse", "HEAD:" + item["path"]).stdout.strip()
        require(actual == item["blob"], "CG26_SOURCE_BLOB_MISMATCH", {"path": item["path"], "actual": actual, "expected": item["blob"]})

    require(cg26_protocol.get("schemaVersion") == 1, "CG26_PROTOCOL_SCHEMA_MISMATCH")
    require(cg26_protocol.get("id") == source["protocolId"], "CG26_PROTOCOL_CONTENT_ID_MISMATCH")
    require(cg26_protocol.get("status") == "QUALIFIED", "CG26_PROTOCOL_NOT_QUALIFIED")
    require(cg26_protocol.get("qualification", {}).get("reconciliationId") == source["reconciliationId"], "CG26_PROTOCOL_RECONCILIATION_ID_MISMATCH")
    require(cg26_protocol.get("requiredDecision") == source["requiredDecision"], "CG26_REQUIRED_DECISION_MISMATCH")
    require(cg26_protocol.get("requiredReason") == source["requiredReason"], "CG26_REQUIRED_REASON_MISMATCH")

    require(cg26_reconciliation.get("schemaVersion") == 1, "CG26_RECONCILIATION_SCHEMA_MISMATCH")
    require(cg26_reconciliation.get("protocolId") == source["protocolId"], "CG26_RECONCILIATION_PROTOCOL_MISMATCH")
    require(cg26_reconciliation.get("reconciliationId") == source["reconciliationId"], "CG26_RECONCILIATION_RECEIPT_ID_MISMATCH")
    require(cg26_reconciliation.get("decision") == "NO_CHANGE_REQUIRED", "SOURCE_RECONCILIATION_NOT_NO_CHANGE")
    require(cg26_reconciliation.get("patchRequired") is False, "SOURCE_RECONCILIATION_PATCH_REQUIRED")
    require(cg26_reconciliation.get("reason") == source["requiredReason"], "SOURCE_RECONCILIATION_REASON_MISMATCH")
    disposition = cg26_reconciliation.get("verificationDisposition", {})
    require(disposition.get("requiredClassCount") == 16, "CG26_VERIFICATION_CLASS_COUNT_MISMATCH")
    require(disposition.get("allRequiredClassesSatisfied") is True, "CG26_VERIFICATION_INCOMPLETE")
    require(disposition.get("historicalQualificationProjectedToLive") is True, "CG26_HISTORICAL_PROJECTION_MISSING")
    require(disposition.get("semanticClosureChangedSinceQualifiedHead") is False, "CG26_SEMANTIC_CLOSURE_ALREADY_DRIFTED")
    require(disposition.get("consumerMutationRequired") is False, "CG26_CONSUMER_MUTATION_REQUIRED")

    class_results = {}
    for item in cg26_reconciliation.get("verificationResults", []):
        cls = item.get("class")
        require(isinstance(cls, str) and cls, "CG26_VERIFICATION_CLASS_INVALID")
        require(cls not in class_results, "CG26_VERIFICATION_CLASS_DUPLICATE", {"class": cls})
        class_results[cls] = item.get("status")
    require(len(class_results) == 16, "CG26_VERIFICATION_CLASS_SET_SIZE_MISMATCH")
    for cls, status in class_results.items():
        require(status == "PASS", "VERIFICATION_CLASS_NOT_PASS", {"class": cls, "status": status})

    current = protocol.get("currentConsumer", {})
    actual_head = git(biz_root, "rev-parse", "HEAD").stdout.strip()
    actual_tree = git(biz_root, "rev-parse", "HEAD^{tree}").stdout.strip()
    require(actual_head == current.get("sha"), "CONSUMER_BASE_SHA_MISMATCH", {"actual": actual_head, "expected": current.get("sha")})
    require(actual_tree == current.get("tree"), "CONSUMER_BASE_TREE_MISMATCH", {"actual": actual_tree, "expected": current.get("tree")})

    qualified_sha = source.get("qualifiedConsumerSha")
    ancestor = git(biz_root, "merge-base", "--is-ancestor", qualified_sha, actual_head, check=False)
    require(ancestor.returncode == 0, "QUALIFIED_CONSUMER_NOT_ANCESTOR")
    ahead = int(git(biz_root, "rev-list", "--count", qualified_sha + ".." + actual_head).stdout.strip())
    require(ahead == current.get("expectedCommitsAheadOfCg26"), "CONSUMER_AHEAD_COUNT_MISMATCH", {"actual": ahead, "expected": current.get("expectedCommitsAheadOfCg26")})

    semantic_closure = cg26_protocol.get("semanticClosure", {})
    require(semantic_closure.get("mustBeUnchangedSinceHistoricalHead") is True, "CG26_SEMANTIC_CLOSURE_POLICY_INVALID")
    changed = [p for p in git(biz_root, "diff", "--name-only", qualified_sha + ".." + actual_head).stdout.splitlines() if p]
    closure_drift = sorted(p for p in changed if semantic_path(p, semantic_closure))
    require(not closure_drift, "SEMANTIC_CLOSURE_DRIFT", {"paths": closure_drift})
    require(current.get("requiredSemanticClosureDrift") is False, "PROTOCOL_REQUIRES_SEMANTIC_DRIFT")

    source_issue = protocol.get("sourceIssue", {})
    require(issue.get("number") == source_issue.get("number"), "SOURCE_ISSUE_IDENTITY_MISMATCH")
    require(issue.get("title") == source_issue.get("title"), "SOURCE_ISSUE_IDENTITY_MISMATCH")
    require(issue.get("state") == source_issue.get("requiredState"), "SOURCE_ISSUE_NOT_OPEN", {"state": issue.get("state")})
    body = issue.get("body") or ""
    for text in source_issue.get("requiredAcceptanceText", []):
        require(text in body, "SOURCE_ISSUE_ACCEPTANCE_TEXT_MISSING", {"text": text})

    expected_map = protocol.get("acceptanceEvidenceMap", [])
    actual_acceptance = receipt.get("acceptance", [])
    require(len(actual_acceptance) == len(expected_map) == 5, "ACCEPTANCE_EVIDENCE_COUNT_MISMATCH")
    actual_by_id = {item.get("id"): item for item in actual_acceptance}
    require(len(actual_by_id) == 5, "ACCEPTANCE_EVIDENCE_ID_DUPLICATE")
    for mapping in expected_map:
        acceptance_id = mapping.get("acceptance")
        item = actual_by_id.get(acceptance_id)
        require(item is not None, "ACCEPTANCE_EVIDENCE_MISSING", {"acceptance": acceptance_id})
        require(item.get("status") == "PASS", "ACCEPTANCE_NOT_PASS", {"acceptance": acceptance_id})
        require(item.get("verificationClasses") == mapping.get("verificationClasses"), "ACCEPTANCE_EVIDENCE_MAP_MISMATCH", {"acceptance": acceptance_id})
        for cls in mapping.get("verificationClasses", []):
            require(class_results.get(cls) == "PASS", "VERIFICATION_CLASS_NOT_PASS", {"acceptance": acceptance_id, "class": cls, "status": class_results.get(cls)})

    require(receipt.get("schemaVersion") == 1, "CLOSURE_SCHEMA_MISMATCH")
    require(receipt.get("protocolId") == protocol["id"], "CLOSURE_PROTOCOL_MISMATCH")
    rsource = receipt.get("source", {})
    require(rsource.get("cg26ReconciliationId") == source["reconciliationId"], "CLOSURE_SOURCE_RECONCILIATION_MISMATCH")
    require(rsource.get("cg26Decision") == "NO_CHANGE_REQUIRED", "CLOSURE_SOURCE_DECISION_MISMATCH")
    require(rsource.get("cg26Reason") == source["requiredReason"], "CLOSURE_SOURCE_REASON_MISMATCH")
    require(rsource.get("qualifiedConsumerSha") == qualified_sha, "CLOSURE_SOURCE_CONSUMER_MISMATCH")

    rcurrent = receipt.get("currentConsumer", {})
    require(rcurrent.get("repository") == current.get("repository"), "CLOSURE_CONSUMER_REPOSITORY_MISMATCH")
    require(rcurrent.get("sha") == actual_head, "CLOSURE_CONSUMER_SHA_MISMATCH")
    require(rcurrent.get("tree") == actual_tree, "CLOSURE_CONSUMER_TREE_MISMATCH")
    require(rcurrent.get("descendsFromQualifiedConsumer") is True, "CLOSURE_ANCESTRY_PROOF_REQUIRED")
    require(rcurrent.get("commitsAhead") == ahead, "CLOSURE_AHEAD_COUNT_MISMATCH")
    require(rcurrent.get("semanticClosureDrift") is False, "CLOSURE_SEMANTIC_DRIFT_FORBIDDEN")

    rissue = receipt.get("sourceIssue", {})
    require(rissue.get("repository") == source_issue.get("repository"), "CLOSURE_ISSUE_REPOSITORY_MISMATCH")
    require(rissue.get("number") == source_issue.get("number"), "CLOSURE_ISSUE_NUMBER_MISMATCH")
    require(rissue.get("title") == source_issue.get("title"), "CLOSURE_ISSUE_TITLE_MISMATCH")
    require(rissue.get("requiredState") == source_issue.get("requiredState"), "CLOSURE_ISSUE_STATE_CONTRACT_MISMATCH")

    require(receipt.get("decision") == protocol.get("requiredDecision"), "CLOSURE_DECISION_INVALID")
    require(receipt.get("reason") == protocol.get("requiredReason"), "CLOSURE_REASON_INVALID")
    require(receipt.get("nextDisposition") == protocol.get("requiredNextDisposition"), "CLOSURE_NEXT_DISPOSITION_INVALID")
    require(digest(closure_core(receipt)) == receipt.get("closureId"), "CLOSURE_ID_MISMATCH")

    authority_zero(protocol.get("authorityCeiling", {}), "PROTOCOL_AUTHORITY_ESCALATION")
    authority_zero(receipt.get("authority", {}), "CLOSURE_AUTHORITY_ESCALATION")

    return {
        "schemaVersion": 1,
        "status": "SOURCE_ISSUE_CLOSURE_READINESS_QUALIFIED",
        "decision": protocol["requiredDecision"],
        "reason": protocol["requiredReason"],
        "nextDisposition": protocol["requiredNextDisposition"],
        "repository": current["repository"],
        "consumerSha": actual_head,
        "consumerTree": actual_tree,
        "sourceIssue": source_issue["number"],
        "acceptanceCount": len(actual_acceptance),
        "verificationClassCount": len(class_results),
        "commitsAheadOfCg26": ahead,
        "semanticClosureChangedPaths": closure_drift,
        "closureId": receipt["closureId"],
        "sourceIssueMutation": False,
        "consumerMutation": False,
        "sourceIssueClosureAuthorized": False,
    }


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--protocol", required=True)
    p.add_argument("--closure", required=True)
    p.add_argument("--cg26-protocol", required=True)
    p.add_argument("--cg26-reconciliation", required=True)
    p.add_argument("--governance-root", required=True)
    p.add_argument("--biz-root", required=True)
    p.add_argument("--issue-json", required=True)
    args = p.parse_args()
    try:
        result = verify(
            load(args.protocol),
            load(args.closure),
            load(args.cg26_protocol),
            load(args.cg26_reconciliation),
            Path(args.governance_root),
            Path(args.biz_root),
            load(args.issue_json),
        )
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    except ClosureVerificationError as exc:
        out = {
            "schemaVersion": 1,
            "status": "REJECTED",
            "decision": "SOURCE_ISSUE_CLOSURE_NOT_READY",
            "reason": exc.reason,
        }
        if exc.detail is not None:
            out["detail"] = exc.detail
        print(json.dumps(out, indent=2, sort_keys=True))
        return 2


if __name__ == "__main__":
    sys.exit(main())
