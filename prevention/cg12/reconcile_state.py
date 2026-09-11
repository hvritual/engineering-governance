#!/usr/bin/env python3
import argparse
import json
import sys
from pathlib import Path


class ReconciliationError(Exception):
    def __init__(self, reason, detail=None):
        super().__init__(reason)
        self.reason = reason
        self.detail = detail


def require(condition, reason, detail=None):
    if not condition:
        raise ReconciliationError(reason, detail)


def load(path):
    return json.loads(Path(path).read_text())


def index_unique(items, key, duplicate_reason):
    result = {}
    for item in items:
        value = item.get(key)
        require(isinstance(value, str) and value, duplicate_reason, {"invalidKey": value})
        require(value not in result, duplicate_reason, {"duplicateKey": value})
        result[value] = item
    return result


def sorted_strings(value, reason):
    require(isinstance(value, list), reason)
    require(all(isinstance(item, str) and item for item in value), reason)
    require(len(value) == len(set(value)), reason)
    return sorted(value)


def check_authorization(subject, authorization):
    case_id = subject["caseId"]
    target = subject.get("target")
    if target is None:
        require(authorization.get("status") == "NOT_AUTHORIZED", "FALLBACK_BECAME_EXECUTABLE", {"caseId": case_id})
        require(authorization.get("grant") == "NONE", "FALLBACK_BECAME_EXECUTABLE", {"caseId": case_id})
        require(authorization.get("target") is None, "FALLBACK_BECAME_EXECUTABLE", {"caseId": case_id})
        require(authorization.get("allowedPaths") == [], "FALLBACK_BECAME_EXECUTABLE", {"caseId": case_id})
        return

    require(authorization.get("status") == "AUTHORIZED", "CG11_AUTHORIZATION_REQUIRED", {"caseId": case_id})
    require(authorization.get("grant") == "PATCH_BRANCH_ONLY", "CG11_GRANT_MISMATCH", {"caseId": case_id})
    require(authorization.get("authorizationId") == subject.get("expectedAuthorizationId"), "AUTHORIZATION_ID_MISMATCH", {"caseId": case_id})
    require(authorization.get("target") == target, "AUTHORIZATION_TARGET_MISMATCH", {"caseId": case_id})
    require(sorted_strings(authorization.get("allowedPaths"), "AUTHORIZATION_ALLOWED_PATHS_INVALID") == sorted(subject["authorizedPaths"]),
            "AUTHORIZATION_ALLOWED_PATHS_MISMATCH", {"caseId": case_id})
    require(sorted_strings(authorization.get("requiredVerificationClasses"), "AUTHORIZATION_VERIFICATION_CLASSES_INVALID") == sorted(subject["requiredVerificationClasses"]),
            "AUTHORIZATION_VERIFICATION_CLASSES_MISMATCH", {"caseId": case_id})
    require(authorization.get("directMainUpdate") is False, "DIRECT_MAIN_UPDATE_FORBIDDEN", {"caseId": case_id})
    require(authorization.get("mergeAuthorization") == "NOT_GRANTED", "MERGE_AUTHORIZATION_FORBIDDEN", {"caseId": case_id})
    require(authorization.get("repositoryAdministration") is False, "REPOSITORY_ADMINISTRATION_FORBIDDEN", {"caseId": case_id})
    require(authorization.get("consumerAdoption") == "NOT_ADOPTED", "CONSUMER_ADOPTION_FORBIDDEN", {"caseId": case_id})


