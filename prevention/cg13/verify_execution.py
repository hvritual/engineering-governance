#!/usr/bin/env python3
import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path


class AttestationError(Exception):
    def __init__(self, reason, detail=None):
        super().__init__(reason)
        self.reason = reason
        self.detail = detail


def require(condition, reason, detail=None):
    if not condition:
        raise AttestationError(reason, detail)


def load(path):
    return json.loads(Path(path).read_text())


def canonical_digest(value):
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def git(repo, args):
    cp = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, check=False)
    require(cp.returncode == 0, "GIT_READBACK_FAILED", {"args": args, "stderr": cp.stderr.decode("utf-8", "replace")[-4000:]})
    return cp.stdout


def sorted_unique_strings(value, reason):
    require(isinstance(value, list), reason)
    require(all(isinstance(item, str) and item for item in value), reason)
    require(len(value) == len(set(value)), reason)
    return sorted(value)


def verify(protocol, receipt, execution, verification, repo):
    require(protocol.get("id") == "CG13-CONTROLLED-PATCH-EXECUTION", "WRONG_PROTOCOL")
    require(protocol.get("status") in {"CANDIDATE", "QUALIFIED"}, "UNEXPECTED_PROTOCOL_STATUS")
    require(receipt.get("disposition") == protocol["execution"]["requiredDisposition"], "CG12_DISPOSITION_NOT_EXECUTABLE")
    require(receipt.get("patchRequired") is True, "PATCH_REQUIRED_TRUE_REQUIRED")
    require(execution.get("status") == "PATCH_APPLIED_UNVERIFIED", "EXECUTION_STATUS_INVALID")

    for field in ("caseId", "repository", "baseSha", "workBranch"):
        require(execution.get(field) == receipt.get(field), "EXECUTION_RECEIPT_BINDING_MISMATCH", {"field": field})
    require(execution.get("sourceReconciliationSha256") == canonical_digest(receipt), "SOURCE_RECONCILIATION_DIGEST_MISMATCH")
    require(execution.get("commitAuthorization") == "NOT_GRANTED", "COMMIT_AUTHORIZATION_FORBIDDEN")
    require(execution.get("pullRequestAuthorization") == "NOT_GRANTED", "PULL_REQUEST_AUTHORIZATION_FORBIDDEN")
    require(execution.get("mergeAuthorization") == "NOT_GRANTED", "MERGE_AUTHORIZATION_FORBIDDEN")
    require(execution.get("directMainUpdate") is False, "DIRECT_MAIN_UPDATE_FORBIDDEN")
    require(execution.get("repositoryAdministration") is False, "REPOSITORY_ADMINISTRATION_FORBIDDEN")
    require(execution.get("consumerAdoption") is False, "CONSUMER_ADOPTION_FORBIDDEN")

    allowed = sorted_unique_strings(receipt.get("allowedPaths"), "ALLOWED_PATHS_INVALID")
    required_classes = sorted_unique_strings(receipt.get("preservedVerificationClasses"), "VERIFICATION_CLASSES_INVALID")
    require(execution.get("allowedPaths") == allowed, "EXECUTION_ALLOWLIST_BINDING_MISMATCH")
    require(execution.get("requiredVerificationClasses") == required_classes, "EXECUTION_VERIFICATION_CLASS_BINDING_MISMATCH")
    require(isinstance(execution.get("patchSha256"), str) and re.fullmatch(r"[0-9a-f]{64}", execution["patchSha256"]), "PATCH_DIGEST_INVALID")

    repo = Path(repo).resolve()
    head = git(repo, ["rev-parse", "HEAD"]).decode().strip()
    branch = git(repo, ["branch", "--show-current"]).decode().strip()
    require(head == receipt["baseSha"], "BASE_MOVED_AFTER_PATCH", {"expected": receipt["baseSha"], "actual": head})
    require(branch == receipt["workBranch"], "WORK_BRANCH_MOVED_AFTER_PATCH", {"expected": receipt["workBranch"], "actual": branch})
    require(branch != "main", "WORK_BRANCH_MUST_NOT_BE_MAIN")

    staged = git(repo, ["diff", "--cached", "--name-only", "-z", "--"])
    require(staged == b"", "PATCH_MUST_REMAIN_UNCOMMITTED_AND_UNSTAGED")
    status = git(repo, ["status", "--porcelain", "--untracked-files=all"]).decode()
    require(not any(line.startswith("?? ") for line in status.splitlines()), "POST_APPLY_UNTRACKED_FILE_FORBIDDEN")

    actual_paths = sorted(p.decode("utf-8") for p in git(repo, ["diff", "--name-only", "-z", "--"]).split(b"\0") if p)
    expected_paths = sorted_unique_strings(execution.get("changedPaths"), "EXECUTION_CHANGED_PATHS_INVALID")
    require(actual_paths == expected_paths, "EXECUTION_DIFF_PATHS_DRIFT", {"expected": expected_paths, "actual": actual_paths})
    require(bool(actual_paths), "EXECUTION_DIFF_EMPTY")
    require(set(actual_paths).issubset(set(allowed)), "EXECUTION_DIFF_OUTSIDE_ALLOWLIST")

    diff_bytes = git(repo, ["diff", "--binary", "--full-index", "--no-ext-diff", "--"])
    actual_diff_sha = hashlib.sha256(diff_bytes).hexdigest()
    require(actual_diff_sha == execution.get("diffSha256"), "EXECUTION_DIFF_DIGEST_MISMATCH", {
        "expected": execution.get("diffSha256"), "actual": actual_diff_sha,
    })

    require(verification.get("schemaVersion") == 1, "VERIFICATION_SCHEMA_MISMATCH")
    for field in ("caseId", "repository", "baseSha", "workBranch"):
        require(verification.get(field) == receipt.get(field), "VERIFICATION_BINDING_MISMATCH", {"field": field})
    require(sorted_unique_strings(verification.get("changedPaths"), "VERIFICATION_PATHS_INVALID") == actual_paths, "VERIFICATION_PATHS_MISMATCH")
    require(verification.get("diffSha256") == actual_diff_sha, "VERIFICATION_DIFF_DIGEST_MISMATCH")

    checks = verification.get("checks")
    require(isinstance(checks, list), "VERIFICATION_CHECKS_INVALID")
    classes = [item.get("class") for item in checks]
    require(all(isinstance(c, str) and c for c in classes), "VERIFICATION_CLASS_INVALID")
    require(len(classes) == len(set(classes)), "VERIFICATION_CLASS_DUPLICATE")
    require(sorted(classes) == required_classes, "VERIFICATION_CLASS_SET_MISMATCH", {
        "required": required_classes, "actual": sorted(classes),
    })
    for item in checks:
        require(item.get("outcome") == "PASS", "VERIFICATION_NOT_PASS", {"class": item.get("class"), "outcome": item.get("outcome")})
        digest = item.get("evidenceSha256")
        require(isinstance(digest, str) and re.fullmatch(r"[0-9a-f]{64}", digest), "VERIFICATION_EVIDENCE_DIGEST_INVALID", {"class": item.get("class")})

    verification_sha = canonical_digest(verification)
    execution_sha = canonical_digest(execution)
    attestation_core = {
        "schemaVersion": 1,
        "protocolId": protocol["id"],
        "caseId": receipt["caseId"],
        "repository": receipt["repository"],
        "baseSha": receipt["baseSha"],
        "workBranch": receipt["workBranch"],
        "sourceReconciliationSha256": canonical_digest(receipt),
        "executionReceiptSha256": execution_sha,
        "changedPaths": actual_paths,
        "diffSha256": actual_diff_sha,
        "verificationReceiptSha256": verification_sha,
        "verificationClasses": required_classes,
        "commitAuthorization": "NOT_GRANTED",
        "pullRequestAuthorization": "NOT_GRANTED",
        "mergeAuthorization": "NOT_GRANTED",
        "directMainUpdate": False,
        "repositoryAdministration": False,
        "consumerAdoption": False,
    }
    return {
        **attestation_core,
        "status": "PATCH_EXECUTION_ATTESTED",
        "decision": "CONTROLLED_PATCH_EXECUTION_VERIFIED",
        "attestationId": canonical_digest(attestation_core),
    }


def parse_args():
    parser = argparse.ArgumentParser(description="CG-13 controlled patch execution verifier")
    parser.add_argument("--protocol", required=True)
    parser.add_argument("--receipt", required=True)
    parser.add_argument("--execution", required=True)
    parser.add_argument("--verification", required=True)
    parser.add_argument("--repo", required=True)
    return parser.parse_args()


def main():
    args = parse_args()
    try:
        result = verify(load(args.protocol), load(args.receipt), load(args.execution), load(args.verification), args.repo)
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    except AttestationError as exc:
        out = {"schemaVersion": 1, "status": "REJECTED", "decision": "CONTROLLED_PATCH_EXECUTION_NOT_VERIFIED", "reason": exc.reason}
        if exc.detail is not None:
            out["detail"] = exc.detail
        print(json.dumps(out, indent=2, sort_keys=True))
        return 2


if __name__ == "__main__":
    sys.exit(main())
