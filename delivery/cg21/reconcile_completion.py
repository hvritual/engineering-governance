#!/usr/bin/env python3
import argparse
import hashlib
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path, PurePosixPath


SHA40 = re.compile(r"^[0-9a-f]{40}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")


class CompletionReconciliationError(Exception):
    def __init__(self, reason, detail=None):
        super().__init__(reason)
        self.reason = reason
        self.detail = detail


def require(condition, reason, detail=None):
    if not condition:
        raise CompletionReconciliationError(reason, detail)


def load(path):
    return json.loads(Path(path).read_text())


def canonical_digest(value):
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def execution_core(attestation):
    return {k: v for k, v in attestation.items() if k not in {"status", "decision", "executionAttestationId"}}


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


def validate_protocols(protocol, cg07_protocol):
    require(protocol.get("schemaVersion") == 1, "PROTOCOL_SCHEMA_MISMATCH")
    require(protocol.get("id") == "CG21-EXECUTION-INTEGRITY-COMPLETION", "WRONG_PROTOCOL")
    require(protocol.get("status") in {"CANDIDATE", "QUALIFIED"}, "UNEXPECTED_PROTOCOL_STATUS")
    require(cg07_protocol.get("schemaVersion") == 1, "CG07_PROTOCOL_SCHEMA_MISMATCH")
    require(cg07_protocol.get("id") == protocol.get("integrityProtocol"), "CG07_PROTOCOL_MISMATCH")
    require(cg07_protocol.get("status") == "QUALIFIED", "CG07_PROTOCOL_NOT_QUALIFIED")

    policy = protocol.get("convergencePolicy", {})
    delivery = cg07_protocol.get("supportedDelivery", {})
    recovery = cg07_protocol.get("recovery", {})
    ceiling = cg07_protocol.get("authorityCeiling", {})
    require(delivery.get("requiredBaseBranch") == policy.get("requiredBaseBranch"), "CG07_BASE_BRANCH_POLICY_DRIFT")
    require(policy.get("requiredMergeMethod") in set(delivery.get("mergeMethods") or []), "CG07_MERGE_METHOD_POLICY_DRIFT")
    require(recovery.get("integrityPassDisposition") == policy.get("integrityPassRecoveryDisposition"), "CG07_PASS_DISPOSITION_POLICY_DRIFT")
    require(recovery.get("integrityFailureDisposition") == policy.get("integrityFailureRecoveryDisposition"), "CG07_FAILURE_DISPOSITION_POLICY_DRIFT")
    require(ceiling.get("automaticDestructiveRecovery") is False, "CG07_DESTRUCTIVE_RECOVERY_AUTHORIZED")
    require(protocol.get("authorityCeiling", {}).get("changesCg07TrustSemantics") is False, "CG07_TRUST_SEMANTICS_CHANGED")
    return policy


