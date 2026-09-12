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

from review_state import canonical_digest, check_summary, effective_review_summary, qualify_snapshot


class PullRequestQualificationError(Exception):
    def __init__(self, reason, detail=None):
        super().__init__(reason)
        self.reason = reason
        self.detail = detail


def require(condition, reason, detail=None):
    if not condition:
        raise PullRequestQualificationError(reason, detail)


def load(path):
    return json.loads(Path(path).read_text())


def source_core(attestation):
    return {k: v for k, v in attestation.items() if k not in {"status", "decision", "providerDiffAttestationId"}}


def sorted_unique_strings(value, reason):
    require(isinstance(value, list), reason)
    require(all(isinstance(item, str) and item for item in value), reason)
    require(len(value) == len(set(value)), reason)
    return sorted(value)


def validate_source(protocol, attestation):
    require(protocol.get("schemaVersion") == 1, "PROTOCOL_SCHEMA_MISMATCH")
    require(protocol.get("id") == "CG18-PULL-REQUEST-QUALIFICATION-DECISION", "WRONG_PROTOCOL")
    require(protocol.get("status") in {"CANDIDATE", "QUALIFIED"}, "UNEXPECTED_PROTOCOL_STATUS")
    require(isinstance(attestation, dict), "CG17_PROVIDER_DIFF_ATTESTATION_REQUIRED")
    require(attestation.get("protocolId") == protocol.get("sourceProtocol"), "CG17_PROTOCOL_MISMATCH")
    require(attestation.get("status") == protocol.get("requiredSourceStatus"), "CG17_PROVIDER_DIFF_STATUS_INVALID")
    require(attestation.get("decision") == protocol.get("requiredSourceDecision"), "CG17_PROVIDER_DIFF_DECISION_INVALID")
    require(canonical_digest(source_core(attestation)) == attestation.get("providerDiffAttestationId"), "CG17_PROVIDER_DIFF_ATTESTATION_ID_MISMATCH")

    require(re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", attestation.get("repository", "")) is not None, "GITHUB_REPOSITORY_SLUG_REQUIRED")
    require(isinstance(attestation.get("pullRequestNumber"), int) and attestation["pullRequestNumber"] > 0, "PULL_REQUEST_NUMBER_INVALID")
    for field in ("baseSha", "headSha", "providerAppliedTreeSha", "headTreeSha"):
        require(re.fullmatch(r"[0-9a-f]{40}", attestation.get(field, "")) is not None, "CG17_GIT_ID_INVALID", {"field": field})
    for field in ("upstreamDiffSha256", "localDiffSha256", "providerDiffSha256", "providerDiffAttestationId"):
        require(re.fullmatch(r"[0-9a-f]{64}", attestation.get(field, "")) is not None, "CG17_DIGEST_INVALID", {"field": field})

    changed_paths = sorted_unique_strings(attestation.get("changedPaths"), "CG17_CHANGED_PATHS_INVALID")
    verification_classes = sorted_unique_strings(attestation.get("verificationClasses"), "CG17_VERIFICATION_CLASSES_INVALID")
    require(bool(changed_paths), "CG17_CHANGED_PATHS_EMPTY")
    require(bool(verification_classes), "CG17_VERIFICATION_CLASSES_EMPTY")
    require(attestation.get("provider") == "GITHUB", "PROVIDER_MISMATCH")
    require(attestation.get("providerSemanticEquality") is True, "PROVIDER_SEMANTIC_EQUALITY_REQUIRED")
    require(attestation.get("providerAppliedTreeSha") == attestation.get("headTreeSha"), "PROVIDER_TREE_IDENTITY_MISMATCH")
    require(attestation.get("localDiffSha256") == attestation.get("upstreamDiffSha256"), "UPSTREAM_LOCAL_DIFF_IDENTITY_MISMATCH")
    for field in ("pullRequestUpdateAuthorization", "reviewSubmissionAuthorization", "autoMergeAuthorization", "mergeAuthorization"):
        require(attestation.get(field) == "NOT_GRANTED", "DOWNSTREAM_AUTHORITY_ESCALATION", {"field": field})
    require(attestation.get("directMainUpdate") is False, "DOWNSTREAM_AUTHORITY_ESCALATION", {"field": "directMainUpdate"})
    require(attestation.get("repositoryAdministration") is False, "DOWNSTREAM_AUTHORITY_ESCALATION", {"field": "repositoryAdministration"})
    require(attestation.get("consumerAdoption") is False, "DOWNSTREAM_AUTHORITY_ESCALATION", {"field": "consumerAdoption"})
    return changed_paths, verification_classes


class GitHubReader:
    def __init__(self, token, api_url="https://api.github.com"):
        require(isinstance(token, str) and token, "GITHUB_TOKEN_REQUIRED")
        self.token = token
        self.api_url = api_url.rstrip("/")

    def request(self, method, url, payload=None, accept="application/vnd.github+json"):
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Accept", accept)
        req.add_header("Authorization", f"Bearer {self.token}")
        req.add_header("X-GitHub-Api-Version", "2022-11-28")
        if data is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=30) as response:
                raw = response.read()
                return json.loads(raw.decode("utf-8")) if raw else None
        except urllib.error.HTTPError as exc:
            raise PullRequestQualificationError("GITHUB_API_READ_ERROR", {"method": method, "url": url, "status": exc.code, "body": exc.read().decode("utf-8", "replace")[-4000:]})
        except urllib.error.URLError as exc:
            raise PullRequestQualificationError("GITHUB_API_UNREACHABLE", {"method": method, "url": url, "error": str(exc)})

    def get(self, path):
        return self.request("GET", self.api_url + path)

    def graphql(self, query, variables):
        response = self.request("POST", "https://api.github.com/graphql", {"query": query, "variables": variables})
        require(isinstance(response, dict) and not response.get("errors"), "GITHUB_GRAPHQL_ERROR", response.get("errors") if isinstance(response, dict) else response)
        return response.get("data") or {}


