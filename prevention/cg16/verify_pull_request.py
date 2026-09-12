#!/usr/bin/env python3
import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path


class PullRequestAttestationError(Exception):
    def __init__(self, reason, detail=None):
        super().__init__(reason)
        self.reason = reason
        self.detail = detail


def require(condition, reason, detail=None):
    if not condition:
        raise PullRequestAttestationError(reason, detail)


def load(path):
    return json.loads(Path(path).read_text())


def canonical_digest(value):
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def source_core(attestation):
    return {key: value for key, value in attestation.items() if key not in {"status", "decision", "publicationAttestationId"}}


def git(repo, args, *, check=True):
    cp = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, check=False)
    if check and cp.returncode != 0:
        raise PullRequestAttestationError("GIT_READBACK_FAILED", {
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


def validate_source(protocol, attestation):
    require(protocol.get("id") == "CG16-CONTROLLED-PULL-REQUEST-CREATION", "WRONG_PROTOCOL")
    require(protocol.get("status") in {"CANDIDATE", "QUALIFIED"}, "UNEXPECTED_PROTOCOL_STATUS")
    require(isinstance(attestation, dict), "CG15_PUBLICATION_ATTESTATION_REQUIRED")
    rule = protocol["pullRequest"]
    require(attestation.get("protocolId") == rule["requiredSourceProtocol"], "CG15_PROTOCOL_MISMATCH")
    require(attestation.get("status") == rule["requiredSourceStatus"], "CG15_PUBLICATION_ATTESTATION_STATUS_INVALID")
    require(attestation.get("decision") == rule["requiredSourceDecision"], "CG15_PUBLICATION_ATTESTATION_DECISION_INVALID")
    require(canonical_digest(source_core(attestation)) == attestation.get("publicationAttestationId"), "CG15_PUBLICATION_ATTESTATION_ID_MISMATCH")
    require(re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", attestation.get("repository", "")) is not None, "GITHUB_REPOSITORY_SLUG_REQUIRED")
    require(attestation.get("baseBranch") == rule["baseBranch"], "BASE_BRANCH_MISMATCH")
    require(attestation.get("workBranch") != rule["baseBranch"], "HEAD_BRANCH_MUST_NOT_BE_BASE_BRANCH")
    require(attestation.get("remoteBranchSha") == attestation.get("commitSha"), "CG15_REMOTE_COMMIT_BINDING_INVALID")
    require(attestation.get("pullRequestAuthorization") == "NOT_GRANTED", "CG15_SOURCE_AUTHORITY_INVALID")
    require(attestation.get("mergeAuthorization") == "NOT_GRANTED", "CG15_SOURCE_AUTHORITY_INVALID")
    require(attestation.get("directMainUpdate") is False, "CG15_SOURCE_AUTHORITY_INVALID")
    require(attestation.get("repositoryAdministration") is False, "CG15_SOURCE_AUTHORITY_INVALID")
    require(attestation.get("consumerAdoption") is False, "CG15_SOURCE_AUTHORITY_INVALID")
    return (
        sorted_unique_strings(attestation.get("changedPaths"), "CG15_CHANGED_PATHS_INVALID"),
        sorted_unique_strings(attestation.get("verificationClasses"), "CG15_VERIFICATION_CLASSES_INVALID"),
    )


def remote_ref(repo, remote_name, ref):
    cp = git(repo, ["ls-remote", "--refs", remote_name, ref], check=False)
    require(cp.returncode == 0, "REMOTE_READ_FAILED", {"ref": ref, "stderr": cp.stderr.decode("utf-8", "replace")[-4000:]})
    lines = [line for line in cp.stdout.decode("utf-8", "replace").splitlines() if line.strip()]
    require(len(lines) <= 1, "REMOTE_REF_AMBIGUOUS", {"ref": ref})
    if not lines:
        return None
    parts = lines[0].split("\t")
    require(len(parts) == 2 and parts[1] == ref and re.fullmatch(r"[0-9a-f]{40}", parts[0]), "REMOTE_REF_INVALID", {"ref": ref})
    return parts[0]


class GitHubClient:
    def __init__(self, token, api_url="https://api.github.com"):
        require(isinstance(token, str) and token, "GITHUB_TOKEN_REQUIRED")
        self.token = token
        self.api_url = api_url.rstrip("/")

    def get(self, path):
        req = urllib.request.Request(self.api_url + path, method="GET")
        req.add_header("Accept", "application/vnd.github+json")
        req.add_header("Authorization", f"Bearer {self.token}")
        req.add_header("X-GitHub-Api-Version", "2022-11-28")
        try:
            with urllib.request.urlopen(req, timeout=30) as response:
                raw = response.read()
                return json.loads(raw.decode("utf-8")) if raw else None
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", "replace")[-4000:]
            raise PullRequestAttestationError("GITHUB_API_ERROR", {"path": path, "status": exc.code, "body": raw})
        except urllib.error.URLError as exc:
            raise PullRequestAttestationError("GITHUB_API_UNREACHABLE", {"path": path, "error": str(exc)})


def list_pr_files(client, repository, number):
    paths = []
    page = 1
    while True:
        items = client.get(f"/repos/{repository}/pulls/{number}/files?per_page=100&page={page}")
        require(isinstance(items, list), "PULL_REQUEST_FILES_RESPONSE_INVALID")
        for item in items:
            filename = item.get("filename") if isinstance(item, dict) else None
            require(isinstance(filename, str) and filename, "PULL_REQUEST_FILE_INVALID")
            paths.append(filename)
        if len(items) < 100:
            break
        page += 1
        require(page <= 100, "PULL_REQUEST_FILES_PAGINATION_LIMIT")
    require(len(paths) == len(set(paths)), "PULL_REQUEST_FILE_DUPLICATE")
    return sorted(paths)


def verify(protocol, source_attestation, receipt, repo, token, api_url="https://api.github.com"):
    changed_paths, verification_classes = validate_source(protocol, source_attestation)
    rule = protocol["pullRequest"]
    require(receipt.get("schemaVersion") == 1, "PULL_REQUEST_RECEIPT_SCHEMA_MISMATCH")
    require(receipt.get("protocolId") == protocol["id"], "PULL_REQUEST_RECEIPT_PROTOCOL_MISMATCH")
    require(receipt.get("status") == "PULL_REQUEST_CREATED_UNVERIFIED", "PULL_REQUEST_RECEIPT_STATUS_INVALID")
    require(receipt.get("provider") == rule["provider"], "PULL_REQUEST_PROVIDER_MISMATCH")

    for field in ("caseId", "repository"):
        require(receipt.get(field) == source_attestation.get(field), "PULL_REQUEST_SOURCE_BINDING_MISMATCH", {"field": field})
    require(receipt.get("sourcePublicationAttestationId") == source_attestation.get("publicationAttestationId"), "SOURCE_PUBLICATION_ATTESTATION_ID_MISMATCH")
    require(receipt.get("sourcePublicationAttestationSha256") == canonical_digest(source_attestation), "SOURCE_PUBLICATION_ATTESTATION_DIGEST_MISMATCH")
    require(receipt.get("baseBranch") == source_attestation.get("baseBranch"), "PULL_REQUEST_BASE_REF_MISMATCH")
    require(receipt.get("baseSha") == source_attestation.get("baseSha"), "PULL_REQUEST_BASE_SHA_MISMATCH")
    require(receipt.get("headBranch") == source_attestation.get("workBranch"), "PULL_REQUEST_HEAD_REF_MISMATCH")
    require(receipt.get("headSha") == source_attestation.get("commitSha"), "PULL_REQUEST_HEAD_SHA_MISMATCH")
    require(receipt.get("changedPaths") == changed_paths, "PULL_REQUEST_PATH_RECEIPT_MISMATCH")
    require(receipt.get("diffSha256") == source_attestation.get("diffSha256"), "PULL_REQUEST_DIFF_RECEIPT_MISMATCH")
    require(receipt.get("verificationClasses") == verification_classes, "PULL_REQUEST_VERIFICATION_CLASS_RECEIPT_MISMATCH")
    require(receipt.get("commitCount") == 1, "PULL_REQUEST_COMMIT_COUNT_MISMATCH")
    require(receipt.get("draft") is False, "DRAFT_PULL_REQUEST_FORBIDDEN")
    require(receipt.get("maintainerCanModify") is False, "MAINTAINER_MUTATION_FORBIDDEN")
    require(receipt.get("autoMerge") is False, "AUTO_MERGE_AUTHORIZATION_FORBIDDEN")
    require(receipt.get("pullRequestAuthority") == "SINGLE_OPEN_PR_CREATE_ONLY", "PULL_REQUEST_AUTHORITY_INVALID")
    require(receipt.get("pullRequestUpdateAuthorization") == "NOT_GRANTED", "PULL_REQUEST_UPDATE_AUTHORIZATION_FORBIDDEN")
    require(receipt.get("reviewSubmissionAuthorization") == "NOT_GRANTED", "REVIEW_SUBMISSION_AUTHORIZATION_FORBIDDEN")
    require(receipt.get("autoMergeAuthorization") == "NOT_GRANTED", "AUTO_MERGE_AUTHORIZATION_FORBIDDEN")
    require(receipt.get("mergeAuthorization") == "NOT_GRANTED", "MERGE_AUTHORIZATION_FORBIDDEN")
    require(receipt.get("directMainUpdate") is False, "DIRECT_MAIN_UPDATE_FORBIDDEN")
    require(receipt.get("repositoryAdministration") is False, "REPOSITORY_ADMINISTRATION_FORBIDDEN")
    require(receipt.get("consumerAdoption") is False, "CONSUMER_ADOPTION_FORBIDDEN")
    number = receipt.get("pullRequestNumber")
    require(isinstance(number, int) and number > 0, "PULL_REQUEST_NUMBER_INVALID")

    repo = Path(repo).resolve()
    remote_url = git(repo, ["remote", "get-url", "origin"], check=False)
    require(remote_url.returncode == 0, "ORIGIN_REMOTE_REQUIRED")
    actual_repository = normalize_remote(remote_url.stdout.decode())
    require(actual_repository == source_attestation["repository"], "REPOSITORY_IDENTITY_MISMATCH")
    branch = git(repo, ["branch", "--show-current"]).stdout.decode().strip()
    require(branch == source_attestation["workBranch"], "WORK_BRANCH_MISMATCH")
    head = git(repo, ["rev-parse", "HEAD"]).stdout.decode().strip()
    require(head == source_attestation["commitSha"], "LOCAL_HEAD_COMMIT_MISMATCH")
    status = git(repo, ["status", "--porcelain", "--untracked-files=all"]).stdout.decode()
    require(status == "", "LOCAL_WORKTREE_NOT_CLEAN", {"status": status})
    remote_base = remote_ref(repo, "origin", f"refs/heads/{source_attestation['baseBranch']}")
    remote_head = remote_ref(repo, "origin", f"refs/heads/{source_attestation['workBranch']}")
    require(remote_base == source_attestation["baseSha"], "REMOTE_BASE_SHA_MISMATCH", {"actual": remote_base})
    require(remote_head == source_attestation["commitSha"], "REMOTE_HEAD_SHA_MISMATCH", {"actual": remote_head})

    client = GitHubClient(token, api_url)
    pr = client.get(f"/repos/{source_attestation['repository']}/pulls/{number}")
    require(isinstance(pr, dict), "PULL_REQUEST_RESPONSE_INVALID")
    require(pr.get("number") == number, "PULL_REQUEST_NUMBER_MISMATCH")
    require(pr.get("state") == "open", "PULL_REQUEST_NOT_OPEN")
    require(pr.get("draft") is False, "DRAFT_PULL_REQUEST_FORBIDDEN")
    require(pr.get("merged") is False, "PULL_REQUEST_ALREADY_MERGED")
    require(pr.get("maintainer_can_modify") is False, "MAINTAINER_MUTATION_FORBIDDEN")
    require(pr.get("auto_merge") is None, "AUTO_MERGE_FORBIDDEN")
    require(pr.get("title") == receipt.get("title"), "PULL_REQUEST_TITLE_MISMATCH")
    live_body = pr.get("body") or ""
    require(hashlib.sha256(live_body.encode("utf-8")).hexdigest() == receipt.get("bodySha256"), "PULL_REQUEST_BODY_DIGEST_MISMATCH")
    base = pr.get("base") or {}
    head_info = pr.get("head") or {}
    require(base.get("ref") == source_attestation["baseBranch"], "PULL_REQUEST_BASE_REF_MISMATCH")
    require(base.get("sha") == source_attestation["baseSha"], "PULL_REQUEST_BASE_SHA_MISMATCH", {"actual": base.get("sha")})
    require(head_info.get("ref") == source_attestation["workBranch"], "PULL_REQUEST_HEAD_REF_MISMATCH")
    require(head_info.get("sha") == source_attestation["commitSha"], "PULL_REQUEST_HEAD_SHA_MISMATCH", {"actual": head_info.get("sha")})
    require((base.get("repo") or {}).get("full_name") == source_attestation["repository"], "PULL_REQUEST_BASE_REPOSITORY_MISMATCH")
    require((head_info.get("repo") or {}).get("full_name") == source_attestation["repository"], "PULL_REQUEST_HEAD_REPOSITORY_MISMATCH")
    require(pr.get("commits") == 1, "PULL_REQUEST_COMMIT_COUNT_MISMATCH", {"actual": pr.get("commits")})
    actual_paths = list_pr_files(client, source_attestation["repository"], number)
    require(actual_paths == changed_paths, "PULL_REQUEST_CHANGED_PATHS_MISMATCH", {"expected": changed_paths, "actual": actual_paths})
    require(pr.get("node_id") == receipt.get("pullRequestNodeId"), "PULL_REQUEST_NODE_ID_MISMATCH")
    require(pr.get("html_url") == receipt.get("pullRequestUrl"), "PULL_REQUEST_URL_MISMATCH")

    receipt_sha = canonical_digest(receipt)
    core = {
        "schemaVersion": 1,
        "protocolId": protocol["id"],
        "caseId": source_attestation["caseId"],
        "repository": source_attestation["repository"],
        "provider": rule["provider"],
        "sourceRemotePublicationAttestationId": source_attestation["publicationAttestationId"],
        "sourceRemotePublicationAttestationSha256": canonical_digest(source_attestation),
        "pullRequestReceiptSha256": receipt_sha,
        "pullRequestNumber": number,
        "pullRequestNodeId": pr.get("node_id"),
        "pullRequestUrl": pr.get("html_url"),
        "baseBranch": source_attestation["baseBranch"],
        "baseSha": source_attestation["baseSha"],
        "headBranch": source_attestation["workBranch"],
        "headSha": source_attestation["commitSha"],
        "changedPaths": actual_paths,
        "diffSha256": source_attestation["diffSha256"],
        "verificationClasses": verification_classes,
        "title": pr.get("title"),
        "bodySha256": hashlib.sha256(live_body.encode("utf-8")).hexdigest(),
        "pullRequestAuthority": "SINGLE_OPEN_PR_CREATE_ONLY",
        "pullRequestUpdateAuthorization": "NOT_GRANTED",
        "reviewSubmissionAuthorization": "NOT_GRANTED",
        "autoMergeAuthorization": "NOT_GRANTED",
        "mergeAuthorization": "NOT_GRANTED",
        "directMainUpdate": False,
        "repositoryAdministration": False,
        "consumerAdoption": False,
    }
    return {
        **core,
        "status": "PULL_REQUEST_ATTESTED",
        "decision": "CONTROLLED_PULL_REQUEST_CREATION_VERIFIED",
        "pullRequestAttestationId": canonical_digest(core),
    }


def parse_args():
    parser = argparse.ArgumentParser(description="CG-16 GitHub pull request identity verifier")
    parser.add_argument("--protocol", required=True)
    parser.add_argument("--source-attestation", required=True)
    parser.add_argument("--receipt", required=True)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--api-url", default=os.environ.get("GITHUB_API_URL", "https://api.github.com"))
    return parser.parse_args()


def main():
    args = parse_args()
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    try:
        result = verify(load(args.protocol), load(args.source_attestation), load(args.receipt), args.repo, token, args.api_url)
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    except PullRequestAttestationError as exc:
        out = {"schemaVersion": 1, "status": "REJECTED", "decision": "PULL_REQUEST_NOT_VERIFIED", "reason": exc.reason}
        if exc.detail is not None:
            out["detail"] = exc.detail
        print(json.dumps(out, indent=2, sort_keys=True))
        return 2


if __name__ == "__main__":
    sys.exit(main())