def validate_source(protocol, source):
    require(isinstance(source, dict), "CG20_MERGE_EXECUTION_ATTESTATION_REQUIRED")
    require(source.get("schemaVersion") == 1, "CG20_SCHEMA_MISMATCH")
    require(source.get("protocolId") == protocol.get("sourceProtocol"), "CG20_PROTOCOL_MISMATCH")
    require(source.get("status") == protocol.get("requiredSourceStatus"), "CG20_EXECUTION_STATUS_INVALID")
    require(source.get("decision") == protocol.get("requiredSourceDecision"), "CG20_EXECUTION_DECISION_INVALID")
    require(SHA256.fullmatch(source.get("executionAttestationId", "")) is not None, "CG20_EXECUTION_ATTESTATION_ID_INVALID")
    require(canonical_digest(execution_core(source)) == source.get("executionAttestationId"), "CG20_EXECUTION_ATTESTATION_ID_MISMATCH")

    policy = protocol["convergencePolicy"]
    require(source.get("provider") == policy["requiredProvider"], "PROVIDER_MISMATCH")
    require(source.get("externalExecutor") == policy["requiredExternalExecutor"], "EXTERNAL_EXECUTOR_MISMATCH")
    repository = source.get("repository")
    require(isinstance(repository, str) and repository.count("/") == 1, "REPOSITORY_IDENTITY_INVALID")
    require(isinstance(source.get("pullRequestNumber"), int) and source["pullRequestNumber"] > 0, "PULL_REQUEST_IDENTITY_INVALID")
    require(source.get("baseBranch") == policy["requiredBaseBranch"], "CG07_REQUIRED_BASE_BRANCH_MISMATCH")
    require(isinstance(source.get("headBranch"), str) and source["headBranch"], "HEAD_BRANCH_REQUIRED")

    for field in ("authorizedBaseSha", "headSha", "expectedHeadSha", "mergeSha", "mergeParentSha", "mergeTreeSha", "headTreeSha", "postMergeBaseSha"):
        require(SHA40.fullmatch(source.get(field, "")) is not None, "CG20_GIT_ID_INVALID", {"field": field})
    for field in ("sourceAuthorizationId", "sourceAuthorizationSha256", "mergeExecutionRequestId", "mergeExecutionRequestSha256", "externalMergeReceiptSha256"):
        require(SHA256.fullmatch(source.get(field, "")) is not None, "CG20_DIGEST_INVALID", {"field": field})

    paths = canonical_paths(source.get("changedPaths"), "CG20_CHANGED_PATHS_INVALID")
    classes = canonical_paths(source.get("verificationClasses"), "CG20_VERIFICATION_CLASSES_INVALID")
    require(bool(paths), "CG20_CHANGED_PATHS_EMPTY")
    require(bool(classes), "CG20_VERIFICATION_CLASSES_EMPTY")

    require(source.get("mergeMethod") == policy["requiredMergeMethod"], "MERGE_METHOD_MISMATCH")
    require(source.get("authorizationConsumption") == policy["requiredAuthorizationConsumption"], "AUTHORIZATION_NOT_CONSUMED_BY_SUCCESSFUL_MERGE")
    require(source.get("authorizationUseCount") == policy["requiredAuthorizationUseCount"], "AUTHORIZATION_USE_COUNT_INVALID")
    require(source.get("consumptionState") == policy["requiredConsumptionState"], "AUTHORIZATION_CONSUMPTION_STATE_INVALID")
    require(source.get("providerCompareAndSwap") == policy["requiredProviderCompareAndSwap"], "PROVIDER_CAS_MISMATCH")
    require(source.get("providerSingleUseBoundary") == policy["requiredProviderSingleUseBoundary"], "PROVIDER_SINGLE_USE_BOUNDARY_MISMATCH")
    require(source.get("secondConsumptionStateSource") == policy["requiredSecondConsumptionStateSource"], "SECOND_CONSUMPTION_SOURCE_MISMATCH")
    require(source.get("expectedHeadSha") == source.get("headSha"), "EXPECTED_HEAD_SHA_MISMATCH")
    require(source.get("postMergeBaseSha") == source.get("mergeSha"), "POST_MERGE_BASE_IDENTITY_MISMATCH")
    require(source.get("mergeParentSha") == source.get("authorizedBaseSha"), "MERGE_PARENT_IDENTITY_MISMATCH")
    require(source.get("mergeTreeSha") == source.get("headTreeSha"), "MERGE_TREE_HEAD_TREE_MISMATCH")
    require(source.get("autoMerge") is False, "AUTO_MERGE_FORBIDDEN")
    require(source.get("directMainUpdate") is False, "DIRECT_MAIN_UPDATE_FORBIDDEN")
    require(source.get("repositoryAdministration") is False, "REPOSITORY_ADMINISTRATION_FORBIDDEN")
    require(source.get("consumerAdoption") is False, "CONSUMER_ADOPTION_FORBIDDEN")
    return paths, classes


def build_cg07_receipt(protocol, cg07_protocol, source):
    paths, _ = validate_source(protocol, source)
    validate_protocols(protocol, cg07_protocol)
    return {
        "schemaVersion": 1,
        "protocolId": cg07_protocol["id"],
        "delivery": {
            "repository": source["repository"],
            "pullRequest": source["pullRequestNumber"],
            "baseBranch": source["baseBranch"],
            "reviewedHeadSha": source["headSha"],
            "preMainSha": source["authorizedBaseSha"],
            "mergeMethod": source["mergeMethod"],
            "postMergeSha": source["mergeSha"],
            "reviewedPaths": paths,
        },
        "mergeRequest": {
            "expectedHeadSha": source["expectedHeadSha"],
            "nonForce": True,
        },
        "recovery": {
            "requestedDisposition": protocol["convergencePolicy"]["integrityPassRecoveryDisposition"],
            "automaticForcePush": False,
            "automaticReset": False,
            "automaticRevert": False,
        },
    }