def paged_list(client, path):
    out = []
    page = 1
    while True:
        sep = "&" if "?" in path else "?"
        items = client.get(f"{path}{sep}per_page=100&page={page}")
        require(isinstance(items, list), "GITHUB_LIST_RESPONSE_INVALID", {"path": path})
        out.extend(items)
        if len(items) < 100:
            break
        page += 1
        require(page <= 100, "GITHUB_PAGINATION_LIMIT", {"path": path})
    return out


def pr_files(client, repository, number):
    paths = []
    for item in paged_list(client, f"/repos/{repository}/pulls/{number}/files"):
        path = item.get("filename") if isinstance(item, dict) else None
        require(isinstance(path, str) and path, "PULL_REQUEST_FILE_INVALID")
        paths.append(path)
    require(len(paths) == len(set(paths)), "PULL_REQUEST_FILE_DUPLICATE")
    return sorted(paths)


def review_threads(client, repository, number):
    owner, name = repository.split("/", 1)
    query = """
    query($owner:String!,$name:String!,$number:Int!,$cursor:String){
      repository(owner:$owner,name:$name){
        pullRequest(number:$number){
          reviewThreads(first:100,after:$cursor){
            nodes{isResolved}
            pageInfo{hasNextPage endCursor}
          }
        }
      }
    }
    """
    cursor = None
    nodes = []
    while True:
        data = client.graphql(query, {"owner": owner, "name": name, "number": number, "cursor": cursor})
        repository_node = data.get("repository") or {}
        pr_node = repository_node.get("pullRequest") or {}
        threads = pr_node.get("reviewThreads") or {}
        page_nodes = threads.get("nodes") or []
        require(isinstance(page_nodes, list), "REVIEW_THREADS_RESPONSE_INVALID")
        nodes.extend(page_nodes)
        page = threads.get("pageInfo") or {}
        if not page.get("hasNextPage"):
            break
        cursor = page.get("endCursor")
        require(isinstance(cursor, str) and cursor, "REVIEW_THREADS_CURSOR_INVALID")
        require(len(nodes) <= 10000, "REVIEW_THREADS_PAGINATION_LIMIT")
    return nodes


def check_runs(client, repository, head_sha):
    runs = []
    page = 1
    while True:
        response = client.get(f"/repos/{repository}/commits/{head_sha}/check-runs?per_page=100&page={page}")
        require(isinstance(response, dict) and isinstance(response.get("check_runs"), list), "CHECK_RUNS_RESPONSE_INVALID")
        batch = response["check_runs"]
        runs.extend(batch)
        if len(batch) < 100:
            break
        page += 1
        require(page <= 100, "CHECK_RUNS_PAGINATION_LIMIT")
    return runs


