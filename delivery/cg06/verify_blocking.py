#!/usr/bin/env python3
import argparse
import json
import sys
from pathlib import Path


class VerificationError(Exception):
    def __init__(self, reason, detail=None):
        super().__init__(reason)
        self.reason = reason
        self.detail = detail


def load(path):
    return json.loads(Path(path).read_text())


def require(cond, reason, detail=None):
    if not cond:
        raise VerificationError(reason, detail)


def emit(value):
    print(json.dumps(value, indent=2, sort_keys=True))


def args():
    p = argparse.ArgumentParser(description="CG-06 repository-blocking trusted-delivery verifier")
    p.add_argument("--protocol", required=True)
    p.add_argument("--intent", required=True)
    p.add_argument("--observed", required=True)
    p.add_argument("--mode", choices=("readiness", "live"), required=True)
    return p.parse_args()


def rule_by_type(rules, typ):
    return [r for r in rules if r.get("type") == typ]


def main():
    a = args()
    try:
        protocol = load(a.protocol)
        intent = load(a.intent)
        observed = load(a.observed)

        require(protocol.get("schemaVersion") == 1, "PROTOCOL_SCHEMA_MISMATCH")
        require(intent.get("schemaVersion") == 1, "INTENT_SCHEMA_MISMATCH")
        require(observed.get("schemaVersion") == 1, "OBSERVED_SCHEMA_MISMATCH")
        require(protocol.get("id") == "CG06-REPOSITORY-BLOCKING-TRUSTED-DELIVERY", "WRONG_PROTOCOL")
        require(protocol.get("status") == "BLOCKED_ADMIN_ACTIVATION", "UNEXPECTED_PROTOCOL_STATE")
        require(intent.get("status") == "READY_EXCEPT_EXTERNAL_ADMIN_ACTIVATION", "INTENT_NOT_READY")
        require(intent.get("repository") == "hvritual/iot-delivery-system", "WRONG_CONSUMER")
        require(intent.get("branch") == "main", "WRONG_BRANCH")

        ruleset = intent["rulesetRequest"]["payloadTemplate"]
        require(ruleset.get("target") == "branch", "RULESET_TARGET_MISMATCH")
        require(ruleset.get("enforcement") == "active", "RULESET_NOT_ACTIVE_INTENT")
        require("~DEFAULT_BRANCH" in ruleset.get("conditions", {}).get("ref_name", {}).get("include", []), "DEFAULT_BRANCH_NOT_TARGETED")
        required_types = {"update", "deletion", "non_fast_forward", "required_status_checks"}
        actual_types = {r.get("type") for r in ruleset.get("rules", [])}
        require(required_types.issubset(actual_types), "INTENT_MISSING_REQUIRED_RULES", {"missing": sorted(required_types - actual_types)})
        status_rule = rule_by_type(ruleset["rules"], "required_status_checks")[0]
        checks = status_rule.get("parameters", {}).get("required_status_checks", [])
        require(len(checks) == 1, "INTENT_REQUIRED_STATUS_COUNT_MISMATCH")
        require(checks[0].get("context") == "engineering-governance/trusted-delivery", "INTENT_STATUS_CONTEXT_MISMATCH")
        require(checks[0].get("integration_id") == "${TRUSTED_GITHUB_APP_INTEGRATION_ID}", "INTENT_STATUS_SOURCE_NOT_PINNED_TEMPLATE")
        bypass = ruleset.get("bypass_actors", [])
        require(len(bypass) == 1, "INTENT_BYPASS_SET_NOT_MINIMAL")
        require(bypass[0].get("actor_type") == "Integration", "INTENT_BYPASS_NOT_INTEGRATION")
        require(bypass[0].get("actor_id") == "${TRUSTED_GITHUB_APP_INTEGRATION_ID}", "INTENT_BYPASS_ID_NOT_PINNED_TEMPLATE")
        require(intent["trustedPublisherRequest"].get("state") == "UNRESOLVED", "UNEXPECTED_PUBLISHER_STATE")
        require(observed.get("accountPermission", {}).get("permission") == "admin", "HUMAN_ADMIN_PERMISSION_MISSING")

        readiness = {
            "schemaVersion": 1,
            "status": "PASS",
            "decision": "READY_FOR_EXTERNAL_ADMIN_ACTIVATION",
            "repositoryBlocking": False,
            "unresolved": [
                "trusted GitHub App integration id",
                "ruleset administration write",
                "trusted status/check publication capability",
                "live bypass/negative proof",
                "positive exact-SHA integration and post-readback"
            ],
        }
        if a.mode == "readiness":
            emit(readiness)
            return 0

        # Live mode intentionally fails closed until actual repository state proves every invariant.
        require(observed.get("repository") == intent.get("repository"), "OBSERVED_REPOSITORY_MISMATCH")
        require(observed.get("branch") == intent.get("branch"), "OBSERVED_BRANCH_MISMATCH")
        require(observed.get("branchObservation", {}).get("protected") is True, "BRANCH_NOT_PROTECTED")

        live_rulesets = observed.get("rulesets", [])
        matching = [r for r in live_rulesets if r.get("name") == ruleset.get("name") and r.get("enforcement") == "active"]
        require(len(matching) == 1, "ACTIVE_RULESET_NOT_FOUND")
        live = matching[0]
        live_bypass = live.get("bypass_actors", [])
        require(len(live_bypass) == 1, "LIVE_BYPASS_SET_NOT_MINIMAL")
        trusted_id = live_bypass[0].get("actor_id")
        require(isinstance(trusted_id, int) and trusted_id > 0, "LIVE_TRUSTED_INTEGRATION_ID_INVALID")
        require(live_bypass[0].get("actor_type") == "Integration", "LIVE_BYPASS_NOT_INTEGRATION")

        live_types = {r.get("type") for r in live.get("rules", [])}
        require(required_types.issubset(live_types), "LIVE_RULESET_MISSING_REQUIRED_RULES")
        live_status = rule_by_type(live.get("rules", []), "required_status_checks")
        require(len(live_status) == 1, "LIVE_STATUS_RULE_COUNT_MISMATCH")
        live_checks = live_status[0].get("parameters", {}).get("required_status_checks", [])
        require(len(live_checks) == 1, "LIVE_REQUIRED_STATUS_COUNT_MISMATCH")
        require(live_checks[0].get("context") == "engineering-governance/trusted-delivery", "LIVE_STATUS_CONTEXT_MISMATCH")
        require(live_checks[0].get("integration_id") == trusted_id, "LIVE_STATUS_SOURCE_NOT_PINNED_TO_BYPASS_APP")

        publisher = observed.get("trustedPublisher", {})
        require(publisher.get("integrationId") == trusted_id, "PUBLISHER_INTEGRATION_ID_MISMATCH")
        require(publisher.get("installed") is True, "TRUSTED_PUBLISHER_NOT_INSTALLED")
        require(publisher.get("statusesWrite") is True, "TRUSTED_PUBLISHER_MISSING_STATUS_WRITE")
        require(publisher.get("candidateControlled") is False, "TRUSTED_PUBLISHER_CANDIDATE_CONTROLLED")

        candidate = observed.get("qualifiedCandidate", {})
        candidate_sha = candidate.get("sourceCommit")
        require(isinstance(candidate_sha, str) and len(candidate_sha) == 40, "QUALIFIED_CANDIDATE_SHA_INVALID")
        require(candidate.get("cg05Aggregate") == "PASS", "CG05_AGGREGATE_NOT_PASS")
        status = observed.get("trustedStatus", {})
        require(status.get("context") == "engineering-governance/trusted-delivery", "TRUSTED_STATUS_CONTEXT_MISMATCH")
        require(status.get("sourceCommit") == candidate_sha, "TRUSTED_STATUS_SOURCE_SHA_MISMATCH")
        require(status.get("integrationId") == trusted_id, "TRUSTED_STATUS_INTEGRATION_MISMATCH")
        require(status.get("state") == "success", "TRUSTED_STATUS_NOT_SUCCESS")

        post = observed.get("postIntegration", {})
        require(post.get("nonForce") is True, "INTEGRATION_NOT_NON_FORCE")
        require(post.get("mainSha") == candidate_sha, "POST_INTEGRATION_MAIN_SHA_MISMATCH")
        require(post.get("rulesetStillActive") is True, "RULESET_NOT_ACTIVE_AFTER_INTEGRATION")
        require(post.get("negativeBypassProof") == "PASS", "NEGATIVE_BYPASS_PROOF_MISSING")

        emit({
            "schemaVersion": 1,
            "status": "PASS",
            "decision": "REPOSITORY_BLOCKING_TRUSTED_DELIVERY_VERIFIED",
            "repositoryBlocking": True,
            "trustedIntegrationId": trusted_id,
            "qualifiedCandidateSha": candidate_sha,
            "mainSha": post.get("mainSha")
        })
        return 0
    except VerificationError as e:
        value = {"schemaVersion": 1, "status": "REJECTED", "reason": e.reason, "repositoryBlocking": False}
        if e.detail is not None:
            value["detail"] = e.detail
        emit(value)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
