#!/usr/bin/env python3
import argparse
import json
import re
import sys
from pathlib import Path

from build_proof import (
    ProofOfChangeError,
    build,
    canonical_digest,
    load,
    validate_source,
)


SHA256 = re.compile(r"^[0-9a-f]{64}$")


class ProofVerificationError(Exception):
    def __init__(self, reason, detail=None):
        super().__init__(reason)
        self.reason = reason
        self.detail = detail


def require(condition, reason, detail=None):
    if not condition:
        raise ProofVerificationError(reason, detail)


def proof_core(proof):
    return {k: v for k, v in proof.items() if k not in {"status", "decision", "proofId"}}


def verify(protocol, source, source_verification, proof):
    try:
        validate_source(protocol, source, source_verification)
    except ProofOfChangeError as exc:
        raise ProofVerificationError(exc.reason, exc.detail)

    require(isinstance(proof, dict), "PROOF_OF_CHANGE_REQUIRED")
    require(proof.get("schemaVersion") == 1, "PROOF_SCHEMA_MISMATCH")
    require(proof.get("protocolId") == protocol.get("id"), "PROOF_PROTOCOL_MISMATCH")
    require(proof.get("status") == protocol["outputs"]["status"], "PROOF_STATUS_INVALID")
    require(proof.get("decision") == protocol["outputs"]["decision"], "PROOF_DECISION_INVALID")
    require(SHA256.fullmatch(proof.get("proofId", "")) is not None, "PROOF_ID_INVALID")
    require(canonical_digest(proof_core(proof)) == proof.get("proofId"), "PROOF_ID_MISMATCH")

    policy = protocol["proofPolicy"]
    require(proof.get("proofType") == policy["proofType"], "PROOF_TYPE_MISMATCH")
    require(proof.get("terminalState") == policy["terminalState"], "TERMINAL_STATE_MISMATCH")
    require(proof.get("completionState") == policy["requiredCompletionState"], "PROOF_COMPLETION_STATE_MISMATCH")
    require(proof.get("recoveryDisposition") == policy["requiredRecoveryDisposition"], "PROOF_RECOVERY_DISPOSITION_MISMATCH")
    require(proof.get("completionAuthority") == policy["requiredCompletionAuthority"], "COMPLETION_AUTHORITY_DRIFT")
    require(proof.get("integrityAuthority") == policy["requiredIntegrityAuthority"], "INTEGRITY_AUTHORITY_DRIFT")
    require(proof.get("sourceCompletionAttestationId") == source["completionAttestationId"], "PROOF_SOURCE_COMPLETION_BINDING_MISMATCH")
    require(proof.get("sourceCompletionAttestationSha256") == canonical_digest(source), "PROOF_SOURCE_COMPLETION_DIGEST_MISMATCH")
    require(proof.get("sourceCompletionVerificationId") == source_verification["verificationId"], "PROOF_SOURCE_VERIFICATION_BINDING_MISMATCH")
    require(proof.get("sourceCompletionVerificationSha256") == canonical_digest(source_verification), "PROOF_SOURCE_VERIFICATION_DIGEST_MISMATCH")
    require(proof.get("sourceExecutionAttestationId") == source["sourceExecutionAttestationId"], "PROOF_SOURCE_EXECUTION_BINDING_MISMATCH")
    require(proof.get("cg07VerificationSha256") == source["cg07VerificationSha256"], "PROOF_INTEGRITY_BINDING_MISMATCH")
    require(proof.get("portableDeterministicReceipt") is True, "PROOF_PORTABILITY_FLAG_INVALID")
    for field in (
        "createsNewCompletionDecision",
        "createsNewIntegrityDecision",
        "executesMerge",
        "executesRecovery",
        "directMainUpdate",
        "repositoryAdministration",
        "consumerAdoption",
        "repositoryBlocking",
    ):
        require(proof.get(field) is False, "PROOF_AUTHORITY_ESCALATION", {"field": field})

    try:
        expected = build(protocol, source, source_verification)
    except ProofOfChangeError as exc:
        raise ProofVerificationError(exc.reason, exc.detail)
    if expected != proof:
        differing = sorted(k for k in set(expected) | set(proof) if expected.get(k) != proof.get(k))
        raise ProofVerificationError("PROOF_RECONCILIATION_MISMATCH", {"differingFields": differing})

    core = {
        "schemaVersion": 1,
        "protocolId": protocol["id"],
        "proofId": proof["proofId"],
        "proofSha256": canonical_digest(proof),
        "sourceCompletionAttestationId": source["completionAttestationId"],
        "sourceCompletionVerificationId": source_verification["verificationId"],
        "sourceExecutionAttestationId": source["sourceExecutionAttestationId"],
        "cg07VerificationSha256": source["cg07VerificationSha256"],
        "completionState": source["completionState"],
        "terminalState": proof["terminalState"],
        "completionAuthority": proof["completionAuthority"],
        "integrityAuthority": proof["integrityAuthority"],
        "repositoryBlocking": False,
    }
    return {
        **core,
        "status": protocol["outputs"]["verificationStatus"],
        "decision": protocol["outputs"]["verificationDecision"],
        "verificationId": canonical_digest(core),
    }


def parse_args():
    parser = argparse.ArgumentParser(description="CG-22 terminal Proof-of-Change verifier")
    parser.add_argument("--protocol", required=True)
    parser.add_argument("--source-completion", required=True)
    parser.add_argument("--source-verification", required=True)
    parser.add_argument("--proof", required=True)
    return parser.parse_args()


def main():
    args = parse_args()
    try:
        out = verify(
            load(args.protocol),
            load(args.source_completion),
            load(args.source_verification),
            load(args.proof),
        )
        print(json.dumps(out, indent=2, sort_keys=True))
        return 0
    except ProofVerificationError as exc:
        out = {
            "schemaVersion": 1,
            "status": "REJECTED",
            "decision": "TERMINAL_PROOF_OF_CHANGE_NOT_VERIFIED",
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
