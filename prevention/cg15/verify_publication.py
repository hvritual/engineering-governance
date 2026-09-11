#!/usr/bin/env python3
import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path


class PublicationAttestationError(Exception):
    def __init__(self, reason, detail=None):
        super().__init__(reason)
        self.reason = reason
        self.detail = detail


def require(condition, reason, detail=None):
    if not condition:
        raise PublicationAttestationError(reason, detail)


def load(path):
    return json.loads(Path(path).read_text())


def canonical_digest(value):
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def source_core(attestation):
    return {key: value for key, value in attestation.items() if key not in {"status", "decision", "commitAttestationId"}}


def git(repo, args, *, check=True):
    cp = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, check=False)
    if check and cp.returncode != 0:
        raise PublicationAttestationError("GIT_READBACK_FAILED", {
            "args": args,
            "returncode": cp.returncode,
            "stderr": cp.stderr.decode("utf-8", "replace")[-4000:],
        })
    return cp


def normalize_remote(url):
    value = url.strip()
    for prefix in ("https://github.com/", "http://github.com/", "ssh://git@github.com/", "git@github.com:"):
        if value.startswith(prefix):
            value = value[len(prefix):]
            break
    if value.endswith(".git"):
        value = value[:-4]
    return value.strip("/")


def sorted_unique_strings(value, reason):
    require(isinstance(value, list), reason)
    require(all(isinstance(item, str) and item for item in value), reason)
    require(len(value) == len(set(value)), reason)
    return sorted(value)


def remote_ref(repo, remote_name, ref):
    cp = git(repo, ["ls-remote", "--refs", remote_name, ref], check=False)
    require(cp.returncode == 0, "REMOTE_READ_FAILED", {"ref": ref, "stderr": cp.stderr.decode("utf-8", "replace")[-4000:]})
    lines = [line for line in cp.stdout.decode("utf-8", "replace").splitlines() if line.strip()]
    require(len(lines) <= 1, "REMOTE_REF_AMBIGUOUS", {"ref": ref, "lines": lines})
    if not lines:
        return None
    parts = lines[0].split("\t")
    require(len(parts) == 2 and parts[1] == ref and re.fullmatch(r"[0-9a-f]{40}", parts[0]), "REMOTE_REF_INVALID", {"ref": ref, "line": lines[0]})
    return parts[0]


def validate_source(protocol, attestation):
    require(protocol.get("id") == "CG15-CONTROLLED-REMOTE-BRANCH-PUBLICATION", "WRONG_PROTOCOL")
    require(protocol.get("status") in {"CANDIDATE", "QUALIFIED"}, "UNEXPECTED_PROTOCOL_STATUS")
    require(isinstance(attestation, dict), "CG14_COMMIT_ATTESTATION_REQUIRED")
    publication = protocol["publication"]
    require(attestation.get("protocolId") == publication["requiredSourceProtocol"], "CG14_PROTOCOL_MISMATCH")
    require(attestation.get("status") == publication["requiredSourceStatus"], "CG14_COMMIT_ATTESTATION_STATUS_INVALID")
    require(attestation.get("decision") == publication["requiredSourceDecision"], "CG14_COMMIT_ATTESTATION_DECISION_INVALID")
    require(canonical_digest(source_core(attestation)) == attestation.get("commitAttestationId"), "CG14_COMMIT_ATTESTATION_ID_MISMATCH")
    require(attestation.get("pushAuthorization") == "NOT_GRANTED", "CG14_SOURCE_AUTHORITY_INVALID", {"field": "pushAuthorization"})
    require(attestation.get("pullRequestAuthorization") == "NOT_GRANTED", "CG14_SOURCE_AUTHORITY_INVALID", {"field": "pullRequestAuthorization"})
    require(attestation.get("mergeAuthorization") == "NOT_GRANTED", "CG14_SOURCE_AUTHORITY_INVALID", {"field": "mergeAuthorization"})
    require(attestation.get("directMainUpdate") is False, "CG14_SOURCE_AUTHORITY_INVALID", {"field": "directMainUpdate"})
    require(attestation.get("repositoryAdministration") is False, "CG14_SOURCE_AUTHORITY_INVALID", {"field": "repositoryAdministration"})
    require(attestation.get("consumerAdoption") is False, "CG14_SOURCE_AUTHORITY_INVALID", {"field": "consumerAdoption"})


