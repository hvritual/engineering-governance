#!/usr/bin/env python3
import argparse
import importlib.util
import json
import sys
from pathlib import Path


class VerificationError(Exception):
    def __init__(self, reason, detail=None):
        super().__init__(reason)
        self.reason = reason
        self.detail = detail


def require(condition, reason, detail=None):
    if not condition:
        raise VerificationError(reason, detail)


def load(path):
    return json.loads(Path(path).read_text())


def load_reconciler(path):
    spec = importlib.util.spec_from_file_location("cg12_reconcile_state", path)
    require(spec is not None and spec.loader is not None, "RECONCILER_LOAD_FAILED")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def verify_document(protocol, subjects, authorizations, observed, actual, reconciler):
    expected = reconciler.reconcile_document(protocol, subjects, authorizations, observed)
    require(actual == expected, "RECONCILIATION_OUTPUT_MISMATCH")

    receipts = actual.get("receipts", [])
    require(len(receipts) == len(subjects.get("subjects", [])), "RECEIPT_COUNT_MISMATCH")
    by_case = {receipt["caseId"]: receipt for receipt in receipts}
    require(len(by_case) == len(receipts), "DUPLICATE_RECEIPT_CASE")

    real_subjects = [subject for subject in subjects["subjects"] if subject.get("target") is not None]
    fallback_subjects = [subject for subject in subjects["subjects"] if subject.get("target") is None]
    qualification = protocol["qualification"]
    require(len(real_subjects) >= qualification["minimumRealSubjects"], "INSUFFICIENT_REAL_SUBJECTS")

    no_change = 0
    patch_required = 0
    investigation_required = 0
    for subject in real_subjects:
        receipt = by_case[subject["caseId"]]
        if receipt["disposition"] == protocol["dispositions"]["alreadySatisfied"]:
            no_change += 1
        elif receipt["disposition"] == protocol["dispositions"]["patchRequired"]:
            patch_required += 1
        elif receipt["disposition"] == protocol["dispositions"]["investigationRequired"]:
            investigation_required += 1
        else:
            raise VerificationError("UNKNOWN_REAL_SUBJECT_DISPOSITION", {"caseId": subject["caseId"], "disposition": receipt["disposition"]})
        require(receipt.get("consumerMutation") is False, "CONSUMER_MUTATION_FORBIDDEN", {"caseId": subject["caseId"]})
        require(receipt.get("directMainUpdate") is False, "DIRECT_MAIN_UPDATE_FORBIDDEN", {"caseId": subject["caseId"]})
        require(receipt.get("mergeAuthorization") is False, "MERGE_AUTHORIZATION_FORBIDDEN", {"caseId": subject["caseId"]})
        require(receipt.get("repositoryAdministration") is False, "REPOSITORY_ADMINISTRATION_FORBIDDEN", {"caseId": subject["caseId"]})
        require(receipt.get("consumerAdoption") is False, "CONSUMER_ADOPTION_FORBIDDEN", {"caseId": subject["caseId"]})
        require(receipt.get("preservedVerificationClasses") == sorted(subject["requiredVerificationClasses"]),
                "REQUIRED_VERIFICATION_WEAKENED", {"caseId": subject["caseId"]})

    if qualification.get("requireAtLeastOneAlreadySatisfiedRealSubject"):
        require(no_change >= 1, "NO_ALREADY_SATISFIED_REAL_SUBJECT")
    if qualification.get("requireAtLeastOneInvestigationRealSubject"):
        require(investigation_required >= 1, "NO_INVESTIGATION_REAL_SUBJECT")
    if qualification.get("requireNoPatchRequiredInQualification"):
        require(patch_required == 0, "UNEXPECTED_PATCH_REQUIRED_IN_QUALIFICATION", {"patchRequired": patch_required})

    for subject in fallback_subjects:
        receipt = by_case[subject["caseId"]]
        require(receipt.get("authorizationStatus") == "NOT_AUTHORIZED", "FALLBACK_BECAME_EXECUTABLE")
        require(receipt.get("disposition") == protocol["dispositions"]["notAuthorized"], "FALLBACK_DISPOSITION_MISMATCH")
        require(receipt.get("patchRequired") is False, "FALLBACK_BECAME_EXECUTABLE")

    ceiling = protocol.get("authorityCeiling", {})
    for field in (
        "modifiesConsumerFiles",
        "createsConsumerBranch",
        "opensConsumerPullRequest",
        "authorizesMerge",
        "directMainUpdate",
        "repositoryAdministration",
        "consumerAdoption",
        "repositoryBlocking",
    ):
        require(ceiling.get(field) is False, "AUTHORITY_CEILING_EXCEEDED", {"field": field})

    return {
        "schemaVersion": 1,
        "status": "PASS",
        "decision": "AUTHORIZED_IMPLEMENTATION_RECONCILIATION_VERIFIED",
        "realSubjectCount": len(real_subjects),
        "noChangeRequiredCount": no_change,
        "patchRequiredCount": patch_required,
        "investigationRequiredCount": investigation_required,
        "fallbackNoActionCount": len(fallback_subjects),
        "consumerMutation": False,
        "directMainUpdate": False,
        "mergeAuthorization": False,
        "repositoryAdministration": False,
        "consumerAdoption": False,
        "repositoryBlocking": False,
    }


def parse_args():
    parser = argparse.ArgumentParser(description="CG-12 implementation reconciliation verifier")
    parser.add_argument("--protocol", required=True)
    parser.add_argument("--subjects", required=True)
    parser.add_argument("--authorizations", required=True)
    parser.add_argument("--observed", required=True)
    parser.add_argument("--reconciliations", required=True)
    parser.add_argument("--reconciler", required=True)
    return parser.parse_args()


def main():
    args = parse_args()
    try:
        result = verify_document(
            load(args.protocol),
            load(args.subjects),
            load(args.authorizations),
            load(args.observed),
            load(args.reconciliations),
            load_reconciler(args.reconciler),
        )
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    except Exception as exc:
        if isinstance(exc, VerificationError):
            reason, detail = exc.reason, exc.detail
        else:
            reason, detail = "RECONCILIATION_VERIFIER_ERROR", {"error": str(exc)}
        value = {
            "schemaVersion": 1,
            "status": "REJECTED",
            "decision": "AUTHORIZED_IMPLEMENTATION_RECONCILIATION_NOT_VERIFIED",
            "reason": reason,
        }
        if detail is not None:
            value["detail"] = detail
        print(json.dumps(value, indent=2, sort_keys=True))
        return 2


if __name__ == "__main__":
    sys.exit(main())
