#!/usr/bin/env python3
import argparse
import hashlib
import json
import re
import sys
from pathlib import Path, PurePosixPath


SHA40 = re.compile(r"^[0-9a-f]{40}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
REPOSITORY = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


class ProofOfChangeError(Exception):
    def __init__(self, reason, detail=None):
        super().__init__(reason)
        self.reason = reason
        self.detail = detail


def require(condition, reason, detail=None):
    if not condition:
        raise ProofOfChangeError(reason, detail)


def load(path):
    return json.loads(Path(path).read_text())


def canonical_digest(value):
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def completion_core(attestation):
    return {k: v for k, v in attestation.items() if k not in {"status", "decision", "completionAttestationId"}}


def verification_core(verification):
    return {k: v for k, v in verification.items() if k not in {"status", "decision", "verificationId"}}


def canonical_paths(value, reason):
    require(isinstance(value, list), reason, {"problem": "not_a_list"})
    result = []
    for item in value:
        require(isinstance(item, str) and item, reason, {"problem": "invalid_path", "path": item})
        path = PurePosixPath(item)
        require(not path.is_absolute(), reason, {"problem": "absolute_path", "path": item})
        require(".." not in path.parts, reason, {"problem": "parent_escape", "path": item})
        require(path.as_posix() == item, reason, {"problem": "non_canonical_path", "path": item})
        result.append(item)
    require(len(result) == len(set(result)), reason, {"problem": "duplicate_path"})
    return sorted(result)


def canonical_strings(value, reason):
    require(isinstance(value, list), reason, {"problem": "not_a_list"})
    require(all(isinstance(item, str) and item for item in value), reason, {"problem": "invalid_value"})
    require(len(value) == len(set(value)), reason, {"problem": "duplicate_value"})
    return sorted(value)


def validate_protocol(protocol):
    require(protocol.get("schemaVersion") == 1, "PROTOCOL_SCHEMA_MISMATCH")
    require(protocol.get("id") == "CG22-TERMINAL-PROOF-OF-CHANGE", "WRONG_PROTOCOL")
    require(protocol.get("status") in {"CANDIDATE", "QUALIFIED"}, "UNEXPECTED_PROTOCOL_STATUS")
    ceiling = protocol.get("authorityCeiling") or {}
    require(ceiling.get("createsNewCompletionDecision") is False, "NEW_COMPLETION_AUTHORITY_FORBIDDEN")
    require(ceiling.get("createsNewIntegrityDecision") is False, "NEW_INTEGRITY_AUTHORITY_FORBIDDEN")
    for field in (
        "executesMerge",
        "executesRecovery",
        "automaticForcePush",
        "automaticReset",
        "automaticRevert",
        "updatesPullRequest",
        "modifiesRemoteBranch",
        "modifiesCommit",
        "directMainUpdate",
        "repositoryAdministration",
        "consumerAdoption",
        "repositoryBlocking",
        "changesCg21CompletionSemantics",
        "changesCg07IntegritySemantics",
        "changesCg06TrustSemantics",
    ):
        require(ceiling.get(field) is False, "PROTOCOL_AUTHORITY_CEILING_INVALID", {"field": field})
    return protocol["proofPolicy"]


def validate_source(protocol, source, source_verification):
    policy = validate_protocol(protocol)
    require(isinstance(source, dict), "CG21_COMPLETION_ATTESTATION_REQUIRED")
    require(source.get("schemaVersion") == 1, "CG21_COMPLETION_SCHEMA_MISMATCH")
    require(source.get("protocolId") == protocol.get("sourceProtocol"), "CG21_COMPLETION_PROTOCOL_MISMATCH")
    require(source.get("status") == protocol.get("requiredSourceStatus"), "CG21_COMPLETION_STATUS_INVALID")
    require(source.get("decision") == protocol.get("requiredSourceDecision"), "CG21_COMPLETION_DECISION_INVALID")
    require(SHA256.fullmatch(source.get("completionAttestationId", "")) is not None, "CG21_COMPLETION_ATTESTATION_ID_INVALID")
    require(canonical_digest(completion_core(source)) == source.get("completionAttestationId"), "CG21_COMPLETION_ATTESTATION_ID_MISMATCH")

    repository = source.get("repository")
    require(isinstance(repository, str) and REPOSITORY.fullmatch(repository) is not None, "REPOSITORY_IDENTITY_INVALID")
    require(isinstance(source.get("pullRequestNumber"), int) and source["pullRequestNumber"] > 0, "PULL_REQUEST_IDENTITY_INVALID")
    require(source.get("baseBranch") == policy["requiredBaseBranch"], "BASE_BRANCH_MISMATCH")
    for field in ("authorizedBaseSha", "headSha", "mergeSha", "currentMainSha"):
        require(SHA40.fullmatch(source.get(field, "")) is not None, "SOURCE_GIT_ID_INVALID", {"field": field})
    for field in (
        "sourceExecutionAttestationId",
        "sourceExecutionAttestationSha256",
        "cg07ReceiptSha256",
        "cg07ObservedSha256",
        "cg07VerificationSha256",
    ):
        require(SHA256.fullmatch(source.get(field, "")) is not None, "SOURCE_DIGEST_INVALID", {"field": field})

    paths = canonical_paths(source.get("changedPaths"), "SOURCE_CHANGED_PATHS_INVALID")
    classes = canonical_strings(source.get("verificationClasses"), "SOURCE_VERIFICATION_CLASSES_INVALID")
    require(paths, "SOURCE_CHANGED_PATHS_EMPTY")
    require(classes, "SOURCE_VERIFICATION_CLASSES_EMPTY")
    require(source.get("mergeMethod") == policy["requiredMergeMethod"], "MERGE_METHOD_MISMATCH")
    require(source.get("authorizationConsumption") == policy["requiredAuthorizationConsumption"], "AUTHORIZATION_CONSUMPTION_INVALID")
    require(source.get("authorizationUseCount") == policy["requiredAuthorizationUseCount"], "AUTHORIZATION_USE_COUNT_INVALID")
    require(source.get("consumptionState") == policy["requiredConsumptionState"], "AUTHORIZATION_CONSUMPTION_STATE_INVALID")
    require(source.get("completionState") == policy["requiredCompletionState"], "COMPLETION_STATE_NOT_TERMINAL")
    require(source.get("recoveryDisposition") == policy["requiredRecoveryDisposition"], "RECOVERY_DISPOSITION_NOT_NO_ACTION")
    require(source.get("integrityAuthority") == policy["requiredIntegrityAuthority"], "INTEGRITY_AUTHORITY_DRIFT")
    require(source.get("currentMainRelation") in {"identical", "ahead"}, "CURRENT_MAIN_RELATION_INVALID")
    for field in (
        "executesMerge",
        "executesRecovery",
        "automaticForcePush",
        "automaticReset",
        "automaticRevert",
        "directMainUpdate",
        "repositoryAdministration",
        "consumerAdoption",
        "repositoryBlocking",
    ):
        require(source.get(field) is False, "SOURCE_AUTHORITY_ESCALATION", {"field": field})

    require(isinstance(source_verification, dict), "CG21_COMPLETION_VERIFICATION_REQUIRED")
    require(source_verification.get("schemaVersion") == 1, "CG21_VERIFICATION_SCHEMA_MISMATCH")
    require(source_verification.get("protocolId") == protocol.get("sourceProtocol"), "CG21_VERIFICATION_PROTOCOL_MISMATCH")
    require(source_verification.get("status") == protocol.get("requiredSourceVerificationStatus"), "CG21_VERIFICATION_STATUS_INVALID")
    require(source_verification.get("decision") == protocol.get("requiredSourceVerificationDecision"), "CG21_VERIFICATION_DECISION_INVALID")
    require(SHA256.fullmatch(source_verification.get("verificationId", "")) is not None, "CG21_VERIFICATION_ID_INVALID")
    require(canonical_digest(verification_core(source_verification)) == source_verification.get("verificationId"), "CG21_VERIFICATION_ID_MISMATCH")
    require(source_verification.get("completionAttestationId") == source["completionAttestationId"], "CG21_VERIFICATION_COMPLETION_BINDING_MISMATCH")
    require(source_verification.get("completionAttestationSha256") == canonical_digest(source), "CG21_VERIFICATION_COMPLETION_DIGEST_MISMATCH")
    require(source_verification.get("sourceExecutionAttestationId") == source["sourceExecutionAttestationId"], "CG21_VERIFICATION_EXECUTION_BINDING_MISMATCH")
    require(source_verification.get("cg07VerificationSha256") == source["cg07VerificationSha256"], "CG21_VERIFICATION_INTEGRITY_BINDING_MISMATCH")
    require(source_verification.get("completionState") == source["completionState"], "CG21_VERIFICATION_COMPLETION_STATE_MISMATCH")
    require(source_verification.get("recoveryDisposition") == source["recoveryDisposition"], "CG21_VERIFICATION_RECOVERY_MISMATCH")
    require(source_verification.get("repositoryBlocking") is False, "CG21_VERIFICATION_BLOCKING_ESCALATION")
    return paths, classes


def build(protocol, source, source_verification):
    paths, classes = validate_source(protocol, source, source_verification)
    policy = protocol["proofPolicy"]
    core = {
        "schemaVersion": 1,
        "protocolId": protocol["id"],
        "proofType": policy["proofType"],
        "caseId": source["caseId"],
        "repository": source["repository"],
        "pullRequestNumber": source["pullRequestNumber"],
        "baseBranch": source["baseBranch"],
        "authorizedBaseSha": source["authorizedBaseSha"],
        "headSha": source["headSha"],
        "mergeSha": source["mergeSha"],
        "changedPaths": paths,
        "verificationClasses": classes,
        "mergeMethod": source["mergeMethod"],
        "sourceCompletionAttestationId": source["completionAttestationId"],
        "sourceCompletionAttestationSha256": canonical_digest(source),
        "sourceCompletionVerificationId": source_verification["verificationId"],
        "sourceCompletionVerificationSha256": canonical_digest(source_verification),
        "sourceExecutionAttestationId": source["sourceExecutionAttestationId"],
        "sourceExecutionAttestationSha256": source["sourceExecutionAttestationSha256"],
        "cg07ReceiptSha256": source["cg07ReceiptSha256"],
        "cg07ObservedSha256": source["cg07ObservedSha256"],
        "cg07VerificationSha256": source["cg07VerificationSha256"],
        "authorizationConsumption": source["authorizationConsumption"],
        "authorizationUseCount": source["authorizationUseCount"],
        "consumptionState": source["consumptionState"],
        "currentMainSha": source["currentMainSha"],
        "currentMainRelation": source["currentMainRelation"],
        "completionState": source["completionState"],
        "recoveryDisposition": source["recoveryDisposition"],
        "completionAuthority": policy["requiredCompletionAuthority"],
        "integrityAuthority": source["integrityAuthority"],
        "terminalState": policy["terminalState"],
        "portableDeterministicReceipt": True,
        "createsNewCompletionDecision": False,
        "createsNewIntegrityDecision": False,
        "executesMerge": False,
        "executesRecovery": False,
        "directMainUpdate": False,
        "repositoryAdministration": False,
        "consumerAdoption": False,
        "repositoryBlocking": False,
    }
    return {
        **core,
        "status": protocol["outputs"]["status"],
        "decision": protocol["outputs"]["decision"],
        "proofId": canonical_digest(core),
    }


def parse_args():
    parser = argparse.ArgumentParser(description="CG-22 terminal Proof-of-Change builder")
    parser.add_argument("--protocol", required=True)
    parser.add_argument("--source-completion", required=True)
    parser.add_argument("--source-verification", required=True)
    return parser.parse_args()


def main():
    args = parse_args()
    try:
        out = build(load(args.protocol), load(args.source_completion), load(args.source_verification))
        print(json.dumps(out, indent=2, sort_keys=True))
        return 0
    except ProofOfChangeError as exc:
        out = {
            "schemaVersion": 1,
            "status": "REJECTED",
            "decision": "TERMINAL_PROOF_OF_CHANGE_NOT_PROVEN",
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
