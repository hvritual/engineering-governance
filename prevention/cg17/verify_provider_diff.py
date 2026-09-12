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

from diff_identity import DiffIdentityError, apply_provider_diff_to_base, local_git_identity


class ProviderDiffAttestationError(Exception):
    def __init__(self, reason, detail=None):
        super().__init__(reason)
        self.reason = reason
        self.detail = detail


def require(condition, reason, detail=None):
    if not condition:
        raise ProviderDiffAttestationError(reason, detail)


def load(path):
    return json.loads(Path(path).read_text())


def canonical_digest(value):
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def source_core(attestation):
    return {k: v for k, v in attestation.items() if k not in {"status", "decision", "pullRequestAttestationId"}}


def sorted_unique_strings(value, reason):
    require(isinstance(value, list), reason)
    require(all(isinstance(item, str) and item for item in value), reason)
    require(len(value) == len(set(value)), reason)
    return sorted(value)


def validate_source(protocol, source):
    require(protocol.get("schemaVersion") == 1, "PROTOCOL_SCHEMA_MISMATCH")
    require(protocol.get("id") == "CG17-PROVIDER-DIFF-READBACK", "WRONG_PROTOCOL")
    require(protocol.get("status") in {"CANDIDATE", "QUALIFIED"}, "UNEXPECTED_PROTOCOL_STATUS")
    require(isinstance(source, dict), "CG16_PULL_REQUEST_ATTESTATION_REQUIRED")
    require(source.get("protocolId") == protocol["sourceProtocol"], "CG16_PROTOCOL_MISMATCH")
    require(source.get("status") == protocol["requiredSourceStatus"], "CG16_PULL_REQUEST_ATTESTATION_STATUS_INVALID")
    require(source.get("decision") == protocol["requiredSourceDecision"], "CG16_PULL_REQUEST_ATTESTATION_DECISION_INVALID")
    require(canonical_digest(source_core(source)) == source.get("pullRequestAttestationId"), "CG16_PULL_REQUEST_ATTESTATION_ID_MISMATCH")
    require(source.get("provider") == protocol["identity"]["provider"], "PROVIDER_MISMATCH")
    require(isinstance(source.get("pullRequestNumber"), int) and source["pullRequestNumber"] > 0, "PULL_REQUEST_NUMBER_INVALID")
    for field in ("repository", "baseBranch", "headBranch", "baseSha", "headSha", "diffSha256"):
        require(isinstance(source.get(field), str) and source[field], "CG16_SOURCE_FIELD_REQUIRED", {"field": field})
    require(re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", source["repository"]) is not None, "GITHUB_REPOSITORY_SLUG_REQUIRED")
    for field in ("baseSha", "headSha"):
        require(re.fullmatch(r"[0-9a-f]{40}", source[field]) is not None, "CG16_GIT_ID_INVALID", {"field": field})
    require(re.fullmatch(r"[0-9a-f]{64}", source["diffSha256"]) is not None, "CG16_DIFF_DIGEST_INVALID")
    changed_paths = sorted_unique_strings(source.get("changedPaths"), "CG16_CHANGED_PATHS_INVALID")
    verification_classes = sorted_unique_strings(source.get("verificationClasses"), "CG16_VERIFICATION_CLASSES_INVALID")
    require(changed_paths and verification_classes, "CG16_SOURCE_COLLECTION_EMPTY")
    require(source.get("pullRequestUpdateAuthorization") == "NOT_GRANTED", "DOWNSTREAM_AUTHORITY_ESCALATION", {"field": "pullRequestUpdateAuthorization"})
    require(source.get("reviewSubmissionAuthorization") == "NOT_GRANTED", "DOWNSTREAM_AUTHORITY_ESCALATION", {"field": "reviewSubmissionAuthorization"})
    require(source.get("autoMergeAuthorization") == "NOT_GRANTED", "DOWNSTREAM_AUTHORITY_ESCALATION", {"field": "autoMergeAuthorization"})
    require(source.get("mergeAuthorization") == "NOT_GRANTED", "DOWNSTREAM_AUTHORITY_ESCALATION", {"field": "mergeAuthorization"})
    require(source.get("directMainUpdate") is False, "DIRECT_MAIN_UPDATE_FORBIDDEN")
    require(source.get("repositoryAdministration") is False, "REPOSITORY_ADMINISTRATION_FORBIDDEN")
    require(source.get("consumerAdoption") is False, "CONSUMER_ADOPTION_FORBIDDEN")
    require(source["baseSha"] != source["headSha"], "BASE_HEAD_MUST_DIFFER")
    return changed_paths, verification_classes


def git(repo, args, *, check=True):
    cp = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, check=False)
    if check and cp.returncode != 0:
        raise ProviderDiffAttestationError("GIT_READBACK_FAILED", {
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


class GitHubReader:
    def __init__(self, token, api_url="https://api.github.com"):
        require(isinstance(token, str) and token, "GITHUB_TOKEN_REQUIRED")
        self.token = token
        self.api_url = api_url.rstrip("/")

    def request(self, path, accept):
        req = urllib.request.Request(self.api_url + path, method="GET")
        req.add_header("Accept", accept)
        req.add_header("Authorization", f"Bearer {self.token}")
        req.add_header("X-GitHub-Api-Version", "2022-11-28")
        try:
            with urllib.request.urlopen(req, timeout=30) as response:
                return response.read()
        except urllib.error.HTTPError as exc:
            raise ProviderDiffAttestationError("GITHUB_API_READ_ERROR", {
                "path": path,
                "status": exc.code,
                "body": exc.read().decode("utf-8", "replace")[-4000:],
            })
        except urllib.error.URLError as exc:
            raise ProviderDiffAttestationError("GITHUB_API_UNREACHABLE", {"path": path, "error": str(exc)})

    def json(self, path):
        raw = self.request(path, "application/vnd.github+json")
        try:
            return json.loads(raw.decode("utf-8")) if raw else None
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ProviderDiffAttestationError("GITHUB_JSON_INVALID", {"path": path, "error": str(exc)})

    def diff(self, repository, number):
        raw = self.request(f"/repos/{repository}/pulls/{number}", "application/vnd.github.v3.diff")
        require(raw, "PROVIDER_DIFF_EMPTY")
        return raw


def list_pr_files(client, repository, number):
    paths = []
    page = 1
    while True:
        items = client.json(f"/repos/{repository}/pulls/{number}/files?per_page=100&page={page}")
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


def verify(protocol, source, repo, token, api_url="https://api.github.com"):
    changed_paths, verification_classes = validate_source(protocol, source)
    repo = Path(repo).resolve()
    require(git(repo, ["rev-parse", "--is-inside-work-tree"], check=False).stdout.decode().strip() == "true", "REPOSITORY_NOT_GIT_WORKTREE")
    remote = git(repo, ["remote", "get-url", "origin"], check=False)
    require(remote.returncode == 0 and normalize_remote(remote.stdout.decode()) == source["repository"], "REPOSITORY_IDENTITY_MISMATCH")
    require(git(repo, ["status", "--porcelain", "--untracked-files=all"]).stdout.decode() == "", "LOCAL_WORKTREE_NOT_CLEAN")

    local = local_git_identity(repo, source["baseSha"], source["headSha"])
    require(local["changedPaths"] == changed_paths, "LOCAL_CHANGED_PATHS_MISMATCH", {"expected": changed_paths, "actual": local["changedPaths"]})
    require(local["localDiffSha256"] == source["diffSha256"], "UPSTREAM_DIFF_DIGEST_MISMATCH", {"expected": source["diffSha256"], "actual": local["localDiffSha256"]})

    client = GitHubReader(token, api_url)
    number = source["pullRequestNumber"]
    pr = client.json(f"/repos/{source['repository']}/pulls/{number}")
    require(isinstance(pr, dict) and pr.get("number") == number, "PULL_REQUEST_RESPONSE_INVALID")
    require(pr.get("state") == "open", "PULL_REQUEST_NOT_OPEN")
    require(pr.get("draft") is False, "DRAFT_PULL_REQUEST_FORBIDDEN")
    require(pr.get("merged") is False, "PULL_REQUEST_ALREADY_MERGED")
    base = pr.get("base") or {}
    head = pr.get("head") or {}
    require(base.get("ref") == source["baseBranch"] and base.get("sha") == source["baseSha"], "PULL_REQUEST_BASE_IDENTITY_MISMATCH")
    require(head.get("ref") == source["headBranch"] and head.get("sha") == source["headSha"], "PULL_REQUEST_HEAD_IDENTITY_MISMATCH")
    require((base.get("repo") or {}).get("full_name") == source["repository"], "PULL_REQUEST_BASE_REPOSITORY_MISMATCH")
    require((head.get("repo") or {}).get("full_name") == source["repository"], "PULL_REQUEST_HEAD_REPOSITORY_MISMATCH")
    actual_paths = list_pr_files(client, source["repository"], number)
    require(actual_paths == changed_paths, "PULL_REQUEST_CHANGED_PATHS_MISMATCH", {"expected": changed_paths, "actual": actual_paths})

    provider_diff = client.diff(source["repository"], number)
    try:
        provider = apply_provider_diff_to_base(repo, source["baseSha"], provider_diff)
    except DiffIdentityError as exc:
        raise ProviderDiffAttestationError(exc.reason, exc.detail)
    require(provider["providerAppliedTreeSha"] == local["headTreeSha"], "PROVIDER_LOCAL_DIFF_SEMANTIC_MISMATCH", {
        "providerAppliedTreeSha": provider["providerAppliedTreeSha"],
        "headTreeSha": local["headTreeSha"],
    })

    core = {
        "schemaVersion": 1,
        "protocolId": protocol["id"],
        "caseId": source["caseId"],
        "repository": source["repository"],
        "provider": "GITHUB",
        "sourcePullRequestAttestationId": source["pullRequestAttestationId"],
        "sourcePullRequestAttestationSha256": canonical_digest(source),
        "pullRequestNumber": number,
        "baseBranch": source["baseBranch"],
        "baseSha": source["baseSha"],
        "headBranch": source["headBranch"],
        "headSha": source["headSha"],
        "changedPaths": changed_paths,
        "verificationClasses": verification_classes,
        "upstreamDiffSha256": source["diffSha256"],
        "localDiffSha256": local["localDiffSha256"],
        "localDiffBytes": local["localDiffBytes"],
        "providerDiffSha256": provider["providerDiffSha256"],
        "providerDiffBytes": provider["providerDiffBytes"],
        "providerAppliedTreeSha": provider["providerAppliedTreeSha"],
        "headTreeSha": local["headTreeSha"],
        "providerSemanticEquality": True,
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
        "status": "PROVIDER_DIFF_ATTESTED",
        "decision": "PROVIDER_DIFF_RECONCILIATION_VERIFIED",
        "providerDiffAttestationId": canonical_digest(core),
    }


def parse_args():
    parser = argparse.ArgumentParser(description="CG-17 GitHub provider diff readback verifier")
    parser.add_argument("--protocol", required=True)
    parser.add_argument("--source-attestation", required=True)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--api-url", default=os.environ.get("GITHUB_API_URL", "https://api.github.com"))
    return parser.parse_args()


def main():
    args = parse_args()
    token = os.environ.get("GITHUB_TOKEN", "")
    try:
        result = verify(load(args.protocol), load(args.source_attestation), args.repo, token, args.api_url)
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    except ProviderDiffAttestationError as exc:
        out = {"schemaVersion": 1, "status": "REJECTED", "decision": "PROVIDER_DIFF_NOT_ATTESTED", "reason": exc.reason}
        if exc.detail is not None:
            out["detail"] = exc.detail
        print(json.dumps(out, indent=2, sort_keys=True))
        return 2


if __name__ == "__main__":
    sys.exit(main())