def reconcile_subject(protocol, subject, authorization, observed):
    case_id = subject["caseId"]
    check_authorization(subject, authorization)

    if subject.get("target") is None:
        return {
            "caseId": case_id,
            "authorizationStatus": "NOT_AUTHORIZED",
            "disposition": protocol["dispositions"]["notAuthorized"],
            "patchRequired": False,
            "mergeAuthorization": False,
            "consumerMutation": False,
        }

    require(isinstance(observed, dict), "OBSERVED_SUBJECT_MISSING", {"caseId": case_id})
    target = subject["target"]
    require(observed.get("repository") == target["repository"], "OBSERVED_REPOSITORY_MISMATCH", {"caseId": case_id})
    require(observed.get("baseBranch") == target["baseBranch"], "OBSERVED_BASE_BRANCH_MISMATCH", {"caseId": case_id})
    require(observed.get("liveBaseSha") == target["baseSha"], "LIVE_BASE_SHA_MISMATCH", {
        "caseId": case_id,
        "expected": target["baseSha"],
        "actual": observed.get("liveBaseSha"),
    })
    mutated = observed.get("consumerMutated")
    require(isinstance(mutated, bool), "CONSUMER_MUTATION_STATE_INVALID", {"caseId": case_id})

    existing = sorted_strings(observed.get("existingAuthorizedPaths"), "OBSERVED_AUTHORIZED_PATHS_INVALID")
    expected_paths = sorted(subject["authorizedPaths"])
    require(existing == expected_paths, "AUTHORIZED_PATH_MISSING_OR_BROADENED", {
        "caseId": case_id,
        "expected": expected_paths,
        "actual": existing,
    })

    blobs = observed.get("blobs")
    require(isinstance(blobs, dict), "OBSERVED_BLOBS_INVALID", {"caseId": case_id})
    drift = []
    for path, expected_blob in sorted(subject["expectedBlobs"].items()):
        actual = blobs.get(path)
        if actual != expected_blob:
            drift.append({"path": path, "expected": expected_blob, "actual": actual})

    fresh = observed.get("freshVerification")
    require(isinstance(fresh, dict), "FRESH_VERIFICATION_INVALID", {"caseId": case_id})
    verification_failures = []
    for verification_class in subject["freshVerificationRequired"]:
        if fresh.get(verification_class) != "PASS":
            verification_failures.append({
                "class": verification_class,
                "actual": fresh.get(verification_class),
            })

    carried = observed.get("identityCarriedVerification")
    require(isinstance(carried, dict), "IDENTITY_CARRIED_VERIFICATION_INVALID", {"caseId": case_id})
    for verification_class, policy in subject.get("identityCarriedVerification", {}).items():
        if carried.get(verification_class) != "IDENTITY_MATCH":
            verification_failures.append({
                "class": verification_class,
                "actual": carried.get(verification_class),
            })
        for path in policy.get("requiresExactBlobPaths", []):
            if any(item["path"] == path for item in drift):
                verification_failures.append({
                    "class": verification_class,
                    "actual": "IDENTITY_DRIFT",
                    "path": path,
                })

    preserved = sorted_strings(observed.get("preservedVerificationClasses"), "PRESERVED_VERIFICATION_CLASSES_INVALID")
    require(preserved == sorted(subject["requiredVerificationClasses"]), "REQUIRED_VERIFICATION_WEAKENED", {
        "caseId": case_id,
        "expected": sorted(subject["requiredVerificationClasses"]),
        "actual": preserved,
    })

    if drift:
        disposition = protocol["dispositions"]["patchRequired"]
        patch_required = True
        reason = "IMPLEMENTATION_IDENTITY_DRIFT"
    elif verification_failures:
        disposition = protocol["dispositions"]["investigationRequired"]
        patch_required = False
        reason = "VERIFICATION_NOT_PROVEN"
    else:
        disposition = protocol["dispositions"]["alreadySatisfied"]
        patch_required = False
        reason = "CURRENT_IMPLEMENTATION_SATISFIES_AUTHORIZED_PLAN"

    if mutated:
        if disposition == protocol["dispositions"]["alreadySatisfied"]:
            raise ReconciliationError("PATCH_WHEN_ALREADY_SATISFIED", {"caseId": case_id})
        raise ReconciliationError("CONSUMER_MUTATION_DURING_RECONCILIATION", {"caseId": case_id})

    return {
        "caseId": case_id,
        "authorizationStatus": "AUTHORIZED",
        "authorizationId": authorization["authorizationId"],
        "repository": target["repository"],
        "baseSha": target["baseSha"],
        "workBranch": target["workBranch"],
        "allowedPaths": sorted(subject["authorizedPaths"]),
        "preservedVerificationClasses": sorted(subject["requiredVerificationClasses"]),
        "disposition": disposition,
        "reason": reason,
        "patchRequired": patch_required,
        "identityDrift": drift,
        "verificationFailures": verification_failures,
        "consumerMutation": False,
        "directMainUpdate": False,
        "mergeAuthorization": False,
        "repositoryAdministration": False,
        "consumerAdoption": False,
    }


def reconcile_document(protocol, subjects_doc, authorizations_doc, observed_doc):
    require(protocol.get("schemaVersion") == 1, "PROTOCOL_SCHEMA_MISMATCH")
    require(protocol.get("id") == "CG12-AUTHORIZED-IMPLEMENTATION-RECONCILIATION", "WRONG_PROTOCOL")
    require(protocol.get("status") in {"CANDIDATE", "QUALIFIED"}, "UNEXPECTED_PROTOCOL_STATUS")
    require(subjects_doc.get("schemaVersion") == 1, "SUBJECTS_SCHEMA_MISMATCH")
    require(subjects_doc.get("protocolId") == protocol.get("id"), "SUBJECTS_PROTOCOL_MISMATCH")

    subjects = index_unique(subjects_doc.get("subjects", []), "caseId", "DUPLICATE_OR_INVALID_SUBJECT")
    authorizations = index_unique(authorizations_doc.get("authorizations", []), "caseId", "DUPLICATE_OR_INVALID_AUTHORIZATION")
    observed = index_unique(observed_doc.get("subjects", []), "caseId", "DUPLICATE_OR_INVALID_OBSERVED_SUBJECT")

    require(set(subjects) == set(authorizations), "SUBJECT_AUTHORIZATION_SET_MISMATCH")
    expected_observed = {case_id for case_id, subject in subjects.items() if subject.get("target") is not None}
    require(set(observed) == expected_observed, "OBSERVED_SUBJECT_SET_MISMATCH", {
        "expected": sorted(expected_observed),
        "actual": sorted(observed),
    })

    receipts = []
    for case_id in sorted(subjects):
        receipts.append(reconcile_subject(
            protocol,
            subjects[case_id],
            authorizations[case_id],
            observed.get(case_id),
        ))

    return {
        "schemaVersion": 1,
        "protocolId": protocol["id"],
        "status": "RECONCILED",
        "receipts": receipts,
    }


def parse_args():
    parser = argparse.ArgumentParser(description="CG-12 authorized implementation reconciliation")
    parser.add_argument("--protocol", required=True)
    parser.add_argument("--subjects", required=True)
    parser.add_argument("--authorizations", required=True)
    parser.add_argument("--observed", required=True)
    return parser.parse_args()


def main():
    args = parse_args()
    try:
        result = reconcile_document(load(args.protocol), load(args.subjects), load(args.authorizations), load(args.observed))
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    except ReconciliationError as exc:
        value = {
            "schemaVersion": 1,
            "status": "REJECTED",
            "decision": "IMPLEMENTATION_RECONCILIATION_NOT_PROVEN",
            "reason": exc.reason,
        }
        if exc.detail is not None:
            value["detail"] = exc.detail
        print(json.dumps(value, indent=2, sort_keys=True))
        return 2


if __name__ == "__main__":
    sys.exit(main())
