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
    return {k: v for k, v in attestation.items() if k not in {"status", "decision", "publicationAttestationId"}}


def request_core(request):
    return {k: v for k, v in request.items() if k not in {"status", "decision", "requestId"}}


def git(repo, args, check=True):
    cp = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, check=False)
    if check and cp.returncode != 0:
        raise PullRequestAttestationError("GIT_READBACK_FAILED", {"args": args, "stderr": cp.stderr.decode("utf-8", "replace")[-4000:]})
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
    changed_paths = sorted_unique_strings(attestation.get("changedPaths"), "CG15_CHANGED_PATHS_INVALID")
    verification_classes = sorted_unique_strings(attestation.get("verificationClasses"), "CG15_VERIFICATION_CLASSES_INVALID")
    require(changed_paths and verification_classes, "CG15_SOURCE_COLLECTION_EMPTY")
    return changed_paths, verification_classes


def validate_request(protocol, source, request):
    changed_paths, verification_classes = validate_source(protocol, source)
    rule = protocol["pullRequest"]
    require(isinstance(request, dict), "PULL_REQUEST_CREATION_REQUEST_REQUIRED")
    require(request.get("protocolId") == protocol["id"], "PULL_REQUEST_REQUEST_PROTOCOL_MISMATCH")
    require(request.get("status") == "PULL_REQUEST_CREATION_REQUEST_READY", "PULL_REQUEST_REQUEST_STATUS_INVALID")
    require(request.get("decision") == "CONTROLLED_PULL_REQUEST_REQUEST_VERIFIED", "PULL_REQUEST_REQUEST_DECISION_INVALID")
    require(canonical_digest(request_core(request)) == request.get("requestId"), "PULL_REQUEST_REQUEST_ID_MISMATCH")
    require(request.get("provider") == rule["provider"], "PULL_REQUEST_PROVIDER_MISMATCH")
    require(request.get("externalExecutor") == rule["externalExecutor"], "PULL_REQUEST_EXECUTOR_MISMATCH")
    for key, expected in (
        ("caseId", source["caseId"]), ("repository", source["repository"]),
        ("baseBranch", source["baseBranch"]), ("baseSha", source["baseSha"]),
        ("headBranch", source["workBranch"]), ("headSha", source["commitSha"]),
        ("diffSha256", source["diffSha256"]),
    ):
        require(request.get(key) == expected, "PULL_REQUEST_REQUEST_SOURCE_BINDING_MISMATCH", {"field": key})
    require(request.get("sourcePublicationAttestationId") == source["publicationAttestationId"], "SOURCE_PUBLICATION_ATTESTATION_ID_MISMATCH")
    require(request.get("sourcePublicationAttestationSha256") == canonical_digest(source), "SOURCE_PUBLICATION_ATTESTATION_DIGEST_MISMATCH")
    require(request.get("changedPaths") == changed_paths, "PULL_REQUEST_REQUEST_PATH_MISMATCH")
    require(request.get("verificationClasses") == verification_classes, "PULL_REQUEST_REQUEST_VERIFICATION_CLASS_MISMATCH")
    require(request.get("draft") is False, "DRAFT_PULL_REQUEST_FORBIDDEN")
    require(request.get("maintainerCanModify") is False, "MAINTAINER_MUTATION_FORBIDDEN")
    require(request.get("requestAuthority") == "SINGLE_OPEN_PR_CREATE_REQUEST_ONLY", "PULL_REQUEST_REQUEST_AUTHORITY_INVALID")
    for field in ("pullRequestUpdateAuthorization", "reviewSubmissionAuthorization", "autoMergeAuthorization", "mergeAuthorization"):
        require(request.get(field) == "NOT_GRANTED", "DOWNSTREAM_AUTHORIZATION_FORBIDDEN", {"field": field})
    require(request.get("directMainUpdate") is False, "DIRECT_MAIN_UPDATE_FORBIDDEN")
    require(request.get("repositoryAdministration") is False, "REPOSITORY_ADMINISTRATION_FORBIDDEN")
    require(request.get("consumerAdoption") is False, "CONSUMER_ADOPTION_FORBIDDEN")
    return changed_paths, verification_classes


