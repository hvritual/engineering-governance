#!/usr/bin/env python3
import argparse
import json
import re
import sys
from pathlib import Path

from reconcile_completion import (
    CompletionReconciliationError,
    canonical_digest,
    load,
    reconcile,
)


SHA256 = re.compile(r"^[0-9a-f]{64}$")


class CompletionVerificationError(Exception):
    def __init__(self, reason, detail=None):
        super().__init__(reason)
        self.reason = reason
        self.detail = detail


def require(condition, reason, detail=None):
    if not condition:
        raise CompletionVerificationError(reason, detail)


def completion_core(attestation):
    return {k: v for k, v in attestation.items() if k not in {"status", "decision", "completionAttestationId"}}


def verify(protocol, cg07_protocol, cg07_protocol_path, cg07_verifier_path, source, observed, attestation):
    require(isinstance(attestation, dict), "COMPLETION_ATTESTATION_REQUIRED")
    require(attestation.get("schemaVersion") == 1, "COMPLETION_ATTESTATION_SCHEMA_MISMATCH")
    require(attestation.get("protocolId") == protocol.get("id"), "COMPLETION_ATTESTATION_PROTOCOL_MISMATCH")
    require(attestation.get("status") == protocol["outputs"]["status"], "COMPLETION_ATTESTATION_STATUS_INVALID")
    require(attestation.get("decision") == protocol["outputs"]["decision"], "COMPLETION_ATTESTATION_DECISION_INVALID")
    require(SHA256.fullmatch(attestation.get("completionAttestationId", "")) is not None, "COMPLETION_ATTESTATION_ID_INVALID")
    require(canonical_digest(completion_core(attestation)) == attestation.get("completionAttestationId"), "COMPLETION_ATTESTATION_ID_MISMATCH")

    policy = protocol["convergencePolicy"]
    require(attestation.get("completionState") == policy["completionState"], "COMPLETION_STATE_INVALID")
    require(attestation.get("recoveryDisposition") == policy["integrityPassRecoveryDisposition"], "RECOVERY_DISPOSITION_INVALID")
    require(attestation.get("integrityAuthority") == "CG07", "INTEGRITY_AUTHORITY_DRIFT")
    for field in ("executesMerge", "executesRecovery", "automaticForcePush", "automaticReset", "automaticRevert", "directMainUpdate", "repositoryAdministration", "consumerAdoption", "repositoryBlocking"):
        require(attestation.get(field) is False, "COMPLETION_AUTHORITY_ESCALATION", {"field": field})

    try:
        expected = reconcile(protocol, cg07_protocol, cg07_protocol_path, cg07_verifier_path, source, observed)
    except CompletionReconciliationError as exc:
        raise CompletionVerificationError(exc.reason, exc.detail)

    if expected != attestation:
        differing = sorted(k for k in set(expected) | set(attestation) if expected.get(k) != attestation.get(k))
        raise CompletionVerificationError("COMPLETION_ATTESTATION_RECONCILIATION_MISMATCH", {"differingFields": differing})

    core = {
        "schemaVersion": 1,
        "protocolId": protocol["id"],
        "completionAttestationId": attestation["completionAttestationId"],
        "completionAttestationSha256": canonical_digest(attestation),
        "sourceExecutionAttestationId": attestation["sourceExecutionAttestationId"],
        "cg07VerificationSha256": attestation["cg07VerificationSha256"],
        "completionState": attestation["completionState"],
        "recoveryDisposition": attestation["recoveryDisposition"],
        "repositoryBlocking": False,
    }
    return {
        **core,
        "status": "DELIVERY_COMPLETION_ATTESTATION_VERIFIED",
        "decision": "EXECUTION_INTEGRITY_COMPLETION_ATTESTATION_VERIFIED",
        "verificationId": canonical_digest(core),
    }


def parse_args():
    parser = argparse.ArgumentParser(description="CG-21 completion attestation verifier")
    parser.add_argument("--protocol", required=True)
    parser.add_argument("--cg07-protocol", required=True)
    parser.add_argument("--cg07-verifier", required=True)
    parser.add_argument("--source-execution", required=True)
    parser.add_argument("--observed", required=True)
    parser.add_argument("--completion-attestation", required=True)
    return parser.parse_args()


def main():
    args = parse_args()
    try:
        out = verify(
            load(args.protocol),
            load(args.cg07_protocol),
            args.cg07_protocol,
            args.cg07_verifier,
            load(args.source_execution),
            load(args.observed),
            load(args.completion_attestation),
        )
        print(json.dumps(out, indent=2, sort_keys=True))
        return 0
    except CompletionVerificationError as exc:
        out = {
            "schemaVersion": 1,
            "status": "REJECTED",
            "decision": "DELIVERY_COMPLETION_ATTESTATION_NOT_VERIFIED",
            "reason": exc.reason,
            "recoveryDisposition": "STOP_AND_INVESTIGATE",
            "repositoryBlocking": False,
        }
        if exc.detail is not None:
            out["detail"] = exc.detail
        print(json.dumps(out, indent=2, sort_keys=True))
        return 2


if __name__ == "__main__":
    sys.exit(main())
