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
    return {key: value for key, value in attestation.items() if key not in {"status", "decision", "publicationAttestationId"}}


def git(repo, args, *, check=True):
    cp = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, check=False)
    if check and cp.returncode != 0:
        raise PullRequestCreationError("GIT_COMMAND_FAILED", {
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
    require(protocol.get("id") == "CG16-CONTROLLED-PULL-REQUEST-CREATION", "WRONG_PROTOCOL")
    require(protocol.get("status") in {"CANDIDATE", "QUALIFIED"}, "UNEXPECTED_PROTOCOL_STATUS")
    require(isinstance(attestation, dict), "CG15_PUBLICATION_ATTESTATION_REQUIRED")
    rule = protocol["pullRequest"]
    require(attestation.get("protocolId") == rule["requiredSourceProtocol"], "CG15_PROTOCOL_MISMATCH")
    require(attestation.get("status") == rule["requiredSourceStatus"], "CG15_PUBLICATION_ATTESTATION_STATUS_INVALID")
    require(attestation.get("decision") == rule["requiredSourceDecision"], "CG15_PUBLICATION_ATTESTATION_DECISION_INVALID")

    for field in ("caseId", "repository", "baseSha", "workBranch", "remoteBranchSha", "commitSha", "treeSha", "diffSha256", "publicationAttestationId"):
        require(isinstance(attestation.get(field), str) and attestation[field], "CG15_PUBLICATION_ATTESTATION_FIELD_REQUIRED", {"field": field})
    require(re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", attestation["repository"]) is not None, "GITHUB_REPOSITORY_SLUG_REQUIRED")
    for field in ("baseSha", "remoteBranchSha", "commitSha", "treeSha"):
        require(re.fullmatch(r"[0-9a-f]{40}", attestation[field]) is not None, "CG15_GIT_ID_INVALID", {"field": field})
    for field in ("diffSha256", "publicationAttestationId"):
        require(re.fullmatch(r"[0-9a-f]{64}", attestation[field]) is not None, "CG15_DIGEST_INVALID", {"field": field})
    require(canonical_digest(source_core(attestation)) == attestation["publicationAttestationId"], "CG15_PUBLICATION_ATTESTATION_ID_MISMATCH")

    require(attestation.get("baseBranch") == rule["baseBranch"], "BASE_BRANCH_MISMATCH")
    require(attestation["workBranch"] != rule["baseBranch"], "HEAD_BRANCH_MUST_NOT_BE_BASE_BRANCH")
    require(attestation["remoteBranchSha"] == attestation["commitSha"], "CG15_REMOTE_COMMIT_BINDING_INVALID")
    require(attestation["baseSha"] != attestation["commitSha"], "BASE_HEAD_MUST_DIFFER")
    require(attestation.get("pullRequestAuthorization") == "NOT_GRANTED", "CG15_SOURCE_AUTHORITY_INVALID", {"field": "pullRequestAuthorization"})
    require(attestation.get("mergeAuthorization") == "NOT_GRANTED", "CG15_SOURCE_AUTHORITY_INVALID", {"field": "mergeAuthorization"})
    require(attestation.get("directMainUpdate") is False, "CG15_SOURCE_AUTHORITY_INVALID", {"field": "directMainUpdate"})
    require(attestation.get("repositoryAdministration") is False, "CG15_SOURCE_AUTHORITY_INVALID", {"field": "repositoryAdministration"})
    require(attestation.get("consumerAdoption") is False, "CG15_SOURCE_AUTHORITY_INVALID", {"field": "consumerAdoption"})

    changed_paths = sorted_unique_strings(attestation.get("changedPaths"), "CG15_CHANGED_PATHS_INVALID")
    verification_classes = sorted_unique_strings(attestation.get("verificationClasses"), "CG15_VERIFICATION_CLASSES_INVALID")
    require(bool(changed_paths), "CG15_CHANGED_PATHS_EMPTY")
    require(bool(verification_classes), "CG15_VERIFICATION_CLASSES_EMPTY")
    return changed_paths, verification_classes


def validate_title(title):
    require(isinstance(title, str), "PULL_REQUEST_TITLE_INVALID")
    require(title and title.strip() == title, "PULL_REQUEST_TITLE_INVALID")
    require("\n" not in title and "\r" not in title, "PULL_REQUEST_TITLE_INVALID")
    require(len(title.encode("utf-8")) <= 200, "PULL_REQUEST_TITLE_TOO_LONG")
    return title


def validate_body(body):
    require(isinstance(body, str), "PULL_REQUEST_BODY_INVALID")
    require(len(body.encode("utf-8")) <= 8000, "PULL_REQUEST_BODY_TOO_LONG")
    return body


def remote_ref(repo, remote_name, ref):
    cp = git(repo, ["ls-remote", "--refs", remote_name, ref], check=False)
    require(cp.returncode == 0, "REMOTE_READ_FAILED", {"ref": ref, "stderr": cp.stderr.decode("utf-8", "replace")[-4000:]})
    lines = [line for line in cp.stdout.decode("utf-8", "replace").splitlines() if line.strip()]
    require(len(lines) <= 1, "REMOTE_REF_AMBIGUOUS", {"ref": ref, "lines": lines})
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

    def request(self, method, path, payload=None):
        url = self.api_url + path
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Accept", "application/vnd.github+json")
        req.add_header("Authorization", f"Bearer {self.token}")
        req.add_header("X-GitHub-Api-Version", "2022-11-28")
        if data is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=30) as response:
                raw = response.read()
                return json.loads(raw.decode("utf-8")) if raw else None
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", "replace")[-4000:]
            raise PullRequestCreationError("GITHUB_API_ERROR", {"method": method, "path": path, "status": exc.code, "body": raw})
        except urllib.error.URLError as exc:
            raise PullRequestCreationError("GITHUB_API_UNREACHABLE", {"method": method, "path": path, "error": str(exc)})

    def get(self, path):
        return self.request("GET", path)

    def post(self, path, payload):
        return self.request("POST", path, payload)


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


def validate_live_pr(pr, source, title, body, changed_paths):
    require(isinstance(pr, dict), "PULL_REQUEST_RESPONSE_INVALID")
    require(pr.get("state") == "open", "PULL_REQUEST_NOT_OPEN")
    require(pr.get("draft") is False, "DRAFT_PULL_REQUEST_FORBIDDEN")
    require(pr.get("merged") is False, "PULL_REQUEST_ALREADY_MERGED")
    require(pr.get("maintainer_can_modify") is False, "MAINTAINER_MUTATION_FORBIDDEN")
    require(pr.get("auto_merge") is None, "AUTO_MERGE_FORBIDDEN")
    require(pr.get("title") == title, "PULL_REQUEST_TITLE_MISMATCH")
    require((pr.get("body") or "") == body, "PULL_REQUEST_BODY_MISMATCH")
    base = pr.get("base") or {}
    head = pr.get("head") or {}
    require(base.get("ref") == source["baseBranch"], "PULL_REQUEST_BASE_REF_MISMATCH")
    require(base.get("sha") == source["baseSha"], "PULL_REQUEST_BASE_SHA_MISMATCH", {"expected": source["baseSha"], "actual": base.get("sha")})
    require(head.get("ref") == source["workBranch"], "PULL_REQUEST_HEAD_REF_MISMATCH")
    require(head.get("sha") == source["commitSha"], "PULL_REQUEST_HEAD_SHA_MISMATCH", {"expected": source["commitSha"], "actual": head.get("sha")})
    require((base.get("repo") or {}).get("full_name") == source["repository"], "PULL_REQUEST_BASE_REPOSITORY_MISMATCH")
    require((head.get("repo") or {}).get("full_name") == source["repository"], "PULL_REQUEST_HEAD_REPOSITORY_MISMATCH")
    require(pr.get("commits") == 1, "PULL_REQUEST_COMMIT_COUNT_MISMATCH", {"actual": pr.get("commits")})
    require(changed_paths, "PULL_REQUEST_CHANGED_PATHS_EMPTY")


def create(protocol, source_attestation, repo, title, body, token, api_url="https://api.github.com"):
    changed_paths, verification_classes = validate_source_attestation(protocol, source_attestation)
    title = validate_title(title)
    body = validate_body(body)
    repo = Path(repo).resolve()
    rule = protocol["pullRequest"]

    inside = git(repo, ["rev-parse", "--is-inside-work-tree"], check=False)
    require(inside.returncode == 0 and inside.stdout.decode().strip() == "true", "REPOSITORY_NOT_GIT_WORKTREE")
    remote_url = git(repo, ["remote", "get-url", "origin"], check=False)
    require(remote_url.returncode == 0, "ORIGIN_REMOTE_REQUIRED")
    actual_repository = normalize_remote(remote_url.stdout.decode())
    require(actual_repository == source_attestation["repository"], "REPOSITORY_IDENTITY_MISMATCH", {"expected": source_attestation["repository"], "actual": actual_repository})
    branch = git(repo, ["branch", "--show-current"]).stdout.decode().strip()
    require(branch == source_attestation["workBranch"], "WORK_BRANCH_MISMATCH", {"expected": source_attestation["workBranch"], "actual": branch})
    head = git(repo, ["rev-parse", "HEAD"]).stdout.decode().strip()
    require(head == source_attestation["commitSha"], "LOCAL_HEAD_COMMIT_MISMATCH", {"expected": source_attestation["commitSha"], "actual": head})
    status = git(repo, ["status", "--porcelain", "--untracked-files=all"]).stdout.decode()
    require(status == "", "LOCAL_WORKTREE_NOT_CLEAN", {"status": status})

    base_ref = f"refs/heads/{source_attestation['baseBranch']}"
    head_ref = f"refs/heads/{source_attestation['workBranch']}"
    remote_base = remote_ref(repo, "origin", base_ref)
    remote_head = remote_ref(repo, "origin", head_ref)
    require(remote_base == source_attestation["baseSha"], "REMOTE_BASE_SHA_MISMATCH", {"expected": source_attestation["baseSha"], "actual": remote_base})
    require(remote_head == source_attestation["commitSha"], "REMOTE_HEAD_SHA_MISMATCH", {"expected": source_attestation["commitSha"], "actual": remote_head})

    owner = source_attestation["repository"].split("/", 1)[0]
    client = GitHubClient(token, api_url)
    query = urllib.parse.urlencode({"state": "open", "head": f"{owner}:{source_attestation['workBranch']}", "base": source_attestation["baseBranch"], "per_page": 100})
    existing = client.get(f"/repos/{source_attestation['repository']}/pulls?{query}")
    require(isinstance(existing, list), "OPEN_PULL_REQUEST_QUERY_INVALID")
    require(len(existing) == 0, "OPEN_PULL_REQUEST_ALREADY_EXISTS", {"numbers": [item.get("number") for item in existing if isinstance(item, dict)]})

    created = client.post(f"/repos/{source_attestation['repository']}/pulls", {
        "title": title,
        "head": source_attestation["workBranch"],
        "base": source_attestation["baseBranch"],
        "body": body,
        "draft": False,
        "maintainer_can_modify": False,
    })
    number = created.get("number") if isinstance(created, dict) else None
    require(isinstance(number, int) and number > 0, "PULL_REQUEST_NUMBER_INVALID")
    live = client.get(f"/repos/{source_attestation['repository']}/pulls/{number}")
    live_paths = list_pr_files(client, source_attestation["repository"], number)
    validate_live_pr(live, source_attestation, title, body, live_paths)
    require(live_paths == changed_paths, "PULL_REQUEST_CHANGED_PATHS_MISMATCH", {"expected": changed_paths, "actual": live_paths})

    return {
        "schemaVersion": 1,
        "protocolId": protocol["id"],
        "status": "PULL_REQUEST_CREATED_UNVERIFIED",
        "caseId": source_attestation["caseId"],
        "repository": source_attestation["repository"],
        "provider": rule["provider"],
        "sourcePublicationAttestationId": source_attestation["publicationAttestationId"],
        "sourcePublicationAttestationSha256": canonical_digest(source_attestation),
        "pullRequestNumber": number,
        "pullRequestNodeId": live.get("node_id"),
        "pullRequestUrl": live.get("html_url"),
        "baseBranch": source_attestation["baseBranch"],
        "baseSha": source_attestation["baseSha"],
        "headBranch": source_attestation["workBranch"],
        "headSha": source_attestation["commitSha"],
        "changedPaths": changed_paths,
        "diffSha256": source_attestation["diffSha256"],
        "verificationClasses": verification_classes,
        "title": title,
        "bodySha256": hashlib.sha256(body.encode("utf-8")).hexdigest(),
        "commitCount": 1,
        "draft": False,
        "maintainerCanModify": False,
        "autoMerge": False,
        "pullRequestAuthority": "SINGLE_OPEN_PR_CREATE_ONLY",
        "pullRequestUpdateAuthorization": "NOT_GRANTED",
        "reviewSubmissionAuthorization": "NOT_GRANTED",
        "autoMergeAuthorization": "NOT_GRANTED",
        "mergeAuthorization": "NOT_GRANTED",
        "directMainUpdate": False,
        "repositoryAdministration": False,
        "consumerAdoption": False,
    }


def parse_args():
    parser = argparse.ArgumentParser(description="CG-16 controlled GitHub pull request creator")
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
        result = create(load(args.protocol), load(args.source_attestation), args.repo, args.title, args.body, token, args.api_url)
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    except PullRequestCreationError as exc:
        out = {"schemaVersion": 1, "status": "REJECTED", "decision": "PULL_REQUEST_NOT_CREATED", "reason": exc.reason}
        if exc.detail is not None:
            out["detail"] = exc.detail
        print(json.dumps(out, indent=2, sort_keys=True))
        return 2


if __name__ == "__main__":
    sys.exit(main())