def remote_ref(repo, ref):
    cp = git(repo, ["ls-remote", "--refs", "origin", ref], check=False)
    require(cp.returncode == 0, "REMOTE_READ_FAILED", {"ref": ref})
    lines = [line for line in cp.stdout.decode().splitlines() if line.strip()]
    require(len(lines) <= 1, "REMOTE_REF_AMBIGUOUS", {"ref": ref})
    if not lines:
        return None
    sha, actual_ref = lines[0].split("\t", 1)
    require(actual_ref == ref and re.fullmatch(r"[0-9a-f]{40}", sha), "REMOTE_REF_INVALID", {"ref": ref})
    return sha


class GitHubReader:
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
            raise PullRequestAttestationError("GITHUB_API_READ_ERROR", {"path": path, "status": exc.code, "body": exc.read().decode("utf-8", "replace")[-4000:]})
        except urllib.error.URLError as exc:
            raise PullRequestAttestationError("GITHUB_API_UNREACHABLE", {"path": path, "error": str(exc)})


def list_pr_files(client, repository, number):
    paths = []
    page = 1
    while True:
        items = client.get(f"/repos/{repository}/pulls/{number}/files?per_page=100&page={page}")
        require(isinstance(items, list), "PULL_REQUEST_FILES_RESPONSE_INVALID")
        paths.extend(item.get("filename") for item in items if isinstance(item, dict))
        require(all(isinstance(path, str) and path for path in paths), "PULL_REQUEST_FILE_INVALID")
        if len(items) < 100:
            break
        page += 1
        require(page <= 100, "PULL_REQUEST_FILES_PAGINATION_LIMIT")
    require(len(paths) == len(set(paths)), "PULL_REQUEST_FILE_DUPLICATE")
    return sorted(paths)


