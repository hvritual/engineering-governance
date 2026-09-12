#!/usr/bin/env python3
import argparse
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path


class MergeExecutionPreparationError(Exception):
    def __init__(self, reason, detail=None):
        super().__init__(reason)
        self.reason = reason
        self.detail = detail


def require(condition, reason, detail=None):
    if not condition:
        raise MergeExecutionPreparationError(reason, detail)


def load(path):
    return json.loads(Path(path).read_text())


def canonical_digest(value):
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def authorization_core(attestation):
    return {k: v for k, v in attestation.items() if k not in {"status", "decision", "authorizationId"}}


def sorted_unique_strings(value, reason):
    require(isinstance(value, list), reason)
    require(all(isinstance(item, str) and item for item in value), reason)
    require(len(value) == len(set(value)), reason)
    return sorted(value)


def validate_source(protocol, source):
    require(protocol.get("schemaVersion") == 1, "PROTOCOL_SCHEMA_MISMATCH")
    require(protocol.get("id") == "CG20-CONTROLLED-MERGE-EXECUTION", "WRONG_PROTOCOL")
    require(protocol.get("status") in {"CANDIDATE", "QUALIFIED"}, "UNEXPECTED_PROTOCOL_STATUS")
    require(isinstance(source, dict), "CG19_MERGE_AUTHORIZATION_REQUIRED")
    require(source.get("protocolId") == protocol.get("sourceProtocol"), "CG19_PROTOCOL_MISMATCH")
    require(source.get("status") == protocol.get("requiredSourceStatus"), "CG19_AUTHORIZATION_STATUS_INVALID")
    require(source.get("decision") == protocol.get("requiredSourceDecision"), "CG19_AUTHORIZATION_DECISION_INVALID")
    require(canonical_digest(authorization_core(source)) == source.get("authorizationId"), "CG19_AUTHORIZATION_ID_MISMATCH")

    policy = protocol["executionPolicy"]
    require(source.get("provider") == policy["provider"], "PROVIDER_MISMATCH")
    require(re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", source.get("repository", "")) is not None, "GITHUB_REPOSITORY_SLUG_REQUIRED")
    require(isinstance(source.get("pullRequestNumber"), int) and source["pullRequestNumber"] > 0, "PULL_REQUEST_NUMBER_INVALID")
    for field in ("baseSha", "headSha", "expectedHeadSha"):
        require(re.fullmatch(r"[0-9a-f]{40}", source.get(field, "")) is not None, "CG19_GIT_ID_INVALID", {"field": field})
    require(source.get("expectedHeadSha") == source.get("headSha"), "EXPECTED_HEAD_SHA_MISMATCH")
    require(isinstance(source.get("baseBranch"), str) and source["baseBranch"], "BASE_BRANCH_REQUIRED")
    require(isinstance(source.get("headBranch"), str) and source["headBranch"], "HEAD_BRANCH_REQUIRED")
    require(source["baseSha"] != source["headSha"], "BASE_HEAD_MUST_DIFFER")
    changed_paths = sorted_unique_strings(source.get("changedPaths"), "CG19_CHANGED_PATHS_INVALID")
    verification_classes = sorted_unique_strings(source.get("verificationClasses"), "CG19_VERIFICATION_CLASSES_INVALID")
    require(changed_paths, "CG19_CHANGED_PATHS_EMPTY")
    require(verification_classes, "CG19_VERIFICATION_CLASSES_EMPTY")

    require(source.get("mergeAuthorization") == policy["requiredGrant"], "MERGE_AUTHORIZATION_NOT_GRANTED_ONCE")
    require(source.get("authorizationUseLimit") == policy["requiredUseLimit"], "AUTHORIZATION_USE_LIMIT_INVALID")
    require(source.get("consumptionState") == policy["requiredSourceConsumptionState"], "AUTHORIZATION_ALREADY_CONSUMED")
    require(source.get("mergeMethod") in policy["allowedMergeMethods"], "MERGE_METHOD_NOT_ALLOWED")
    if policy.get("requireSourceExecutesMergeFalse"):
        require(source.get("executesMerge") is False, "SOURCE_EXECUTION_AUTHORITY_INVALID")
    require(source.get("autoMergeAuthorization") == "NOT_GRANTED", "AUTO_MERGE_FORBIDDEN")
    require(source.get("pullRequestUpdateAuthorization") == "NOT_GRANTED", "PULL_REQUEST_UPDATE_FORBIDDEN")
    require(source.get("reviewSubmissionAuthorization") == "NOT_GRANTED", "REVIEW_SUBMISSION_FORBIDDEN")
    require(source.get("directMainUpdate") is False, "DIRECT_MAIN_UPDATE_FORBIDDEN")
    require(source.get("repositoryAdministration") is False, "REPOSITORY_ADMINISTRATION_FORBIDDEN")
    require(source.get("consumerAdoption") is False, "CONSUMER_ADOPTION_FORBIDDEN")
    require(source.get("repositoryBlocking") is False, "REPOSITORY_BLOCKING_FORBIDDEN")
    return changed_paths, verification_classes


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
            raise MergeExecutionPreparationError("GITHUB_API_READ_ERROR", {
                "path": path,
                "status": exc.code,
                "body": exc.read().decode("utf-8", "replace")[-4000:],
            })
        except urllib.error.URLError as exc:
            raise MergeExecutionPreparationError("GITHUB_API_UNREACHABLE", {"path": path, "error": str(exc)})


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


def live_preflight(protocol, source, token, api_url="https://api.github.com"):
    changed_paths, _ = validate_source(protocol, source)
    client = GitHubReader(token, api_url)
    repository = source["repository"]
    number = source["pullRequestNumber"]
    pr = None
    for _ in range(6):
        pr = client.get(f"/repos/{repository}/pulls/{number}")
        require(isinstance(pr, dict) and pr.get("number") == number, "PULL_REQUEST_RESPONSE_INVALID")
        if pr.get("merged") is True:
            raise MergeExecutionPreparationError("PULL_REQUEST_ALREADY_MERGED")
        if pr.get("mergeable") is not None:
            break
        time.sleep(1)
    require(pr.get("mergeable") is not None, "PULL_REQUEST_MERGEABILITY_UNKNOWN")
    require(pr.get("state") == "open", "PULL_REQUEST_NOT_OPEN")
    require(pr.get("draft") is False, "DRAFT_PULL_REQUEST_FORBIDDEN")
    if protocol["executionPolicy"].get("requireMergeableTrue"):
        require(pr.get("mergeable") is True, "PULL_REQUEST_NOT_MERGEABLE")

    base = pr.get("base") or {}
    head = pr.get("head") or {}
    require((base.get("repo") or {}).get("full_name") == repository, "LIVE_BASE_REPOSITORY_MISMATCH")
    require((head.get("repo") or {}).get("full_name") == repository, "LIVE_HEAD_REPOSITORY_MISMATCH")
    require(base.get("ref") == source["baseBranch"] and base.get("sha") == source["baseSha"], "LIVE_BASE_IDENTITY_MISMATCH", {"actualRef": base.get("ref"), "actualSha": base.get("sha")})
    require(head.get("ref") == source["headBranch"] and head.get("sha") == source["headSha"], "LIVE_HEAD_IDENTITY_MISMATCH", {"actualRef": head.get("ref"), "actualSha": head.get("sha")})
    actual_paths = list_pr_files(client, repository, number)
    require(actual_paths == changed_paths, "LIVE_CHANGED_PATHS_MISMATCH", {"expected": changed_paths, "actual": actual_paths})
    return {
        "repository": repository,
        "pullRequestNumber": number,
        "state": pr.get("state"),
        "draft": pr.get("draft"),
        "merged": pr.get("merged"),
        "mergeable": pr.get("mergeable"),
        "baseBranch": base.get("ref"),
        "baseSha": base.get("sha"),
        "headBranch": head.get("ref"),
        "headSha": head.get("sha"),
        "changedPaths": actual_paths,
    }


def prepare(protocol, source, token, api_url="https://api.github.com"):
    changed_paths, verification_classes = validate_source(protocol, source)
    snapshot = live_preflight(protocol, source, token, api_url)
    policy = protocol["executionPolicy"]
    core = {
        "schemaVersion": 1,
        "protocolId": protocol["id"],
        "caseId": source["caseId"],
        "repository": source["repository"],
        "provider": policy["provider"],
        "externalExecutor": policy["externalExecutor"],
        "operation": policy["operation"],
        "sourceAuthorizationId": source["authorizationId"],
        "sourceAuthorizationSha256": canonical_digest(source),
        "livePreflightSha256": canonical_digest(snapshot),
        "pullRequestNumber": source["pullRequestNumber"],
        "baseBranch": source["baseBranch"],
        "baseSha": source["baseSha"],
        "headBranch": source["headBranch"],
        "headSha": source["headSha"],
        "expectedHeadSha": source["expectedHeadSha"],
        "changedPaths": changed_paths,
        "verificationClasses": verification_classes,
        "mergeMethod": source["mergeMethod"],
        "mergeAuthorization": source["mergeAuthorization"],
        "authorizationUseLimit": source["authorizationUseLimit"],
        "sourceConsumptionState": source["consumptionState"],
        "providerCompareAndSwap": policy["providerCompareAndSwap"],
        "providerSingleUseBoundary": policy["providerSingleUseBoundary"],
        "autoMerge": False,
        "directMainUpdate": False,
        "repositoryAdministration": False,
        "consumerAdoption": False,
    }
    return {
        **core,
        "status": protocol["outputs"]["requestStatus"],
        "decision": protocol["outputs"]["requestDecision"],
        "requestId": canonical_digest(core),
    }


def parse_args():
    parser = argparse.ArgumentParser(description="CG-20 controlled merge execution request preparation")
    parser.add_argument("--protocol", required=True)
    parser.add_argument("--source-authorization", required=True)
    parser.add_argument("--api-url", default=os.environ.get("GITHUB_API_URL", "https://api.github.com"))
    return parser.parse_args()


def main():
    args = parse_args()
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or ""
    try:
        out = prepare(load(args.protocol), load(args.source_authorization), token, args.api_url)
        print(json.dumps(out, indent=2, sort_keys=True))
        return 0
    except MergeExecutionPreparationError as exc:
        out = {"schemaVersion": 1, "status": "REJECTED", "decision": "MERGE_EXECUTION_REQUEST_NOT_READY", "reason": exc.reason}
        if exc.detail is not None:
            out["detail"] = exc.detail
        print(json.dumps(out, indent=2, sort_keys=True))
        return 2


if __name__ == "__main__":
    sys.exit(main())