def read_snapshot(protocol, source, client):
    repository = source["repository"]
    number = source["pullRequestNumber"]
    pr = None
    for _ in range(6):
        pr = client.get(f"/repos/{repository}/pulls/{number}")
        require(isinstance(pr, dict) and pr.get("number") == number, "PULL_REQUEST_RESPONSE_INVALID")
        if pr.get("mergeable") is not None:
            break
        time.sleep(1)
    require(pr.get("mergeable") is not None, "PULL_REQUEST_MERGEABILITY_UNKNOWN")

    base = pr.get("base") or {}
    head = pr.get("head") or {}
    require((base.get("repo") or {}).get("full_name") == repository, "LIVE_BASE_REPOSITORY_MISMATCH")
    require((head.get("repo") or {}).get("full_name") == repository, "LIVE_HEAD_REPOSITORY_MISMATCH")
    require(base.get("ref") == source["baseBranch"] and base.get("sha") == source["baseSha"], "LIVE_BASE_SHA_MISMATCH", {"actualRef": base.get("ref"), "actualSha": base.get("sha")})
    require(head.get("ref") == source["headBranch"] and head.get("sha") == source["headSha"], "LIVE_HEAD_SHA_MISMATCH", {"actualRef": head.get("ref"), "actualSha": head.get("sha")})

    paths = pr_files(client, repository, number)
    require(paths == sorted(source["changedPaths"]), "LIVE_CHANGED_PATHS_MISMATCH", {"expected": sorted(source["changedPaths"]), "actual": paths})

    reviews = paged_list(client, f"/repos/{repository}/pulls/{number}/reviews")
    requested = client.get(f"/repos/{repository}/pulls/{number}/requested_reviewers")
    require(isinstance(requested, dict), "REQUESTED_REVIEWERS_RESPONSE_INVALID")
    threads = review_threads(client, repository, number)
    unresolved = sum(1 for thread in threads if isinstance(thread, dict) and thread.get("isResolved") is not True)
    statuses = paged_list(client, f"/repos/{repository}/commits/{source['headSha']}/statuses")
    runs = check_runs(client, repository, source["headSha"])

    policy = protocol["qualificationPolicy"]
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
        "changedPaths": paths,
        "reviewSummary": effective_review_summary(reviews),
        "requestedReviewerLogins": sorted((user or {}).get("login") for user in (requested.get("users") or []) if isinstance((user or {}).get("login"), str)),
        "requestedTeamSlugs": sorted((team or {}).get("slug") for team in (requested.get("teams") or []) if isinstance((team or {}).get("slug"), str)),
        "unresolvedReviewThreads": unresolved,
        "checkSummary": check_summary(runs, statuses, policy),
    }


def qualify(protocol, source, token, api_url="https://api.github.com"):
    changed_paths, verification_classes = validate_source(protocol, source)
    client = GitHubReader(token, api_url)
    snapshot = read_snapshot(protocol, source, client)
    decision = qualify_snapshot(protocol["qualificationPolicy"], snapshot)

    core = {
        "schemaVersion": 1,
        "protocolId": protocol["id"],
        "caseId": source["caseId"],
        "repository": source["repository"],
        "provider": "GITHUB",
        "sourceProviderDiffAttestationId": source["providerDiffAttestationId"],
        "sourceProviderDiffAttestationSha256": canonical_digest(source),
        "pullRequestNumber": source["pullRequestNumber"],
        "baseBranch": source["baseBranch"],
        "baseSha": source["baseSha"],
        "headBranch": source["headBranch"],
        "headSha": source["headSha"],
        "changedPaths": changed_paths,
        "verificationClasses": verification_classes,
        "providerDiffSha256": source["providerDiffSha256"],
        "localDiffSha256": source["localDiffSha256"],
        "headTreeSha": source["headTreeSha"],
        "reviewSummary": snapshot["reviewSummary"],
        "requestedReviewerLogins": snapshot["requestedReviewerLogins"],
        "requestedTeamSlugs": snapshot["requestedTeamSlugs"],
        "unresolvedReviewThreads": snapshot["unresolvedReviewThreads"],
        "checkSummary": snapshot["checkSummary"],
        "mergeable": snapshot["mergeable"],
        "qualificationReasons": decision["reasons"],
        "mergeAuthorization": "NOT_GRANTED",
        "reviewSubmissionAuthorization": "NOT_GRANTED",
        "pullRequestUpdateAuthorization": "NOT_GRANTED",
        "autoMergeAuthorization": "NOT_GRANTED",
        "directMainUpdate": False,
        "repositoryAdministration": False,
        "consumerAdoption": False,
    }
    if decision["disposition"] == "PR_QUALIFIED_FOR_HUMAN_MERGE_REVIEW":
        return {
            **core,
            "status": "PULL_REQUEST_QUALIFIED",
            "decision": "PR_QUALIFIED_FOR_HUMAN_MERGE_REVIEW",
            "qualificationAttestationId": canonical_digest(core),
        }
    return {
        **core,
        "status": "STOP_AND_INVESTIGATE",
        "decision": "PR_NOT_QUALIFIED_FOR_HUMAN_MERGE_REVIEW",
        "qualificationAttestationId": canonical_digest(core),
    }


def parse_args():
    parser = argparse.ArgumentParser(description="CG-18 pull request qualification decision")
    parser.add_argument("--protocol", required=True)
    parser.add_argument("--source-attestation", required=True)
    parser.add_argument("--api-url", default=os.environ.get("GITHUB_API_URL", "https://api.github.com"))
    return parser.parse_args()


def main():
    args = parse_args()
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or ""
    try:
        result = qualify(load(args.protocol), load(args.source_attestation), token, args.api_url)
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0 if result["status"] == "PULL_REQUEST_QUALIFIED" else 3
    except PullRequestQualificationError as exc:
        out = {"schemaVersion": 1, "status": "REJECTED", "decision": "PR_QUALIFICATION_NOT_EVALUATED", "reason": exc.reason}
        if exc.detail is not None:
            out["detail"] = exc.detail
        print(json.dumps(out, indent=2, sort_keys=True))
        return 2


if __name__ == "__main__":
    sys.exit(main())
