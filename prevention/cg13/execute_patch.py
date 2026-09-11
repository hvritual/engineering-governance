#!/usr/bin/env python3
import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path, PurePosixPath


class ExecutionError(Exception):
    def __init__(self, reason, detail=None):
        super().__init__(reason)
        self.reason = reason
        self.detail = detail


def require(condition, reason, detail=None):
    if not condition:
        raise ExecutionError(reason, detail)


def load(path):
    return json.loads(Path(path).read_text())


def canonical_digest(value):
    data = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def git(repo, args, *, check=True):
    cp = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, check=False)
    if check and cp.returncode != 0:
        raise ExecutionError("GIT_COMMAND_FAILED", {
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


def validate_repo_path(path, forbidden_prefixes):
    require(isinstance(path, str) and path, "PATCH_PATH_INVALID", {"path": path})
    require("\\" not in path, "PATCH_PATH_INVALID", {"path": path})
    posix = PurePosixPath(path)
    require(not posix.is_absolute(), "PATCH_PATH_ABSOLUTE", {"path": path})
    require(".." not in posix.parts and "." not in posix.parts, "PATCH_PATH_TRAVERSAL", {"path": path})
    require(path != ".git" and not path.startswith(".git/"), "PATCH_PATH_GIT_METADATA", {"path": path})
    for prefix in forbidden_prefixes:
        if path == prefix.rstrip("/") or path.startswith(prefix):
            raise ExecutionError("FORBIDDEN_PATH", {"path": path, "prefix": prefix})


def sorted_unique_strings(value, reason):
    require(isinstance(value, list), reason)
    require(all(isinstance(item, str) and item for item in value), reason)
    require(len(value) == len(set(value)), reason)
    return sorted(value)


def validate_receipt(protocol, receipt):
    require(protocol.get("schemaVersion") == 1, "PROTOCOL_SCHEMA_MISMATCH")
    require(protocol.get("id") == "CG13-CONTROLLED-PATCH-EXECUTION", "WRONG_PROTOCOL")
    require(protocol.get("status") in {"CANDIDATE", "QUALIFIED"}, "UNEXPECTED_PROTOCOL_STATUS")

    disposition = receipt.get("disposition")
    required_disposition = protocol["execution"]["requiredDisposition"]
    if disposition != required_disposition:
        require(disposition in protocol.get("nonExecutableDispositions", []), "UNKNOWN_RECONCILIATION_DISPOSITION", {"disposition": disposition})
        raise ExecutionError("CG12_DISPOSITION_NOT_EXECUTABLE", {
            "caseId": receipt.get("caseId"),
            "disposition": disposition,
        })

    require(receipt.get("patchRequired") is True, "PATCH_REQUIRED_TRUE_REQUIRED")
    require(receipt.get("authorizationStatus") == "AUTHORIZED", "AUTHORIZED_RECEIPT_REQUIRED")
    require(receipt.get("directMainUpdate") is False, "DIRECT_MAIN_UPDATE_FORBIDDEN")
    require(receipt.get("mergeAuthorization") is False, "MERGE_AUTHORIZATION_FORBIDDEN")
    require(receipt.get("repositoryAdministration") is False, "REPOSITORY_ADMINISTRATION_FORBIDDEN")
    require(receipt.get("consumerAdoption") is False, "CONSUMER_ADOPTION_FORBIDDEN")

    for key in ("caseId", "repository", "baseSha", "workBranch"):
        require(isinstance(receipt.get(key), str) and receipt[key], "RECEIPT_FIELD_REQUIRED", {"field": key})
    require(re.fullmatch(r"[0-9a-f]{40}", receipt["baseSha"]) is not None, "BASE_SHA_INVALID")
    require(receipt["workBranch"] != "main", "WORK_BRANCH_MUST_NOT_BE_MAIN")

    allowed = sorted_unique_strings(receipt.get("allowedPaths"), "ALLOWED_PATHS_INVALID")
    require(bool(allowed), "ALLOWED_PATHS_EMPTY")
    for path in allowed:
        validate_repo_path(path, protocol.get("forbiddenPathPrefixes", []))

    required_verification = sorted_unique_strings(receipt.get("preservedVerificationClasses"), "VERIFICATION_CLASSES_INVALID")
    require(bool(required_verification), "VERIFICATION_CLASSES_EMPTY")
    return allowed, required_verification


def patch_paths(repo, patch_path, protocol):
    patch = Path(patch_path).resolve()
    require(patch.is_file() and patch.stat().st_size > 0, "PATCH_FILE_EMPTY_OR_MISSING")

    summary = git(repo, ["apply", "--summary", str(patch)], check=False)
    require(summary.returncode == 0, "PATCH_PARSE_FAILED", {"stderr": summary.stderr.decode("utf-8", "replace")[-4000:]})
    summary_text = summary.stdout.decode("utf-8", "replace")
    for marker, reason in (
        (" rename ", "PATCH_RENAME_FORBIDDEN"),
        (" copy ", "PATCH_COPY_FORBIDDEN"),
        (" create mode ", "PATCH_CREATE_FILE_FORBIDDEN"),
        ("new file mode", "PATCH_CREATE_FILE_FORBIDDEN"),
        ("120000", "PATCH_SYMLINK_FORBIDDEN"),
        ("160000", "PATCH_GITLINK_FORBIDDEN"),
    ):
        if marker in f" {summary_text} ":
            raise ExecutionError(reason, {"summary": summary_text[-4000:]})

    cp = git(repo, ["apply", "--numstat", "-z", str(patch)], check=False)
    require(cp.returncode == 0, "PATCH_PARSE_FAILED", {"stderr": cp.stderr.decode("utf-8", "replace")[-4000:]})
    fields = [field for field in cp.stdout.split(b"\0") if field]
    paths = []
    for field in fields:
        parts = field.split(b"\t", 2)
        require(len(parts) == 3, "PATCH_NUMSTAT_INVALID")
        path = parts[2].decode("utf-8", "strict")
        validate_repo_path(path, protocol.get("forbiddenPathPrefixes", []))
        paths.append(path)
    require(paths, "PATCH_HAS_NO_CHANGED_PATHS")
    require(len(paths) == len(set(paths)), "PATCH_DUPLICATE_PATH")
    return sorted(paths)


def execute(protocol, receipt, repo, patch_path):
    allowed, required_verification = validate_receipt(protocol, receipt)
    repo = Path(repo).resolve()

    inside = git(repo, ["rev-parse", "--is-inside-work-tree"], check=False)
    require(inside.returncode == 0 and inside.stdout.decode().strip() == "true", "REPOSITORY_NOT_GIT_WORKTREE")

    head = git(repo, ["rev-parse", "HEAD"]).stdout.decode().strip()
    require(head == receipt["baseSha"], "LIVE_BASE_SHA_MISMATCH", {"expected": receipt["baseSha"], "actual": head})
    branch = git(repo, ["branch", "--show-current"]).stdout.decode().strip()
    require(branch == receipt["workBranch"], "WORK_BRANCH_MISMATCH", {"expected": receipt["workBranch"], "actual": branch})
    require(branch != "main", "WORK_BRANCH_MUST_NOT_BE_MAIN")

    status = git(repo, ["status", "--porcelain", "--untracked-files=all"]).stdout.decode()
    require(status == "", "WORKTREE_NOT_CLEAN_AT_START", {"status": status})

    remote = git(repo, ["remote", "get-url", "origin"], check=False)
    require(remote.returncode == 0, "ORIGIN_REMOTE_REQUIRED")
    actual_repository = normalize_remote(remote.stdout.decode())
    require(actual_repository == receipt["repository"], "REPOSITORY_IDENTITY_MISMATCH", {
        "expected": receipt["repository"], "actual": actual_repository,
    })

    preflight_paths = patch_paths(repo, patch_path, protocol)
    require(set(preflight_paths).issubset(set(allowed)), "PATCH_PATH_OUTSIDE_ALLOWLIST", {
        "allowed": allowed, "actual": preflight_paths,
    })
    for path in preflight_paths:
        require((repo / path).exists() or (repo / path).is_symlink(), "AUTHORIZED_PATH_MISSING_AT_START", {"path": path})
        require(not (repo / path).is_symlink(), "PATCH_SYMLINK_FORBIDDEN", {"path": path})

    patch = str(Path(patch_path).resolve())
    check = git(repo, ["apply", "--check", patch], check=False)
    require(check.returncode == 0, "PATCH_APPLY_CHECK_FAILED", {"stderr": check.stderr.decode("utf-8", "replace")[-4000:]})
    applied = git(repo, ["apply", patch], check=False)
    require(applied.returncode == 0, "PATCH_APPLY_FAILED", {"stderr": applied.stderr.decode("utf-8", "replace")[-4000:]})

    staged = git(repo, ["diff", "--cached", "--name-only", "-z", "--"]).stdout
    require(staged == b"", "PATCH_MUST_REMAIN_UNCOMMITTED_AND_UNSTAGED")
    actual_raw = git(repo, ["diff", "--name-only", "-z", "--"]).stdout
    actual_paths = sorted(path.decode("utf-8") for path in actual_raw.split(b"\0") if path)
    for path in actual_paths:
        validate_repo_path(path, protocol.get("forbiddenPathPrefixes", []))
    require(actual_paths == preflight_paths, "POST_APPLY_PATH_SET_MISMATCH", {
        "preflight": preflight_paths, "actual": actual_paths,
    })
    require(set(actual_paths).issubset(set(allowed)), "POST_APPLY_PATH_OUTSIDE_ALLOWLIST")

    status_after = git(repo, ["status", "--porcelain", "--untracked-files=all"]).stdout.decode()
    require(status_after != "", "POST_APPLY_DIFF_EMPTY")
    require(not any(line.startswith("?? ") for line in status_after.splitlines()), "POST_APPLY_UNTRACKED_FILE_FORBIDDEN")

    diff_bytes = git(repo, ["diff", "--binary", "--full-index", "--no-ext-diff", "--"]).stdout
    require(bool(diff_bytes), "POST_APPLY_DIFF_EMPTY")
    diff_sha = hashlib.sha256(diff_bytes).hexdigest()
    patch_sha = hashlib.sha256(Path(patch_path).read_bytes()).hexdigest()
    receipt_sha = canonical_digest(receipt)

    return {
        "schemaVersion": 1,
        "protocolId": protocol["id"],
        "status": "PATCH_APPLIED_UNVERIFIED",
        "caseId": receipt["caseId"],
        "repository": receipt["repository"],
        "baseSha": receipt["baseSha"],
        "workBranch": receipt["workBranch"],
        "sourceReconciliationSha256": receipt_sha,
        "changedPaths": actual_paths,
        "allowedPaths": allowed,
        "requiredVerificationClasses": required_verification,
        "patchSha256": patch_sha,
        "diffSha256": diff_sha,
        "commitAuthorization": "NOT_GRANTED",
        "pullRequestAuthorization": "NOT_GRANTED",
        "mergeAuthorization": "NOT_GRANTED",
        "directMainUpdate": False,
        "repositoryAdministration": False,
        "consumerAdoption": False,
    }


def parse_args():
    parser = argparse.ArgumentParser(description="CG-13 controlled patch executor")
    parser.add_argument("--protocol", required=True)
    parser.add_argument("--receipt", required=True)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--patch", required=True)
    return parser.parse_args()


def main():
    args = parse_args()
    try:
        result = execute(load(args.protocol), load(args.receipt), args.repo, args.patch)
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    except ExecutionError as exc:
        out = {"schemaVersion": 1, "status": "REJECTED", "decision": "CONTROLLED_PATCH_NOT_EXECUTED", "reason": exc.reason}
        if exc.detail is not None:
            out["detail"] = exc.detail
        print(json.dumps(out, indent=2, sort_keys=True))
        return 2


if __name__ == "__main__":
    sys.exit(main())
