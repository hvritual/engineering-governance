#!/usr/bin/env python3
import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


class PullRequestCreationError(Exception):
    def __init__(self, reason, detail=None):
        super().__init__(reason)
        self.reason = reason
        self.detail = detail


def require(condition, reason, detail=None):
    if not condition:
        raise PullRequestCreationError(reason, detail)


def load(path):
    return json.loads(Path(path).read_text())


def canonical_digest(value):
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def source_core(attestation):
    return {k: v for k, v in attestation.items() if k not in {"status", "decision", "publicationAttestationId"}}


def git(repo, args, check=True):
    cp = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, check=False)
    if check and cp.returncode != 0:
        raise PullRequestCreationError("GIT_COMMAND_FAILED", {"args": args, "stderr": cp.stderr.decode("utf-8", "replace")[-4000:]})
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
    require(protocol.get("id") == "CG16-CONTROLLED-PULL-REQUEST-CREATION", "WRONG_PROTOCOL")
    require(protocol.get("status") in {"CANDIDATE", "QUALIFIED"}, "UNEXPECTED_PROTOCOL_STATUS")
    require(isinstance(attestation, dict), "CG15_PUBLICATION_ATTESTATION_REQUIRED")
    rule = protocol["pullRequest"]
    require(attestation.get("protocolId") == rule["requiredSourceProtocol"], "CG15_PROTOCOL_MISMATCH")
    require(attestation.get("status") == rule["requiredSourceStatus"], "CG15_PUBLICATION_ATTESTATION_STATUS_INVALID")
    require(attestation.get("decision") == rule["requiredSourceDecision"], "CG15_PUBLICATION_ATTESTATION_DECISION_INVALID")
    require(re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", attestation.get("repository", "")) is not None, "GITHUB_REPOSITORY_SLUG_REQUIRED")
    for field in ("baseSha", "remoteBranchSha", "commitSha", "treeSha"):
        require(re.fullmatch(r"[0-9a-f]{40}", attestation.get(field, "")) is not None, "CG15_GIT_ID_INVALID", {"field": field})
    for field in ("diffSha256", "publicationAttestationId"):
        require(re.fullmatch(r"[0-9a-f]{64}", attestation.get(field, "")) is not None, "CG15_DIGEST_INVALID", {"field": field})
    require(canonical_digest(source_core(attestation)) == attestation["publicationAttestationId"], "CG15_PUBLICATION_ATTESTATION_ID_MISMATCH")
    require(attestation.get("baseBranch") == rule["baseBranch"], "BASE_BRANCH_MISMATCH")
    require(attestation.get("workBranch") != rule["baseBranch"], "HEAD_BRANCH_MUST_NOT_BE_BASE_BRANCH")
    require(attestation.get("remoteBranchSha") == attestation.get("commitSha"), "CG15_REMOTE_COMMIT_BINDING_INVALID")
    require(attestation.get("baseSha") != attestation.get("commitSha"), "BASE_HEAD_MUST_DIFFER")
    require(attestation.get("pullRequestAuthorization") == "NOT_GRANTED", "CG15_SOURCE_AUTHORITY_INVALID", {"field": "pullRequestAuthorization"})
    require(attestation.get("mergeAuthorization") == "NOT_GRANTED", "CG15_SOURCE_AUTHORITY_INVALID", {"field": "mergeAuthorization"})
    require(attestation.get("directMainUpdate") is False, "CG15_SOURCE_AUTHORITY_INVALID", {"field": "directMainUpdate"})
    require(attestation.get("repositoryAdministration") is False, "CG15_SOURCE_AUTHORITY_INVALID", {"field": "repositoryAdministration"})
    require(attestation.get("consumerAdoption") is False, "CG15_SOURCE_AUTHORITY_INVALID", {"field": "consumerAdoption"})
    changed_paths = sorted_unique_strings(attestation.get("changedPaths"), "CG15_CHANGED_PATHS_INVALID")
    verification_classes = sorted_unique_strings(attestation.get("verificationClasses"), "CG15_VERIFICATION_CLASSES_INVALID")
    require(changed_paths, "CG15_CHANGED_PATHS_EMPTY")
    require(verification_classes, "CG15_VERIFICATION_CLASSES_EMPTY")
    return changed_paths, verification_classes


def validate_title(title):
    require(isinstance(title, str) and title and title.strip() == title, "PULL_REQUEST_TITLE_INVALID")
    require("\n" not in title and "\r" not in title, "PULL_REQUEST_TITLE_INVALID")
    require(len(title.encode("utf-8")) <= 200, "PULL_REQUEST_TITLE_TOO_LONG")
    return title


def validate_body(body):
    require(isinstance(body, str), "PULL_REQUEST_BODY_INVALID")
    require(len(body.encode("utf-8")) <= 8000, "PULL_REQUEST_BODY_TOO_LONG")
    return body


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
            raise PullRequestCreationError("GITHUB_API_READ_ERROR", {"path": path, "status": exc.code, "body": exc.read().decode("utf-8", "replace")[-4000:]})
        except urllib.error.URLError as exc:
            raise PullRequestCreationError("GITHUB_API_UNREACHABLE", {"path": path, "error": str(exc)})


def build_request(protocol, source_attestation, repo, title, body, token, api_url="https://api.github.com"):
    changed_paths, verification_classes = validate_source_attestation(protocol, source_attestation)
    title = validate_title(title)
    body = validate_body(body)
    repo = Path(repo).resolve()
    rule = protocol["pullRequest"]

    inside = git(repo, ["rev-parse", "--is-inside-work-tree"], check=False)
    require(inside.returncode == 0 and inside.stdout.decode().strip() == "true", "REPOSITORY_NOT_GIT_WORKTREE")
    remote = git(repo, ["remote", "get-url", "origin"], check=False)
    require(remote.returncode == 0, "ORIGIN_REMOTE_REQUIRED")
    require(normalize_remote(remote.stdout.decode()) == source_attestation["repository"], "REPOSITORY_IDENTITY_MISMATCH")
    require(git(repo, ["branch", "--show-current"]).stdout.decode().strip() == source_attestation["workBranch"], "WORK_BRANCH_MISMATCH")
    require(git(repo, ["rev-parse", "HEAD"]).stdout.decode().strip() == source_attestation["commitSha"], "LOCAL_HEAD_COMMIT_MISMATCH")
    require(git(repo, ["status", "--porcelain", "--untracked-files=all"]).stdout.decode() == "", "LOCAL_WORKTREE_NOT_CLEAN")
    require(remote_ref(repo, f"refs/heads/{source_attestation['baseBranch']}") == source_attestation["baseSha"], "REMOTE_BASE_SHA_MISMATCH")
    require(remote_ref(repo, f"refs/heads/{source_attestation['workBranch']}") == source_attestation["commitSha"], "REMOTE_HEAD_SHA_MISMATCH")

    owner = source_attestation["repository"].split("/", 1)[0]
    query = urllib.parse.urlencode({"state": "open", "head": f"{owner}:{source_attestation['workBranch']}", "base": source_attestation["baseBranch"], "per_page": 100})
    existing = GitHubReader(token, api_url).get(f"/repos/{source_attestation['repository']}/pulls?{query}")
    require(isinstance(existing, list), "OPEN_PULL_REQUEST_QUERY_INVALID")
    require(len(existing) == 0, "OPEN_PULL_REQUEST_ALREADY_EXISTS", {"numbers": [x.get("number") for x in existing if isinstance(x, dict)]})

    core = {
        "schemaVersion": 1,
        "protocolId": protocol["id"],
        "caseId": source_attestation["caseId"],
        "provider": rule["provider"],
        "externalExecutor": rule["externalExecutor"],
        "operation": "CREATE_PULL_REQUEST",
        "repository": source_attestation["repository"],
        "sourcePublicationAttestationId": source_attestation["publicationAttestationId"],
        "sourcePublicationAttestationSha256": canonical_digest(source_attestation),
        "baseBranch": source_attestation["baseBranch"],
        "baseSha": source_attestation["baseSha"],
        "headBranch": source_attestation["workBranch"],
        "headSha": source_attestation["commitSha"],
        "changedPaths": changed_paths,
        "diffSha256": source_attestation["diffSha256"],
        "verificationClasses": verification_classes,
        "title": title,
        "body": body,
        "draft": False,
        "maintainerCanModify": False,
        "requestAuthority": "SINGLE_OPEN_PR_CREATE_REQUEST_ONLY",
        "pullRequestUpdateAuthorization": "NOT_GRANTED",
        "reviewSubmissionAuthorization": "NOT_GRANTED",
        "autoMergeAuthorization": "NOT_GRANTED",
        "mergeAuthorization": "NOT_GRANTED",
        "directMainUpdate": False,
        "repositoryAdministration": False,
        "consumerAdoption": False
    }
    return {**core, "status": "PULL_REQUEST_CREATION_REQUEST_READY", "decision": "CONTROLLED_PULL_REQUEST_REQUEST_VERIFIED", "requestId": canonical_digest(core)}


def parse_args():
    parser = argparse.ArgumentParser(description="CG-16 side-effect-free pull request creation request builder")
    parser.add_argument("--protocol", required=True)
    parser.add_argument("--source-attestation", required=True)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--title", required=True)
    parser.add_argument("--body", default="")
    parser.add_argument("--api-url", default=os.environ.get("GITHUB_API_URL", "https://api.github.com"))
    return parser.parse_args()


def main():
    args = parse_args()
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    try:
        result = build_request(load(args.protocol), load(args.source_attestation), args.repo, args.title, args.body, token, args.api_url)
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    except PullRequestCreationError as exc:
        out = {"schemaVersion": 1, "status": "REJECTED", "decision": "PULL_REQUEST_REQUEST_NOT_READY", "reason": exc.reason}
        if exc.detail is not None:
            out["detail"] = exc.detail
        print(json.dumps(out, indent=2, sort_keys=True))
        return 2


if __name__ == "__main__":
    sys.exit(main())
