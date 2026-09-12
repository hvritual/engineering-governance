#!/usr/bin/env python3
import hashlib
import json


BLOCKING_CHECK_CONCLUSIONS = {
    "failure",
    "cancelled",
    "timed_out",
    "action_required",
    "stale",
    "startup_failure",
}
BLOCKING_STATUS_STATES = {"error", "failure", "pending"}


def canonical_digest(value):
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def effective_review_summary(reviews):
    latest = {}
    for review in reviews or []:
        if not isinstance(review, dict):
            continue
        user = review.get("user") or {}
        login = user.get("login") if isinstance(user, dict) else None
        state = review.get("state")
        if not isinstance(login, str) or not login:
            continue
        if state not in {"APPROVED", "CHANGES_REQUESTED", "DISMISSED", "COMMENTED"}:
            continue
        if state == "COMMENTED":
            continue
        order = (review.get("submitted_at") or "", int(review.get("id") or 0))
        previous = latest.get(login)
        if previous is None or order >= previous[0]:
            latest[login] = (order, state)

    effective = {}
    for login, (_, state) in latest.items():
        if state == "DISMISSED":
            continue
        effective[login] = state
    approvals = sorted(login for login, state in effective.items() if state == "APPROVED")
    changes_requested = sorted(login for login, state in effective.items() if state == "CHANGES_REQUESTED")
    return {
        "effectiveReviews": effective,
        "approvalLogins": approvals,
        "approvalCount": len(approvals),
        "changesRequestedLogins": changes_requested,
        "changesRequestedCount": len(changes_requested),
    }


def check_summary(check_runs, commit_statuses, policy):
    pending_checks = []
    blocking_checks = []
    for run in check_runs or []:
        if not isinstance(run, dict):
            continue
        name = run.get("name") or run.get("id") or "unknown"
        status = run.get("status")
        conclusion = run.get("conclusion")
        if status != "completed":
            pending_checks.append(str(name))
        elif conclusion in set(policy.get("disallowedCheckConclusions") or BLOCKING_CHECK_CONCLUSIONS):
            blocking_checks.append({"name": str(name), "conclusion": conclusion})

    blocking_statuses = []
    for status in commit_statuses or []:
        if not isinstance(status, dict):
            continue
        state = status.get("state")
        if state in set(policy.get("disallowedCommitStatusStates") or BLOCKING_STATUS_STATES):
            blocking_statuses.append({"context": status.get("context"), "state": state})

    return {
        "checkRunCount": len(check_runs or []),
        "pendingChecks": pending_checks,
        "blockingChecks": blocking_checks,
        "commitStatusCount": len(commit_statuses or []),
        "blockingStatuses": blocking_statuses,
    }


def qualify_snapshot(policy, snapshot):
    reasons = []
    if snapshot.get("state") != "open":
        reasons.append("PULL_REQUEST_NOT_OPEN")
    if snapshot.get("draft") is not False:
        reasons.append("DRAFT_PULL_REQUEST_FORBIDDEN")
    if snapshot.get("merged") is not False:
        reasons.append("PULL_REQUEST_ALREADY_MERGED")
    if policy.get("requireMergeableTrue") and snapshot.get("mergeable") is not True:
        reasons.append("PULL_REQUEST_NOT_MERGEABLE")

    reviews = snapshot.get("reviewSummary") or {}
    if policy.get("requireNoEffectiveChangesRequested") and reviews.get("changesRequestedCount", 0) != 0:
        reasons.append("CHANGES_REQUESTED_BLOCKING")
    minimum_approvals = int(policy.get("minimumApprovals", 0))
    if reviews.get("approvalCount", 0) < minimum_approvals:
        reasons.append("MINIMUM_APPROVALS_NOT_MET")

    if policy.get("requireNoUnresolvedReviewThreads") and snapshot.get("unresolvedReviewThreads", 0) != 0:
        reasons.append("UNRESOLVED_REVIEW_THREADS")

    checks = snapshot.get("checkSummary") or {}
    if checks.get("pendingChecks"):
        reasons.append("CHECKS_NOT_TERMINAL")
    if checks.get("blockingChecks") or checks.get("blockingStatuses"):
        reasons.append("CHECKS_NOT_SUCCESSFUL")

    disposition = "PR_QUALIFIED_FOR_HUMAN_MERGE_REVIEW" if not reasons else "STOP_AND_INVESTIGATE"
    return {
        "disposition": disposition,
        "reasons": reasons,
        "mergeAuthorization": "NOT_GRANTED",
    }
