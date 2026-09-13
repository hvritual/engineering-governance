#!/usr/bin/env python3
import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

SEMANTIC_PREFIXES = (
    "contracts/proto/commercial/",
    "contracts/gen/commercial/",
    "contracts/generated/",
    "contracts/commercial/",
    "internal/commercial/",
    "internal/access/",
    "internal/bizruntime/",
    "web/tests/ce13-plan-catalog/",
)
SEMANTIC_EXACT = {
    "integration/ce13_plan_catalog_mysql_test.go",
    "integration/ce13_platform_web_session_seed_test.go",
    "web/playwright.ce13-plan-catalog.config.ts",
    ".github/workflows/ce13-plan-catalog-qualification.yml",
    "go.mod",
    "go.sum",
}


def reject(reason, **extra):
    out = {"status": "REJECTED", "reason": reason}
    out.update(extra)
    print(json.dumps(out, sort_keys=True))
    raise SystemExit(2)


def load(path):
    return json.loads(Path(path).read_text())


def git(root, *args):
    return subprocess.check_output(["git", "-C", root, *args], text=True).strip()


def blob(root, path, ref="HEAD"):
    return git(root, "rev-parse", f"{ref}:{path}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--protocol", required=True)
    ap.add_argument("--preflight", required=True)
    ap.add_argument("--cg27-protocol", required=True)
    ap.add_argument("--cg27-closure", required=True)
    ap.add_argument("--governance-root", required=True)
    ap.add_argument("--biz-root", required=True)
    ap.add_argument("--issue-json", required=True)
    args = ap.parse_args()

    protocol = load(args.protocol)
    preflight = load(args.preflight)
    cg27_protocol = load(args.cg27_protocol)
    cg27_closure = load(args.cg27_closure)
    issue = load(args.issue_json)

    if protocol.get("id") != "CG28-CONTROLLED-SOURCE-ISSUE-CLOSURE-EXECUTION":
        reject("PROTOCOL_ID_MISMATCH")
    if protocol.get("source", {}).get("cg27", {}).get("closureId") != cg27_closure.get("closureId"):
        reject("CG27_CLOSURE_ID_MISMATCH")
    if cg27_protocol.get("requiredDecision") != "SOURCE_ISSUE_CLOSURE_READY":
        reject("CG27_NOT_CLOSURE_READY")
    if cg27_closure.get("decision") != "SOURCE_ISSUE_CLOSURE_READY":
        reject("CG27_RECEIPT_NOT_CLOSURE_READY")
    if cg27_closure.get("nextDisposition") != "CONTROLLED_SOURCE_ISSUE_CLOSURE_REQUIRED":
        reject("CG27_NEXT_DISPOSITION_MISMATCH")

    for authority in protocol["source"]["cg27"]["authorities"]:
        actual = blob(args.governance_root, authority["path"])
        if actual != authority["blob"]:
            reject("CG27_AUTHORITY_BLOB_MISMATCH", path=authority["path"], expected=authority["blob"], actual=actual)

    target = protocol["target"]
    if issue.get("number") != target["issueNumber"]:
        reject("SOURCE_ISSUE_NUMBER_MISMATCH")
    if issue.get("title") != target["title"]:
        reject("SOURCE_ISSUE_TITLE_MISMATCH")
    if issue.get("state") != target["requiredPreExecutionState"]:
        reject("SOURCE_ISSUE_NOT_OPEN")

    live = protocol["liveConsumer"]
    head = git(args.biz_root, "rev-parse", "HEAD")
    tree = git(args.biz_root, "rev-parse", "HEAD^{tree}")
    if head != live["sha"]:
        reject("LIVE_CONSUMER_SHA_MISMATCH", expected=live["sha"], actual=head)
    if tree != live["tree"]:
        reject("LIVE_CONSUMER_TREE_MISMATCH", expected=live["tree"], actual=tree)

    base = protocol["source"]["cg27"]["qualifiedConsumerSha"]
    try:
        subprocess.check_call(["git", "-C", args.biz_root, "merge-base", "--is-ancestor", base, head], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except subprocess.CalledProcessError:
        reject("CG27_CONSUMER_NOT_ANCESTOR")

    count = int(git(args.biz_root, "rev-list", "--count", f"{base}..{head}"))
    if count != live["commitsAheadOfCg27"]:
        reject("LIVE_CONSUMER_AHEAD_COUNT_MISMATCH", expected=live["commitsAheadOfCg27"], actual=count)

    changed = [p for p in git(args.biz_root, "diff", "--name-only", f"{base}..{head}").splitlines() if p]
    semantic = sorted({p for p in changed if p in SEMANTIC_EXACT or any(p.startswith(prefix) for prefix in SEMANTIC_PREFIXES)})
    if not semantic:
        reject("EXPECTED_SEMANTIC_CLOSURE_DRIFT_NOT_FOUND")

    if protocol.get("qualifiedPreflightDecision") != "SOURCE_ISSUE_CLOSURE_EXECUTION_BLOCKED":
        reject("PREFLIGHT_DECISION_MISMATCH")
    if protocol.get("requiredReason") != "SEMANTIC_CLOSURE_DRIFT_SINCE_CG27":
        reject("PREFLIGHT_REASON_MISMATCH")
    if protocol.get("requiredDisposition") != "REQUALIFICATION_REQUIRED_BEFORE_SOURCE_ISSUE_CLOSURE":
        reject("PREFLIGHT_DISPOSITION_MISMATCH")

    if preflight.get("decision") != protocol["qualifiedPreflightDecision"]:
        reject("PREFLIGHT_RECEIPT_DECISION_MISMATCH")
    if preflight.get("reason") != protocol["requiredReason"]:
        reject("PREFLIGHT_RECEIPT_REASON_MISMATCH")
    if preflight.get("nextDisposition") != protocol["requiredDisposition"]:
        reject("PREFLIGHT_RECEIPT_DISPOSITION_MISMATCH")
    if preflight.get("liveConsumer", {}).get("sha") != head or preflight.get("liveConsumer", {}).get("tree") != tree:
        reject("PREFLIGHT_LIVE_IDENTITY_MISMATCH")
    if preflight.get("liveConsumer", {}).get("commitsAhead") != count:
        reject("PREFLIGHT_AHEAD_COUNT_MISMATCH")
    if preflight.get("semanticClosure", {}).get("driftDetected") is not True:
        reject("PREFLIGHT_SEMANTIC_DRIFT_FLAG_MISSING")

    authority = protocol.get("authorityCeiling", {})
    if any(authority.values()):
        reject("AUTHORITY_ESCALATION")
    execution = preflight.get("execution", {})
    if any(execution.values()):
        reject("UNAUTHORIZED_EXECUTION_RECORDED")

    core = {k: v for k, v in preflight.items() if k != "preflightId"}
    preflight_id = hashlib.sha256(json.dumps(core, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
    if preflight.get("preflightId") not in (None, preflight_id):
        reject("PREFLIGHT_ID_MISMATCH")

    out = {
        "status": "SOURCE_ISSUE_CLOSURE_EXECUTION_PREFLIGHT_QUALIFIED",
        "decision": protocol["qualifiedPreflightDecision"],
        "reason": protocol["requiredReason"],
        "nextDisposition": protocol["requiredDisposition"],
        "liveConsumerSha": head,
        "liveConsumerTree": tree,
        "commitsAheadOfCg27": count,
        "semanticClosureChangedPathCount": len(semantic),
        "semanticClosureChangedPaths": semantic,
        "sourceIssueState": issue["state"],
        "sourceIssueCommented": False,
        "sourceIssueClosed": False,
        "consumerSourceMutation": False,
        "preflightId": preflight_id,
    }
    print(json.dumps(out, sort_keys=True))


if __name__ == "__main__":
    main()
