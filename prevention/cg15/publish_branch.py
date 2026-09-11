#!/usr/bin/env python3
import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path


class PublicationError(Exception):
    def __init__(self, reason, detail=None):
        super().__init__(reason)
        self.reason = reason
        self.detail = detail


def require(condition, reason, detail=None):
    if not condition:
        raise PublicationError(reason, detail)


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
        raise PublicationError("GIT_COMMAND_FAILED", {
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
    require(protocol.get("id") == "CG15-CONTROLLED-REMOTE-BRANCH-PUBLICATION", "WRONG_PROTOCOL")
    require(protocol.get("status") in {"CANDIDATE", "QUALIFIED"}, "UNEXPECTED_PROTOCOL_STATUS")
    require(isinstance(attestation, dict), "CG14_COMMIT_ATTESTATION_REQUIRED")
    publication = protocol["publication"]
    require(attestation.get("protocolId") == publication["requiredSourceProtocol"], "CG14_PROTOCOL_MISMATCH")
    require(attestation.get("status") == publication["requiredSourceStatus"], "CG14_COMMIT_ATTESTATION_STATUS_INVALID")
    require(attestation.get("decision") == publication["requiredSourceDecision"], "CG14_COMMIT_ATTESTATION_DECISION_INVALID")

    for field in ("caseId", "repository", "baseSha", "workBranch", "commitSha", "treeSha", "diffSha256", "commitAttestationId"):
        require(isinstance(attestation.get(field), str) and attestation[field], "CG14_COMMIT_ATTESTATION_FIELD_REQUIRED", {"field": field})
    for field in ("baseSha", "commitSha", "treeSha"):
        require(re.fullmatch(r"[0-9a-f]{40}", attestation[field]) is not None, "CG14_GIT_ID_INVALID", {"field": field})
    for field in ("diffSha256", "commitAttestationId"):
        require(re.fullmatch(r"[0-9a-f]{64}", attestation[field]) is not None, "CG14_DIGEST_INVALID", {"field": field})

    require(canonical_digest(source_core(attestation)) == attestation["commitAttestationId"], "CG14_COMMIT_ATTESTATION_ID_MISMATCH")
    require(attestation.get("parentSha") == attestation["baseSha"], "CG14_PARENT_BINDING_INVALID")
    require(attestation.get("pushAuthorization") == "NOT_GRANTED", "CG14_SOURCE_AUTHORITY_INVALID", {"field": "pushAuthorization"})
    require(attestation.get("pullRequestAuthorization") == "NOT_GRANTED", "CG14_SOURCE_AUTHORITY_INVALID", {"field": "pullRequestAuthorization"})
    require(attestation.get("mergeAuthorization") == "NOT_GRANTED", "CG14_SOURCE_AUTHORITY_INVALID", {"field": "mergeAuthorization"})
    require(attestation.get("directMainUpdate") is False, "CG14_SOURCE_AUTHORITY_INVALID", {"field": "directMainUpdate"})
    require(attestation.get("repositoryAdministration") is False, "CG14_SOURCE_AUTHORITY_INVALID", {"field": "repositoryAdministration"})
    require(attestation.get("consumerAdoption") is False, "CG14_SOURCE_AUTHORITY_INVALID", {"field": "consumerAdoption"})

    work_branch = attestation["workBranch"]
    require(work_branch != publication["baseBranch"], "WORK_BRANCH_MUST_NOT_BE_BASE_BRANCH")
    changed_paths = sorted_unique_strings(attestation.get("changedPaths"), "CG14_CHANGED_PATHS_INVALID")
    verification_classes = sorted_unique_strings(attestation.get("verificationClasses"), "CG14_VERIFICATION_CLASSES_INVALID")
    require(bool(changed_paths), "CG14_CHANGED_PATHS_EMPTY")
    require(bool(verification_classes), "CG14_VERIFICATION_CLASSES_EMPTY")
    return changed_paths, verification_classes


def validate_branch_name(repo, branch, base_branch):
    require(isinstance(branch, str) and branch, "WORK_BRANCH_INVALID")
    require(branch != base_branch, "WORK_BRANCH_MUST_NOT_BE_BASE_BRANCH")
    require(not branch.startswith("refs/"), "WORK_BRANCH_MUST_BE_SHORT_NAME")
    cp = git(repo, ["check-ref-format", "--branch", branch], check=False)
    require(cp.returncode == 0, "WORK_BRANCH_INVALID", {"branch": branch})
    return branch


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


def verify_local_commit(attestation, repo, changed_paths):
    branch = git(repo, ["branch", "--show-current"]).stdout.decode().strip()
    require(branch == attestation["workBranch"], "WORK_BRANCH_MISMATCH", {"expected": attestation["workBranch"], "actual": branch})
    head = git(repo, ["rev-parse", "HEAD"]).stdout.decode().strip()
    require(head == attestation["commitSha"], "LOCAL_HEAD_COMMIT_MISMATCH", {"expected": attestation["commitSha"], "actual": head})
    status = git(repo, ["status", "--porcelain", "--untracked-files=all"]).stdout.decode()
    require(status == "", "LOCAL_WORKTREE_NOT_CLEAN", {"status": status})
    parents = git(repo, ["show", "-s", "--format=%P", head]).stdout.decode().strip().split()
    require(parents == [attestation["baseSha"]], "LOCAL_COMMIT_PARENT_MISMATCH", {"parents": parents})
    tree_sha = git(repo, ["rev-parse", f"{head}^{{tree}}"]).stdout.decode().strip()
    require(tree_sha == attestation["treeSha"], "LOCAL_COMMIT_TREE_MISMATCH")
    actual_paths = sorted(path.decode("utf-8") for path in git(repo, ["diff", "--name-only", "-z", attestation["baseSha"], head, "--"]).stdout.split(b"\0") if path)
    require(actual_paths == changed_paths, "LOCAL_COMMIT_PATHS_MISMATCH", {"expected": changed_paths, "actual": actual_paths})
    diff_bytes = git(repo, ["diff", "--binary", "--full-index", "--no-ext-diff", attestation["baseSha"], head, "--"]).stdout
    diff_sha = hashlib.sha256(diff_bytes).hexdigest()
    require(diff_sha == attestation["diffSha256"], "LOCAL_COMMIT_DIFF_DIGEST_MISMATCH", {"expected": attestation["diffSha256"], "actual": diff_sha})


def publish(protocol, attestation, repo):
    changed_paths, verification_classes = validate_source_attestation(protocol, attestation)
    repo = Path(repo).resolve()
    inside = git(repo, ["rev-parse", "--is-inside-work-tree"], check=False)
    require(inside.returncode == 0 and inside.stdout.decode().strip() == "true", "REPOSITORY_NOT_GIT_WORKTREE")

    publication = protocol["publication"]
    remote_name = publication["remoteName"]
    base_branch = publication["baseBranch"]
    work_branch = validate_branch_name(repo, attestation["workBranch"], base_branch)

    remote_url = git(repo, ["remote", "get-url", remote_name], check=False)
    require(remote_url.returncode == 0, "ORIGIN_REMOTE_REQUIRED")
    actual_repository = normalize_remote(remote_url.stdout.decode())
    require(actual_repository == attestation["repository"], "REPOSITORY_IDENTITY_MISMATCH", {
        "expected": attestation["repository"], "actual": actual_repository,
    })

    verify_local_commit(attestation, repo, changed_paths)

    base_ref = f"refs/heads/{base_branch}"
    target_ref = f"refs/heads/{work_branch}"
    remote_base_before = remote_ref(repo, remote_name, base_ref)
    require(remote_base_before is not None, "REMOTE_BASE_BRANCH_MISSING")
    require(remote_base_before == attestation["baseSha"], "REMOTE_BASE_SHA_MISMATCH", {
        "expected": attestation["baseSha"], "actual": remote_base_before,
    })
    remote_target_before = remote_ref(repo, remote_name, target_ref)
    require(remote_target_before is None, "REMOTE_TARGET_BRANCH_ALREADY_EXISTS", {"actual": remote_target_before})

    base_refspec = f"{attestation['baseSha']}:{base_ref}"
    target_refspec = f"{attestation['commitSha']}:{target_ref}"
    create_only_lease = f"--force-with-lease={target_ref}:"
    pushed = git(repo, ["push", "--porcelain", "--atomic", create_only_lease, remote_name, base_refspec, target_refspec], check=False)
    require(pushed.returncode == 0, "REMOTE_PUBLICATION_FAILED", {
        "stdout": pushed.stdout.decode("utf-8", "replace")[-4000:],
        "stderr": pushed.stderr.decode("utf-8", "replace")[-4000:],
    })

    remote_target_after = remote_ref(repo, remote_name, target_ref)
    require(remote_target_after == attestation["commitSha"], "REMOTE_TARGET_SHA_MISMATCH", {
        "expected": attestation["commitSha"], "actual": remote_target_after,
    })
    remote_base_after = remote_ref(repo, remote_name, base_ref)
    require(remote_base_after == attestation["baseSha"], "REMOTE_BASE_CHANGED_DURING_PUBLICATION", {
        "expected": attestation["baseSha"], "actual": remote_base_after,
    })
    verify_local_commit(attestation, repo, changed_paths)

    return {
        "schemaVersion": 1,
        "protocolId": protocol["id"],
        "status": "REMOTE_BRANCH_PUBLISHED_UNVERIFIED",
        "caseId": attestation["caseId"],
        "repository": attestation["repository"],
        "remoteName": remote_name,
        "baseBranch": base_branch,
        "baseSha": attestation["baseSha"],
        "workBranch": work_branch,
        "commitSha": attestation["commitSha"],
        "treeSha": attestation["treeSha"],
        "changedPaths": changed_paths,
        "diffSha256": attestation["diffSha256"],
        "verificationClasses": verification_classes,
        "sourceCommitAttestationId": attestation["commitAttestationId"],
        "sourceCommitAttestationSha256": canonical_digest(attestation),
        "remoteBaseBefore": remote_base_before,
        "remoteBaseAfter": remote_base_after,
        "remoteTargetBefore": "ABSENT",
        "remoteTargetAfter": remote_target_after,
        "baseRefspec": base_refspec,
        "targetRefspec": target_refspec,
        "atomicPush": True,
        "createOnlyLease": target_ref,
        "unconditionalForcePush": False,
        "existingBranchUpdate": False,
        "remoteRefDeletion": False,
        "publicationAuthority": "SINGLE_REMOTE_BRANCH_CREATE_ONLY",
        "pullRequestAuthorization": "NOT_GRANTED",
        "mergeAuthorization": "NOT_GRANTED",
        "directMainUpdate": False,
        "repositoryAdministration": False,
        "consumerAdoption": False,
    }


def parse_args():
    parser = argparse.ArgumentParser(description="CG-15 controlled remote branch publisher")
    parser.add_argument("--protocol", required=True)
    parser.add_argument("--attestation", required=True)
    parser.add_argument("--repo", required=True)
    return parser.parse_args()


def main():
    args = parse_args()
    try:
        result = publish(load(args.protocol), load(args.attestation), args.repo)
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    except PublicationError as exc:
        out = {"schemaVersion": 1, "status": "REJECTED", "decision": "REMOTE_BRANCH_NOT_PUBLISHED", "reason": exc.reason}
        if exc.detail is not None:
            out["detail"] = exc.detail
        print(json.dumps(out, indent=2, sort_keys=True))
        return 2


if __name__ == "__main__":
    sys.exit(main())
