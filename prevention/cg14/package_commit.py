#!/usr/bin/env python3
import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path


class CommitPackagingError(Exception):
    def __init__(self, reason, detail=None):
        super().__init__(reason)
        self.reason = reason
        self.detail = detail


def require(condition, reason, detail=None):
    if not condition:
        raise CommitPackagingError(reason, detail)


def load(path):
    return json.loads(Path(path).read_text())


def canonical_digest(value):
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def git(repo, args, *, check=True):
    cp = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, check=False)
    if check and cp.returncode != 0:
        raise CommitPackagingError("GIT_COMMAND_FAILED", {
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


def validate_source_attestation(protocol, attestation):
    require(protocol.get("schemaVersion") == 1, "PROTOCOL_SCHEMA_MISMATCH")
    require(protocol.get("id") == "CG14-CONTROLLED-LOCAL-COMMIT-PACKAGING", "WRONG_PROTOCOL")
    require(protocol.get("status") in {"CANDIDATE", "QUALIFIED"}, "UNEXPECTED_PROTOCOL_STATUS")
    require(isinstance(attestation, dict), "CG13_ATTESTATION_REQUIRED")
    packaging = protocol["packaging"]
    require(attestation.get("protocolId") == packaging["requiredSourceProtocol"], "CG13_PROTOCOL_MISMATCH")
    require(attestation.get("status") == packaging["requiredSourceStatus"], "CG13_ATTESTATION_STATUS_INVALID")
    require(attestation.get("decision") == packaging["requiredSourceDecision"], "CG13_ATTESTATION_DECISION_INVALID")

    for field in ("caseId", "repository", "baseSha", "workBranch", "attestationId", "diffSha256"):
        require(isinstance(attestation.get(field), str) and attestation[field], "CG13_ATTESTATION_FIELD_REQUIRED", {"field": field})
    require(re.fullmatch(r"[0-9a-f]{40}", attestation["baseSha"]) is not None, "CG13_BASE_SHA_INVALID")
    require(re.fullmatch(r"[0-9a-f]{64}", attestation["diffSha256"]) is not None, "CG13_DIFF_DIGEST_INVALID")
    require(re.fullmatch(r"[0-9a-f]{64}", attestation["attestationId"]) is not None, "CG13_ATTESTATION_ID_INVALID")

    core = {key: value for key, value in attestation.items() if key not in {"status", "decision", "attestationId"}}
    require(canonical_digest(core) == attestation["attestationId"], "CG13_ATTESTATION_ID_MISMATCH")

    require(attestation.get("commitAuthorization") == "NOT_GRANTED", "CG13_SOURCE_AUTHORITY_INVALID", {"field": "commitAuthorization"})
    require(attestation.get("pullRequestAuthorization") == "NOT_GRANTED", "CG13_SOURCE_AUTHORITY_INVALID", {"field": "pullRequestAuthorization"})
    require(attestation.get("mergeAuthorization") == "NOT_GRANTED", "CG13_SOURCE_AUTHORITY_INVALID", {"field": "mergeAuthorization"})
    require(attestation.get("directMainUpdate") is False, "CG13_SOURCE_AUTHORITY_INVALID", {"field": "directMainUpdate"})
    require(attestation.get("repositoryAdministration") is False, "CG13_SOURCE_AUTHORITY_INVALID", {"field": "repositoryAdministration"})
    require(attestation.get("consumerAdoption") is False, "CG13_SOURCE_AUTHORITY_INVALID", {"field": "consumerAdoption"})
    require(attestation["workBranch"] != "main", "WORK_BRANCH_MUST_NOT_BE_MAIN")

    changed_paths = sorted_unique_strings(attestation.get("changedPaths"), "CG13_CHANGED_PATHS_INVALID")
    require(bool(changed_paths), "CG13_CHANGED_PATHS_EMPTY")
    verification_classes = sorted_unique_strings(attestation.get("verificationClasses"), "CG13_VERIFICATION_CLASSES_INVALID")
    require(bool(verification_classes), "CG13_VERIFICATION_CLASSES_EMPTY")
    return changed_paths, verification_classes


def validate_message(message):
    require(isinstance(message, str), "COMMIT_MESSAGE_INVALID")
    value = message.strip()
    require(bool(value) and value == message, "COMMIT_MESSAGE_INVALID")
    require("\n" not in value and "\r" not in value, "COMMIT_MESSAGE_INVALID")
    require(len(value.encode("utf-8")) <= 200, "COMMIT_MESSAGE_TOO_LONG")
    return value


def package_commit(protocol, attestation, repo, commit_message):
    changed_paths, verification_classes = validate_source_attestation(protocol, attestation)
    message = validate_message(commit_message)
    repo = Path(repo).resolve()

    inside = git(repo, ["rev-parse", "--is-inside-work-tree"], check=False)
    require(inside.returncode == 0 and inside.stdout.decode().strip() == "true", "REPOSITORY_NOT_GIT_WORKTREE")

    remote = git(repo, ["remote", "get-url", "origin"], check=False)
    require(remote.returncode == 0, "ORIGIN_REMOTE_REQUIRED")
    actual_repository = normalize_remote(remote.stdout.decode())
    require(actual_repository == attestation["repository"], "REPOSITORY_IDENTITY_MISMATCH", {
        "expected": attestation["repository"], "actual": actual_repository,
    })

    head_before = git(repo, ["rev-parse", "HEAD"]).stdout.decode().strip()
    require(head_before == attestation["baseSha"], "BASE_MOVED_BEFORE_COMMIT", {
        "expected": attestation["baseSha"], "actual": head_before,
    })
    branch = git(repo, ["branch", "--show-current"]).stdout.decode().strip()
    require(branch == attestation["workBranch"], "WORK_BRANCH_MISMATCH", {
        "expected": attestation["workBranch"], "actual": branch,
    })
    require(branch != "main", "WORK_BRANCH_MUST_NOT_BE_MAIN")

    staged_at_start = git(repo, ["diff", "--cached", "--name-only", "-z", "--"]).stdout
    require(staged_at_start == b"", "STAGED_CONTENT_PRESENT_AT_START")
    status = git(repo, ["status", "--porcelain", "--untracked-files=all"]).stdout.decode()
    require(status != "", "ATTESTED_WORKTREE_DIFF_REQUIRED")
    require(not any(line.startswith("?? ") for line in status.splitlines()), "UNTRACKED_CONTENT_FORBIDDEN")

    actual_paths = sorted(path.decode("utf-8") for path in git(repo, ["diff", "--name-only", "-z", "--"]).stdout.split(b"\0") if path)
    require(actual_paths == changed_paths, "ATTESTED_CHANGED_PATHS_MISMATCH", {
        "expected": changed_paths, "actual": actual_paths,
    })
    diff_bytes = git(repo, ["diff", "--binary", "--full-index", "--no-ext-diff", "--"]).stdout
    actual_diff_sha = hashlib.sha256(diff_bytes).hexdigest()
    require(actual_diff_sha == attestation["diffSha256"], "ATTESTED_DIFF_DIGEST_MISMATCH", {
        "expected": attestation["diffSha256"], "actual": actual_diff_sha,
    })

    git(repo, ["add", "--", *changed_paths])
    staged_paths = sorted(path.decode("utf-8") for path in git(repo, ["diff", "--cached", "--name-only", "-z", "--"]).stdout.split(b"\0") if path)
    require(staged_paths == changed_paths, "STAGED_PATH_SET_MISMATCH", {
        "expected": changed_paths, "actual": staged_paths,
    })
    unstaged = git(repo, ["diff", "--name-only", "-z", "--"]).stdout
    require(unstaged == b"", "UNSTAGED_CONTENT_AFTER_CONTROLLED_STAGING")
    staged_diff = git(repo, ["diff", "--cached", "--binary", "--full-index", "--no-ext-diff", "--"]).stdout
    staged_diff_sha = hashlib.sha256(staged_diff).hexdigest()
    require(staged_diff_sha == attestation["diffSha256"], "STAGED_DIFF_DIGEST_MISMATCH")

    committed = git(repo, ["commit", "-m", message], check=False)
    require(committed.returncode == 0, "LOCAL_COMMIT_FAILED", {"stderr": committed.stderr.decode("utf-8", "replace")[-4000:]})

    commit_sha = git(repo, ["rev-parse", "HEAD"]).stdout.decode().strip()
    require(commit_sha != attestation["baseSha"], "LOCAL_COMMIT_NOT_CREATED")
    branch_after = git(repo, ["branch", "--show-current"]).stdout.decode().strip()
    require(branch_after == attestation["workBranch"], "WORK_BRANCH_MOVED_AFTER_COMMIT")

    parents = git(repo, ["show", "-s", "--format=%P", commit_sha]).stdout.decode().strip().split()
    require(len(parents) == 1, "COMMIT_MUST_HAVE_SINGLE_PARENT", {"parents": parents})
    require(parents[0] == attestation["baseSha"], "COMMIT_PARENT_MISMATCH", {
        "expected": attestation["baseSha"], "actual": parents[0],
    })
    committed_paths = sorted(path.decode("utf-8") for path in git(repo, ["diff", "--name-only", "-z", attestation["baseSha"], commit_sha, "--"]).stdout.split(b"\0") if path)
    require(committed_paths == changed_paths, "COMMIT_CHANGED_PATHS_MISMATCH")
    committed_diff = git(repo, ["diff", "--binary", "--full-index", "--no-ext-diff", attestation["baseSha"], commit_sha, "--"]).stdout
    committed_diff_sha = hashlib.sha256(committed_diff).hexdigest()
    require(committed_diff_sha == attestation["diffSha256"], "COMMIT_DIFF_DIGEST_MISMATCH", {
        "expected": attestation["diffSha256"], "actual": committed_diff_sha,
    })

    status_after = git(repo, ["status", "--porcelain", "--untracked-files=all"]).stdout.decode()
    require(status_after == "", "WORKTREE_NOT_CLEAN_AFTER_COMMIT", {"status": status_after})
    tree_sha = git(repo, ["rev-parse", f"{commit_sha}^{{tree}}"]).stdout.decode().strip()
    actual_message = git(repo, ["show", "-s", "--format=%B", commit_sha]).stdout.decode().rstrip("\n")
    require(actual_message == message, "COMMIT_MESSAGE_MISMATCH")

    return {
        "schemaVersion": 1,
        "protocolId": protocol["id"],
        "status": "LOCAL_COMMIT_CREATED_UNVERIFIED",
        "caseId": attestation["caseId"],
        "repository": attestation["repository"],
        "baseSha": attestation["baseSha"],
        "workBranch": attestation["workBranch"],
        "sourceAttestationId": attestation["attestationId"],
        "sourceAttestationSha256": canonical_digest(attestation),
        "commitSha": commit_sha,
        "parentSha": attestation["baseSha"],
        "treeSha": tree_sha,
        "changedPaths": changed_paths,
        "diffSha256": committed_diff_sha,
        "verificationClasses": verification_classes,
        "commitMessage": message,
        "commitAuthority": "SINGLE_LOCAL_COMMIT_ONLY",
        "pushAuthorization": "NOT_GRANTED",
        "pullRequestAuthorization": "NOT_GRANTED",
        "mergeAuthorization": "NOT_GRANTED",
        "directMainUpdate": False,
        "repositoryAdministration": False,
        "consumerAdoption": False,
    }


def parse_args():
    parser = argparse.ArgumentParser(description="CG-14 controlled local commit packager")
    parser.add_argument("--protocol", required=True)
    parser.add_argument("--attestation", required=True)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--message", required=True)
    return parser.parse_args()


def main():
    args = parse_args()
    try:
        result = package_commit(load(args.protocol), load(args.attestation), args.repo, args.message)
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    except CommitPackagingError as exc:
        out = {"schemaVersion": 1, "status": "REJECTED", "decision": "LOCAL_COMMIT_NOT_PACKAGED", "reason": exc.reason}
        if exc.detail is not None:
            out["detail"] = exc.detail
        print(json.dumps(out, indent=2, sort_keys=True))
        return 2


if __name__ == "__main__":
    sys.exit(main())