def verify(protocol, source_attestation, receipt, repo):
    validate_source(protocol, source_attestation)
    publication = protocol["publication"]
    require(receipt.get("schemaVersion") == 1, "PUBLICATION_RECEIPT_SCHEMA_MISMATCH")
    require(receipt.get("protocolId") == protocol["id"], "PUBLICATION_RECEIPT_PROTOCOL_MISMATCH")
    require(receipt.get("status") == "REMOTE_BRANCH_PUBLISHED_UNVERIFIED", "PUBLICATION_RECEIPT_STATUS_INVALID")

    for field in ("caseId", "repository", "baseSha", "workBranch", "commitSha", "treeSha", "diffSha256"):
        require(receipt.get(field) == source_attestation.get(field), "PUBLICATION_SOURCE_BINDING_MISMATCH", {"field": field})
    require(receipt.get("sourceCommitAttestationId") == source_attestation.get("commitAttestationId"), "SOURCE_COMMIT_ATTESTATION_ID_MISMATCH")
    require(receipt.get("sourceCommitAttestationSha256") == canonical_digest(source_attestation), "SOURCE_COMMIT_ATTESTATION_DIGEST_MISMATCH")
    require(receipt.get("remoteName") == publication["remoteName"], "REMOTE_NAME_MISMATCH")
    require(receipt.get("baseBranch") == publication["baseBranch"], "BASE_BRANCH_MISMATCH")

    changed_paths = sorted_unique_strings(source_attestation.get("changedPaths"), "CG14_CHANGED_PATHS_INVALID")
    verification_classes = sorted_unique_strings(source_attestation.get("verificationClasses"), "CG14_VERIFICATION_CLASSES_INVALID")
    require(receipt.get("changedPaths") == changed_paths, "PUBLICATION_PATH_BINDING_MISMATCH")
    require(receipt.get("verificationClasses") == verification_classes, "PUBLICATION_VERIFICATION_CLASS_BINDING_MISMATCH")

    work_branch = source_attestation["workBranch"]
    base_ref = f"refs/heads/{publication['baseBranch']}"
    target_ref = f"refs/heads/{work_branch}"
    require(receipt.get("baseRefspec") == f"{source_attestation['baseSha']}:{base_ref}", "BASE_REFSPEC_MISMATCH")
    require(receipt.get("targetRefspec") == f"{source_attestation['commitSha']}:{target_ref}", "TARGET_REFSPEC_MISMATCH")
    require(receipt.get("remoteBaseBefore") == source_attestation["baseSha"], "REMOTE_BASE_BEFORE_MISMATCH")
    require(receipt.get("remoteBaseAfter") == source_attestation["baseSha"], "REMOTE_BASE_AFTER_MISMATCH")
    require(receipt.get("remoteTargetBefore") == "ABSENT", "REMOTE_TARGET_PRESTATE_INVALID")
    require(receipt.get("remoteTargetAfter") == source_attestation["commitSha"], "REMOTE_TARGET_RECEIPT_MISMATCH")
    require(receipt.get("atomicPush") is True, "ATOMIC_PUBLICATION_REQUIRED")
    require(receipt.get("createOnlyLease") == target_ref, "CREATE_ONLY_LEASE_MISMATCH")
    require(receipt.get("unconditionalForcePush") is False, "UNCONDITIONAL_FORCE_PUSH_FORBIDDEN")
    require(receipt.get("existingBranchUpdate") is False, "EXISTING_BRANCH_UPDATE_FORBIDDEN")
    require(receipt.get("remoteRefDeletion") is False, "REMOTE_REF_DELETION_FORBIDDEN")
    require(receipt.get("publicationAuthority") == "SINGLE_REMOTE_BRANCH_CREATE_ONLY", "PUBLICATION_AUTHORITY_INVALID")
    require(receipt.get("pullRequestAuthorization") == "NOT_GRANTED", "PULL_REQUEST_AUTHORIZATION_FORBIDDEN")
    require(receipt.get("mergeAuthorization") == "NOT_GRANTED", "MERGE_AUTHORIZATION_FORBIDDEN")
    require(receipt.get("directMainUpdate") is False, "DIRECT_MAIN_UPDATE_FORBIDDEN")
    require(receipt.get("repositoryAdministration") is False, "REPOSITORY_ADMINISTRATION_FORBIDDEN")
    require(receipt.get("consumerAdoption") is False, "CONSUMER_ADOPTION_FORBIDDEN")

    repo = Path(repo).resolve()
    remote_url = git(repo, ["remote", "get-url", publication["remoteName"]], check=False)
    require(remote_url.returncode == 0, "ORIGIN_REMOTE_REQUIRED")
    actual_repository = normalize_remote(remote_url.stdout.decode())
    require(actual_repository == source_attestation["repository"], "REPOSITORY_IDENTITY_MISMATCH", {
        "expected": source_attestation["repository"], "actual": actual_repository,
    })
    branch = git(repo, ["branch", "--show-current"]).stdout.decode().strip()
    require(branch == work_branch, "WORK_BRANCH_MOVED_AFTER_PUBLICATION", {"expected": work_branch, "actual": branch})
    require(branch != publication["baseBranch"], "WORK_BRANCH_MUST_NOT_BE_BASE_BRANCH")
    head = git(repo, ["rev-parse", "HEAD"]).stdout.decode().strip()
    require(head == source_attestation["commitSha"], "LOCAL_HEAD_MOVED_AFTER_PUBLICATION", {"expected": source_attestation["commitSha"], "actual": head})
    status = git(repo, ["status", "--porcelain", "--untracked-files=all"]).stdout.decode()
    require(status == "", "LOCAL_WORKTREE_NOT_CLEAN_AFTER_PUBLICATION", {"status": status})

    remote_base = remote_ref(repo, publication["remoteName"], base_ref)
    require(remote_base == source_attestation["baseSha"], "REMOTE_BASE_SHA_MISMATCH", {"expected": source_attestation["baseSha"], "actual": remote_base})
    remote_target = remote_ref(repo, publication["remoteName"], target_ref)
    require(remote_target == source_attestation["commitSha"], "REMOTE_TARGET_SHA_MISMATCH", {"expected": source_attestation["commitSha"], "actual": remote_target})

    tree_sha = git(repo, ["rev-parse", f"{head}^{{tree}}"]).stdout.decode().strip()
    require(tree_sha == source_attestation["treeSha"], "LOCAL_COMMIT_TREE_MISMATCH")
    actual_paths = sorted(path.decode("utf-8") for path in git(repo, ["diff", "--name-only", "-z", source_attestation["baseSha"], head, "--"]).stdout.split(b"\0") if path)
    require(actual_paths == changed_paths, "LOCAL_COMMIT_PATHS_MISMATCH")
    diff_bytes = git(repo, ["diff", "--binary", "--full-index", "--no-ext-diff", source_attestation["baseSha"], head, "--"]).stdout
    actual_diff_sha = hashlib.sha256(diff_bytes).hexdigest()
    require(actual_diff_sha == source_attestation["diffSha256"], "LOCAL_COMMIT_DIFF_DIGEST_MISMATCH")

    receipt_sha = canonical_digest(receipt)
    core = {
        "schemaVersion": 1,
        "protocolId": protocol["id"],
        "caseId": source_attestation["caseId"],
        "repository": source_attestation["repository"],
        "remoteName": publication["remoteName"],
        "baseBranch": publication["baseBranch"],
        "baseSha": source_attestation["baseSha"],
        "workBranch": work_branch,
        "remoteBranchSha": remote_target,
        "commitSha": source_attestation["commitSha"],
        "treeSha": tree_sha,
        "changedPaths": actual_paths,
        "diffSha256": actual_diff_sha,
        "verificationClasses": verification_classes,
        "sourceLocalCommitAttestationId": source_attestation["commitAttestationId"],
        "sourceLocalCommitAttestationSha256": canonical_digest(source_attestation),
        "publicationReceiptSha256": receipt_sha,
        "publicationAuthority": "SINGLE_REMOTE_BRANCH_CREATE_ONLY",
        "pullRequestAuthorization": "NOT_GRANTED",
        "mergeAuthorization": "NOT_GRANTED",
        "directMainUpdate": False,
        "repositoryAdministration": False,
        "consumerAdoption": False,
    }
    return {
        **core,
        "status": "REMOTE_BRANCH_PUBLICATION_ATTESTED",
        "decision": "CONTROLLED_REMOTE_BRANCH_PUBLICATION_VERIFIED",
        "publicationAttestationId": canonical_digest(core),
    }


def parse_args():
    parser = argparse.ArgumentParser(description="CG-15 remote branch publication verifier")
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
    except PublicationAttestationError as exc:
        out = {"schemaVersion": 1, "status": "REJECTED", "decision": "REMOTE_BRANCH_PUBLICATION_NOT_VERIFIED", "reason": exc.reason}
        if exc.detail is not None:
            out["detail"] = exc.detail
        print(json.dumps(out, indent=2, sort_keys=True))
        return 2


if __name__ == "__main__":
    sys.exit(main())
