#!/usr/bin/env python3
import hashlib
import os
import re
import subprocess
import tempfile
from pathlib import Path


class DiffIdentityError(Exception):
    def __init__(self, reason, detail=None):
        super().__init__(reason)
        self.reason = reason
        self.detail = detail


def require(condition, reason, detail=None):
    if not condition:
        raise DiffIdentityError(reason, detail)


def sha256_bytes(value):
    return hashlib.sha256(value).hexdigest()


def git(repo, args, *, env=None, input_bytes=None, check=True):
    merged_env = os.environ.copy()
    if env:
        merged_env.update(env)
    cp = subprocess.run(
        ["git", "-C", str(repo), *args],
        input=input_bytes,
        capture_output=True,
        check=False,
        env=merged_env,
    )
    if check and cp.returncode != 0:
        raise DiffIdentityError("GIT_COMMAND_FAILED", {
            "args": args,
            "returncode": cp.returncode,
            "stderr": cp.stderr.decode("utf-8", "replace")[-4000:],
        })
    return cp


def validate_git_sha(value, reason):
    require(isinstance(value, str) and re.fullmatch(r"[0-9a-f]{40}", value), reason)
    return value


def local_git_identity(repo, base_sha, head_sha):
    repo = Path(repo).resolve()
    base_sha = validate_git_sha(base_sha, "BASE_SHA_INVALID")
    head_sha = validate_git_sha(head_sha, "HEAD_SHA_INVALID")
    require(git(repo, ["cat-file", "-e", f"{base_sha}^{{commit}}"], check=False).returncode == 0, "BASE_COMMIT_MISSING")
    require(git(repo, ["cat-file", "-e", f"{head_sha}^{{commit}}"], check=False).returncode == 0, "HEAD_COMMIT_MISSING")
    paths_raw = git(repo, ["diff", "--name-only", "-z", base_sha, head_sha, "--"]).stdout
    paths = sorted(part.decode("utf-8") for part in paths_raw.split(b"\0") if part)
    require(paths and len(paths) == len(set(paths)), "LOCAL_CHANGED_PATHS_INVALID")
    diff_bytes = git(repo, ["diff", "--binary", "--full-index", "--no-ext-diff", base_sha, head_sha, "--"]).stdout
    require(diff_bytes, "LOCAL_DIFF_EMPTY")
    tree_sha = git(repo, ["rev-parse", f"{head_sha}^{{tree}}"]).stdout.decode().strip()
    validate_git_sha(tree_sha, "HEAD_TREE_SHA_INVALID")
    return {
        "changedPaths": paths,
        "localDiffSha256": sha256_bytes(diff_bytes),
        "headTreeSha": tree_sha,
        "localDiffBytes": len(diff_bytes),
    }


def validate_provider_diff(provider_diff):
    require(isinstance(provider_diff, (bytes, bytearray)), "PROVIDER_DIFF_BYTES_REQUIRED")
    provider_diff = bytes(provider_diff)
    require(provider_diff, "PROVIDER_DIFF_EMPTY")
    require(len(provider_diff) <= 10 * 1024 * 1024, "PROVIDER_DIFF_TOO_LARGE", {"bytes": len(provider_diff)})
    require(b"\x00" not in provider_diff, "PROVIDER_DIFF_BINARY_UNSUPPORTED")
    text = provider_diff.decode("utf-8", "strict")
    require("GIT binary patch" not in text and "Binary files " not in text, "PROVIDER_DIFF_BINARY_UNSUPPORTED")
    require(text.startswith("diff --git "), "PROVIDER_DIFF_FORMAT_INVALID")
    return provider_diff


def apply_provider_diff_to_base(repo, base_sha, provider_diff):
    repo = Path(repo).resolve()
    base_sha = validate_git_sha(base_sha, "BASE_SHA_INVALID")
    provider_diff = validate_provider_diff(provider_diff)
    with tempfile.TemporaryDirectory(prefix="cg17-index-") as root:
        index_path = str(Path(root) / "index")
        env = {"GIT_INDEX_FILE": index_path}
        git(repo, ["read-tree", base_sha], env=env)
        applied = git(
            repo,
            ["apply", "--cached", "--binary", "--whitespace=nowarn", "--recount", "-"],
            env=env,
            input_bytes=provider_diff,
            check=False,
        )
        require(applied.returncode == 0, "PROVIDER_DIFF_APPLY_FAILED", {
            "stderr": applied.stderr.decode("utf-8", "replace")[-4000:]
        })
        tree_sha = git(repo, ["write-tree"], env=env).stdout.decode().strip()
        validate_git_sha(tree_sha, "PROVIDER_APPLIED_TREE_SHA_INVALID")
    return {
        "providerDiffSha256": sha256_bytes(provider_diff),
        "providerDiffBytes": len(provider_diff),
        "providerAppliedTreeSha": tree_sha,
    }
