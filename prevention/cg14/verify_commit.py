#!/usr/bin/env python3
import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path


class CommitAttestationError(Exception):
    def __init__(self, reason, detail=None):
        super().__init__(reason)
        self.reason = reason
        self.detail = detail


def require(condition, reason, detail=None):
    if not condition:
        raise CommitAttestationError(reason, detail)


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


def source_core(attestation):
    return {key: value for key, value in attestation.items() if key not in {"status", "decision", "attestationId"}}


def verify(protocol, source_attestation, receipt, repo):
    require(protocol.get("id") == "CG14-CONTROLLED-LOCAL-COMMIT-PACKAGING", "WRONG_PROTOCOL")
    require(protocol.get("status") in {"CANDIDATE", "QUALIFIED"}, "UNEXPECTED_PROTOCOL_STATUS")
    require(isinstance(source_attestation, dict), "CG13_ATTESTATION_REQUIRED")
    packaging = protocol["packaging"]
    require(source_attestation.get("protocolId") == packaging["requiredSourceProtocol"], "CG13_PROTOCOL_MISMATCH")
    require(source_attestation.get("status") == packaging["requiredSourceStatus"], "CG13_ATTESTATION_STATUS_INVALID")
    require(source_attestation.get("decision") == packaging["requiredSourceDecision"], "CG13_ATTESTATION_DECISION_INVALID")
    require(canonical_digest(source_core(source_attestation)) == source_attestation.get("attestationId"), "CG13_ATTESTATION_ID_MISMATCH")

    require(receipt.get("schemaVersion") == 1, "COMMIT_RECEIPT_SCHEMA_MISMATCH")
    require(receipt.get("protocolId") == protocol["id"], "COMMIT_RECEIPT_PROTOCOL_MISMATCH")
    require(receipt.get("status") == "LOCAL_COMMIT_CREATED_UNVERIFIED", "COMMIT_RECEIPT_STATUS_INVALID")

    for field in ("caseId", "repository", "baseSha", "workBranch"):
        require(receipt.get(field) == source_attestation.get(field), "COMMIT_SOURCE_BINDING_MISMATCH", {"field": field})
    require(receipt.get("sourceAttestationId") == source_attestation.get("attestationId"), "SOURCE_ATTESTATION_ID_MISMATCH")
    require(receipt.get("sourceAttestationSha256") == canonical_digest(source_attestation), "SOURCE_ATTESTATION_DIGEST_MISMATCH")

    require(receipt.get("commitAuthority") == "SINGLE_LOCAL_COMMIT_ONLY", "COMMIT_AUTHORITY_INVALID")
    require(receipt.get("pushAuthorization") == "NOT_GRANTED", "PUSH_AUTHORIZATION_FORBIDDEN")
    require(receipt.get("pullRequestAuthorization") == "NOT_GRANTED", "PULL_REQUEST_AUTHORIZATION_FORBIDDEN")
    require(receipt.get("mergeAuthorization") == "NOT_GRANTED", "MERGE_AUTHORIZATION_FORBIDDEN")
    require(receipt.get("directMainUpdate") is False, "DIRECT_MAIN_UPDATE_FORBIDDEN")
    require(receipt.get("repositoryAdministration") is False, "REPOSITORY_ADMINISTRATION_FORBIDDEN")
    require(receipt.get("consumerAdoption") is False, "CONSUMER_ADOPTION_FORBIDDEN")

    commit_sha = receipt.get("commitSha")
    require(isinstance(commit_sha, str) and re.fullmatch(r"[0-9a-f]{40}", commit_sha), "COMMIT_SHA_INVALID")
    tree_sha = receipt.get("treeSha")
    require(isinstance(tree_sha, str) and re.fullmatch(r"[0-9a-f]{40}", tree_sha), "TREE_SHA_INVALID")
    require(receipt.get("parentSha") == source_attestation.get("baseSha"), "COMMIT_PARENT_RECEIPT_MISMATCH")
    require(receipt.get("diffSha256") == source_attestation.get("diffSha256"), "COMMIT_DIFF_RECEIPT_MISMATCH")
    require(receipt.get("changedPaths") == sorted_unique_strings(source_attestation.get("changedPaths"), "CG13_CHANGED_PATHS_INVALID"), "COMMIT_PATH_RECEIPT_MISMATCH")
    require(receipt.get("verificationClasses") == sorted_unique_strings(source_attestation.get("verificationClasses"), "CG13_VERIFICATION_CLASSES_INVALID"), "COMMIT_VERIFICATION_CLASS_RECEIPT_MISMATCH")

    repo = Path(repo).resolve()
    head = git(repo, ["rev-parse", "HEAD"]).decode().strip()
    require(head == commit_sha, "HEAD_COMMIT_MISMATCH", {"expected": commit_sha, "actual": head})
    branch = git(repo, ["branch", "--show-current"]).decode().strip()
    require(branch == source_attestation["workBranch"], "WORK_BRANCH_MISMATCH", {"expected": source_attestation["workBranch"], "actual": branch})
    require(branch != "main", "WORK_BRANCH_MUST_NOT_BE_MAIN")

    parents = git(repo, ["show", "-s", "--format=%P", commit_sha]).decode().strip().split()
    require(len(parents) == 1, "COMMIT_MUST_HAVE_SINGLE_PARENT", {"parents": parents})
    require(parents[0] == source_attestation["baseSha"], "COMMIT_PARENT_MISMATCH", {
        "expected": source_attestation["baseSha"], "actual": parents[0],
    })

    actual_tree = git(repo, ["rev-parse", f"{commit_sha}^{{tree}}"]).decode().strip()
    require(actual_tree == tree_sha, "COMMIT_TREE_MISMATCH")
    actual_paths = sorted(path.decode("utf-8") for path in git(repo, ["diff", "--name-only", "-z", source_attestation["baseSha"], commit_sha, "--"]).split(b"\0") if path)
    expected_paths = sorted_unique_strings(source_attestation.get("changedPaths"), "CG13_CHANGED_PATHS_INVALID")
    require(actual_paths == expected_paths, "COMMIT_CHANGED_PATHS_MISMATCH", {"expected": expected_paths, "actual": actual_paths})

    diff_bytes = git(repo, ["diff", "--binary", "--full-index", "--no-ext-diff", source_attestation["baseSha"], commit_sha, "--"])
    actual_diff_sha = hashlib.sha256(diff_bytes).hexdigest()
    require(actual_diff_sha == source_attestation["diffSha256"], "COMMIT_DIFF_DIGEST_MISMATCH", {
        "expected": source_attestation["diffSha256"], "actual": actual_diff_sha,
    })
    require(actual_diff_sha == receipt.get("diffSha256"), "COMMIT_RECEIPT_DIFF_DIGEST_MISMATCH")

    message = git(repo, ["show", "-s", "--format=%B", commit_sha]).decode().rstrip("\n")
    require(message == receipt.get("commitMessage"), "COMMIT_MESSAGE_MISMATCH")
    status = git(repo, ["status", "--porcelain", "--untracked-files=all"]).decode()
    require(status == "", "WORKTREE_NOT_CLEAN_AFTER_COMMIT", {"status": status})

    receipt_sha = canonical_digest(receipt)
    core = {
        "schemaVersion": 1,
        "protocolId": protocol["id"],
        "caseId": source_attestation["caseId"],
        "repository": source_attestation["repository"],
        "baseSha": source_attestation["baseSha"],
        "workBranch": source_attestation["workBranch"],
        "sourcePatchAttestationId": source_attestation["attestationId"],
        "sourcePatchAttestationSha256": canonical_digest(source_attestation),
        "commitReceiptSha256": receipt_sha,
        "commitSha": commit_sha,
        "parentSha": source_attestation["baseSha"],
        "treeSha": actual_tree,
        "changedPaths": actual_paths,
        "diffSha256": actual_diff_sha,
        "verificationClasses": sorted_unique_strings(source_attestation.get("verificationClasses"), "CG13_VERIFICATION_CLASSES_INVALID"),
        "pushAuthorization": "NOT_GRANTED",
        "pullRequestAuthorization": "NOT_GRANTED",
        "mergeAuthorization": "NOT_GRANTED",
        "directMainUpdate": False,
        "repositoryAdministration": False,
        "consumerAdoption": False
    }
    return {
        **core,
        "status": "LOCAL_COMMIT_ATTESTED",
        "decision": "CONTROLLED_LOCAL_COMMIT_VERIFIED",
        "commitAttestationId": canonical_digest(core)
    }


def parse_args():
    parser = argparse.ArgumentParser(description="CG-14 controlled local commit verifier")
    parser.add_argument("--protocol", required=True)
    parser.add_argument("--source-attestation", required=True)
    parser.add_argument("--receipt", required=True)
    parser.add_argument("--repo", required=True)
    return parser.parse_args()


def main():
    args = parse_args()
    try:
        result = verify(load(args.protocol), load(args.source_attestation), load(args.receipt), args.repo)
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    except CommitAttestationError as exc:
        out = {"schemaVersion": 1, "status": "REJECTED", "decision": "LOCAL_COMMIT_NOT_VERIFIED", "reason": exc.reason}
        if exc.detail is not None:
            out["detail"] = exc.detail
        print(json.dumps(out, indent=2, sort_keys=True))
        return 2


if __name__ == "__main__":
    sys.exit(main())