def verify(protocol, source, request, external_receipt, repo, token, api_url="https://api.github.com"):
    changed_paths, verification_classes = validate_request(protocol, source, request)
    rule = protocol["pullRequest"]
    require(isinstance(external_receipt, dict), "EXTERNAL_EXECUTION_RECEIPT_REQUIRED")
    require(external_receipt.get("schemaVersion") == 1, "EXTERNAL_EXECUTION_RECEIPT_SCHEMA_MISMATCH")
    require(external_receipt.get("executor") == rule["externalExecutor"], "EXTERNAL_EXECUTOR_MISMATCH")
    require(external_receipt.get("requestId") == request["requestId"], "EXTERNAL_REQUEST_ID_MISMATCH")
    require(external_receipt.get("repository") == source["repository"], "EXTERNAL_REPOSITORY_MISMATCH")
    require(external_receipt.get("baseBranch") == source["baseBranch"], "EXTERNAL_BASE_BRANCH_MISMATCH")
    require(external_receipt.get("headBranch") == source["workBranch"], "EXTERNAL_HEAD_BRANCH_MISMATCH")
    number = external_receipt.get("pullRequestNumber")
    require(isinstance(number, int) and number > 0, "PULL_REQUEST_NUMBER_INVALID")
    require(external_receipt.get("mergeAuthorization") == "NOT_GRANTED", "MERGE_AUTHORIZATION_FORBIDDEN")

    repo = Path(repo).resolve()
    remote = git(repo, ["remote", "get-url", "origin"], check=False)
    require(remote.returncode == 0 and normalize_remote(remote.stdout.decode()) == source["repository"], "REPOSITORY_IDENTITY_MISMATCH")
    require(git(repo, ["branch", "--show-current"]).stdout.decode().strip() == source["workBranch"], "WORK_BRANCH_MISMATCH")
    require(git(repo, ["rev-parse", "HEAD"]).stdout.decode().strip() == source["commitSha"], "LOCAL_HEAD_COMMIT_MISMATCH")
    require(git(repo, ["status", "--porcelain", "--untracked-files=all"]).stdout.decode() == "", "LOCAL_WORKTREE_NOT_CLEAN")
    require(remote_ref(repo, f"refs/heads/{source['baseBranch']}") == source["baseSha"], "REMOTE_BASE_SHA_MISMATCH")
    require(remote_ref(repo, f"refs/heads/{source['workBranch']}") == source["commitSha"], "REMOTE_HEAD_SHA_MISMATCH")

    client = GitHubReader(token, api_url)
    pr = client.get(f"/repos/{source['repository']}/pulls/{number}")
    require(isinstance(pr, dict) and pr.get("number") == number, "PULL_REQUEST_RESPONSE_INVALID")
    require(pr.get("state") == "open", "PULL_REQUEST_NOT_OPEN")
    require(pr.get("draft") is False, "DRAFT_PULL_REQUEST_FORBIDDEN")
    require(pr.get("merged") is False, "PULL_REQUEST_ALREADY_MERGED")
    require(pr.get("auto_merge") is None, "AUTO_MERGE_FORBIDDEN")
    require(pr.get("title") == request["title"], "PULL_REQUEST_TITLE_MISMATCH")
    require((pr.get("body") or "") == request["body"], "PULL_REQUEST_BODY_MISMATCH")
    base = pr.get("base") or {}
    head = pr.get("head") or {}
    require(base.get("ref") == source["baseBranch"] and base.get("sha") == source["baseSha"], "PULL_REQUEST_BASE_IDENTITY_MISMATCH")
    require(head.get("ref") == source["workBranch"] and head.get("sha") == source["commitSha"], "PULL_REQUEST_HEAD_IDENTITY_MISMATCH")
    require((base.get("repo") or {}).get("full_name") == source["repository"], "PULL_REQUEST_BASE_REPOSITORY_MISMATCH")
    require((head.get("repo") or {}).get("full_name") == source["repository"], "PULL_REQUEST_HEAD_REPOSITORY_MISMATCH")
    require(pr.get("commits") == 1, "PULL_REQUEST_COMMIT_COUNT_MISMATCH", {"actual": pr.get("commits")})
    actual_paths = list_pr_files(client, source["repository"], number)
    require(actual_paths == changed_paths, "PULL_REQUEST_CHANGED_PATHS_MISMATCH", {"expected": changed_paths, "actual": actual_paths})
    if external_receipt.get("pullRequestUrl") is not None:
        require(pr.get("html_url") == external_receipt["pullRequestUrl"], "PULL_REQUEST_URL_MISMATCH")

    core = {
        "schemaVersion": 1,
        "protocolId": protocol["id"],
        "caseId": source["caseId"],
        "repository": source["repository"],
        "provider": rule["provider"],
        "externalExecutor": rule["externalExecutor"],
        "sourceRemotePublicationAttestationId": source["publicationAttestationId"],
        "sourceRemotePublicationAttestationSha256": canonical_digest(source),
        "pullRequestRequestId": request["requestId"],
        "pullRequestRequestSha256": canonical_digest(request),
        "externalExecutionReceiptSha256": canonical_digest(external_receipt),
        "pullRequestNumber": number,
        "pullRequestNodeId": pr.get("node_id"),
        "pullRequestUrl": pr.get("html_url"),
        "baseBranch": source["baseBranch"],
        "baseSha": source["baseSha"],
        "headBranch": source["workBranch"],
        "headSha": source["commitSha"],
        "changedPaths": actual_paths,
        "diffSha256": source["diffSha256"],
        "verificationClasses": verification_classes,
        "title": pr.get("title"),
        "bodySha256": hashlib.sha256((pr.get("body") or "").encode()).hexdigest(),
        "pullRequestUpdateAuthorization": "NOT_GRANTED",
        "reviewSubmissionAuthorization": "NOT_GRANTED",
        "autoMergeAuthorization": "NOT_GRANTED",
        "mergeAuthorization": "NOT_GRANTED",
        "directMainUpdate": False,
        "repositoryAdministration": False,
        "consumerAdoption": False
    }
    return {**core, "status": "PULL_REQUEST_ATTESTED", "decision": "CONTROLLED_PULL_REQUEST_CREATION_VERIFIED", "pullRequestAttestationId": canonical_digest(core)}


def parse_args():
    parser = argparse.ArgumentParser(description="CG-16 external-executor PR identity verifier")
    parser.add_argument("--protocol", required=True)
    parser.add_argument("--source-attestation", required=True)
    parser.add_argument("--request", required=True)
    parser.add_argument("--external-receipt", required=True)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--api-url", default=os.environ.get("GITHUB_API_URL", "https://api.github.com"))
    return parser.parse_args()


def main():
    args = parse_args()
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    try:
        result = verify(load(args.protocol), load(args.source_attestation), load(args.request), load(args.external_receipt), args.repo, token, args.api_url)
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