def run_cg07(cg07_protocol_path, cg07_verifier_path, receipt, observed):
    verifier = Path(cg07_verifier_path)
    require(verifier.is_file(), "CG07_VERIFIER_NOT_FOUND", {"path": str(verifier)})
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        receipt_path = root / "receipt.json"
        observed_path = root / "observed.json"
        receipt_path.write_text(json.dumps(receipt, indent=2, sort_keys=True))
        observed_path.write_text(json.dumps(observed, indent=2, sort_keys=True))
        cp = subprocess.run(
            [sys.executable, str(verifier), "--protocol", str(cg07_protocol_path), "--receipt", str(receipt_path), "--observed", str(observed_path)],
            capture_output=True,
            text=True,
        )
    try:
        result = json.loads(cp.stdout)
    except Exception:
        raise CompletionReconciliationError("CG07_VERIFIER_OUTPUT_INVALID", {"returnCode": cp.returncode, "stdout": cp.stdout[-2000:], "stderr": cp.stderr[-2000:]})
    return cp.returncode, result


def reconcile(protocol, cg07_protocol, cg07_protocol_path, cg07_verifier_path, source, observed):
    policy = validate_protocols(protocol, cg07_protocol)
    paths, classes = validate_source(protocol, source)
    require(isinstance(observed, dict), "OBSERVED_POST_MERGE_SNAPSHOT_REQUIRED")

    receipt = build_cg07_receipt(protocol, cg07_protocol, source)
    return_code, integrity = run_cg07(cg07_protocol_path, cg07_verifier_path, receipt, observed)
    require(return_code == 0, "CG07_POST_MERGE_INTEGRITY_NOT_PROVEN", {"cg07": integrity})
    require(integrity.get("status") == protocol["requiredIntegrityStatus"], "CG07_INTEGRITY_STATUS_INVALID", integrity)
    require(integrity.get("decision") == protocol["requiredIntegrityDecision"], "CG07_INTEGRITY_DECISION_INVALID", integrity)
    require(integrity.get("recoveryDisposition") == policy["integrityPassRecoveryDisposition"], "CG07_RECOVERY_DISPOSITION_INVALID", integrity)
    require(integrity.get("repositoryBlocking") is False, "CG07_REPOSITORY_BLOCKING_ESCALATION")

    core = {
        "schemaVersion": 1,
        "protocolId": protocol["id"],
        "caseId": source["caseId"],
        "repository": source["repository"],
        "sourceExecutionAttestationId": source["executionAttestationId"],
        "sourceExecutionAttestationSha256": canonical_digest(source),
        "cg07ProtocolId": cg07_protocol["id"],
        "cg07ReceiptSha256": canonical_digest(receipt),
        "cg07ObservedSha256": canonical_digest(observed),
        "cg07VerificationSha256": canonical_digest(integrity),
        "pullRequestNumber": source["pullRequestNumber"],
        "baseBranch": source["baseBranch"],
        "authorizedBaseSha": source["authorizedBaseSha"],
        "headSha": source["headSha"],
        "mergeSha": source["mergeSha"],
        "changedPaths": paths,
        "verificationClasses": classes,
        "mergeMethod": source["mergeMethod"],
        "authorizationConsumption": source["authorizationConsumption"],
        "authorizationUseCount": source["authorizationUseCount"],
        "consumptionState": source["consumptionState"],
        "currentMainSha": integrity["currentMainSha"],
        "currentMainRelation": integrity["currentMainRelation"],
        "recoveryDisposition": integrity["recoveryDisposition"],
        "completionState": policy["completionState"],
        "integrityAuthority": "CG07",
        "executesMerge": False,
        "executesRecovery": False,
        "automaticForcePush": False,
        "automaticReset": False,
        "automaticRevert": False,
        "directMainUpdate": False,
        "repositoryAdministration": False,
        "consumerAdoption": False,
        "repositoryBlocking": False,
    }
    return {
        **core,
        "status": protocol["outputs"]["status"],
        "decision": protocol["outputs"]["decision"],
        "completionAttestationId": canonical_digest(core),
    }


def parse_args():
    parser = argparse.ArgumentParser(description="CG-21 execution-to-integrity completion reconciliation")
    parser.add_argument("--protocol", required=True)
    parser.add_argument("--cg07-protocol", required=True)
    parser.add_argument("--cg07-verifier", required=True)
    parser.add_argument("--source-execution", required=True)
    parser.add_argument("--observed", required=True)
    return parser.parse_args()


def main():
    args = parse_args()
    try:
        protocol = load(args.protocol)
        cg07_protocol = load(args.cg07_protocol)
        out = reconcile(protocol, cg07_protocol, args.cg07_protocol, args.cg07_verifier, load(args.source_execution), load(args.observed))
        print(json.dumps(out, indent=2, sort_keys=True))
        return 0
    except CompletionReconciliationError as exc:
        out = {
            "schemaVersion": 1,
            "status": "REJECTED",
            "decision": "DELIVERY_COMPLETION_NOT_PROVEN",
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
