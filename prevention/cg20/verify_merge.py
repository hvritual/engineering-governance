#!/usr/bin/env python3
import argparse
import hashlib
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from prepare_merge import canonical_digest, validate_source


class MergeExecutionVerificationError(Exception):
    def __init__(self, reason, detail=None):
        super().__init__(reason)
        self.reason = reason
        self.detail = detail


def require(condition, reason, detail=None):
    if not condition:
        raise MergeExecutionVerificationError(reason, detail)


def load(path):
    return json.loads(Path(path).read_text())


def request_core(request):
    return {k: v for k, v in request.items() if k not in {"status", "decision", "requestId"}}


def receipt_core(receipt):
    return {k: v for k, v in receipt.items() if k != "receiptId"}


def validate_request(protocol, source, request):
    try:
        changed_paths, verification_classes = validate_source(protocol, source)
    except Exception as exc:
        reason = getattr(exc, "reason", "CG19_AUTHORIZATION_INVALID")
        detail = getattr(exc, "detail", None)
        raise MergeExecutionVerificationError(reason, detail)

    require(isinstance(request, dict), "MERGE_EXECUTION_REQUEST_REQUIRED")
    require(request.get("schemaVersion") == 1, "REQUEST_SCHEMA_MISMATCH")
    require(request.get("protocolId") == protocol.get("id"), "REQUEST_PROTOCOL_MISMATCH")
    require(request.get("status") == protocol["outputs"]["requestStatus"], "REQUEST_STATUS_INVALID")
    require(request.get("decision") == protocol["outputs"]["requestDecision"], "REQUEST_DECISION_INVALID")
    require(canonical_digest(request_core(request)) == request.get("requestId"), "REQUEST_ID_MISMATCH")
    require(request.get("sourceAuthorizationId") == source.get("authorizationId"), "REQUEST_AUTHORIZATION_BINDING_MISMATCH")
    require(request.get("sourceAuthorizationSha256") == canonical_digest(source), "REQUEST_AUTHORIZATION_DIGEST_MISMATCH")

    bindings = {
        "repository": source["repository"],
        "pullRequestNumber": source["pullRequestNumber"],
        "baseBranch": source["baseBranch"],
        "baseSha": source["baseSha"],
        "headBranch": source["headBranch"],
        "headSha": source["headSha"],
        "expectedHeadSha": source["expectedHeadSha"],
        "mergeMethod": source["mergeMethod"],
        "mergeAuthorization": source["mergeAuthorization"],
        "authorizationUseLimit": source["authorizationUseLimit"],
        "sourceConsumptionState": source["consumptionState"],
    }
    for field, expected in bindings.items():
        require(request.get(field) == expected, "REQUEST_BINDING_MISMATCH", {"field": field, "expected": expected, "actual": request.get(field)})
    require(sorted(request.get("changedPaths") or []) == changed_paths, "REQUEST_CHANGED_PATHS_MISMATCH")
    require(sorted(request.get("verificationClasses") or []) == verification_classes, "REQUEST_VERIFICATION_CLASSES_MISMATCH")
    policy = protocol["executionPolicy"]
    require(request.get("provider") == policy["provider"], "REQUEST_PROVIDER_MISMATCH")
    require(request.get("externalExecutor") == policy["externalExecutor"], "REQUEST_EXECUTOR_MISMATCH")
    require(request.get("operation") == policy["operation"], "REQUEST_OPERATION_MISMATCH")
    require(request.get("providerCompareAndSwap") == policy["providerCompareAndSwap"], "REQUEST_CAS_MISMATCH")
    require(request.get("providerSingleUseBoundary") == policy["providerSingleUseBoundary"], "REQUEST_SINGLE_USE_BOUNDARY_MISMATCH")
    require(request.get("autoMerge") is False, "AUTO_MERGE_FORBIDDEN")
    require(request.get("directMainUpdate") is False, "DIRECT_MAIN_UPDATE_FORBIDDEN")
    require(request.get("repositoryAdministration") is False, "REPOSITORY_ADMINISTRATION_FORBIDDEN")
    require(request.get("consumerAdoption") is False, "CONSUMER_ADOPTION_FORBIDDEN")
    return changed_paths, verification_classes


def validate_receipt(protocol, source, request, receipt):
    require(isinstance(receipt, dict), "EXTERNAL_MERGE_RECEIPT_REQUIRED")
    require(receipt.get("schemaVersion") == 1, "RECEIPT_SCHEMA_MISMATCH")
    require(receipt.get("executor") == protocol["executionPolicy"]["externalExecutor"], "RECEIPT_EXECUTOR_MISMATCH")
    require(receipt.get("requestId") == request.get("requestId"), "RECEIPT_REQUEST_BINDING_MISMATCH")
    require(receipt.get("sourceAuthorizationId") == source.get("authorizationId"), "RECEIPT_AUTHORIZATION_BINDING_MISMATCH")
    require(receipt.get("repository") == source.get("repository"), "RECEIPT_REPOSITORY_MISMATCH")
    require(receipt.get("pullRequestNumber") == source.get("pullRequestNumber"), "RECEIPT_PULL_REQUEST_MISMATCH")
    require(receipt.get("expectedHeadSha") == source.get("expectedHeadSha"), "RECEIPT_EXPECTED_HEAD_MISMATCH")
    require(receipt.get("mergeMethod") == source.get("mergeMethod"), "RECEIPT_MERGE_METHOD_MISMATCH")
    require(receipt.get("merged") is True, "PROVIDER_MERGE_NOT_SUCCESSFUL")
    require(re.fullmatch(r"[0-9a-f]{40}", receipt.get("mergeSha", "")) is not None, "RECEIPT_MERGE_SHA_INVALID")
    require(canonical_digest(receipt_core(receipt)) == receipt.get("receiptId"), "RECEIPT_ID_MISMATCH")


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
            raise MergeExecutionVerificationError("GITHUB_API_READ_ERROR", {
                "path": path,
                "status": exc.code,
                "body": exc.read().decode("utf-8", "replace")[-4000:],
            })
        except urllib.error.URLError as exc:
            raise MergeExecutionVerificationError("GITHUB_API_UNREACHABLE", {"path": path, "error": str(exc)})


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


def verify(protocol, source, request, receipt, token, api_url="https://api.github.com"):
    changed_paths, verification_classes = validate_request(protocol, source, request)
    validate_receipt(protocol, source, request, receipt)
    client = GitHubReader(token, api_url)
    repository = source["repository"]
    number = source["pullRequestNumber"]
    pr = client.get(f"/repos/{repository}/pulls/{number}")
    require(isinstance(pr, dict) and pr.get("number") == number, "PULL_REQUEST_RESPONSE_INVALID")
    require(pr.get("merged") is True, "PULL_REQUEST_NOT_MERGED")
    require(pr.get("state") == "closed", "MERGED_PULL_REQUEST_NOT_CLOSED")
    require(pr.get("draft") is False, "DRAFT_STATE_DRIFT")

    base = pr.get("base") or {}
    head = pr.get("head") or {}
    require((base.get("repo") or {}).get("full_name") == repository, "POST_MERGE_BASE_REPOSITORY_MISMATCH")
    require((head.get("repo") or {}).get("full_name") == repository, "POST_MERGE_HEAD_REPOSITORY_MISMATCH")
    require(base.get("ref") == source["baseBranch"], "POST_MERGE_BASE_BRANCH_MISMATCH")
    require(head.get("ref") == source["headBranch"] and head.get("sha") == source["headSha"], "POST_MERGE_HEAD_IDENTITY_MISMATCH")
    require(pr.get("merge_commit_sha") == receipt["mergeSha"], "PROVIDER_MERGE_SHA_MISMATCH", {"expected": receipt["mergeSha"], "actual": pr.get("merge_commit_sha")})
    actual_paths = list_pr_files(client, repository, number)
    require(actual_paths == changed_paths, "POST_MERGE_CHANGED_PATHS_MISMATCH")

    encoded_base = urllib.parse.quote(source["baseBranch"], safe="")
    ref = client.get(f"/repos/{repository}/git/ref/heads/{encoded_base}")
    require(isinstance(ref, dict), "BASE_REF_RESPONSE_INVALID")
    ref_object = ref.get("object") or {}
    require(ref_object.get("sha") == receipt["mergeSha"], "POST_MERGE_BASE_REF_MISMATCH", {"expected": receipt["mergeSha"], "actual": ref_object.get("sha")})

    merge_commit = client.get(f"/repos/{repository}/git/commits/{receipt['mergeSha']}")
    head_commit = client.get(f"/repos/{repository}/git/commits/{source['headSha']}")
    require(isinstance(merge_commit, dict) and isinstance(head_commit, dict), "COMMIT_READBACK_INVALID")
    parents = merge_commit.get("parents") or []
    require(len(parents) == 1, "MERGE_COMMIT_PARENT_COUNT_INVALID", {"count": len(parents)})
    require((parents[0] or {}).get("sha") == source["baseSha"], "MERGE_COMMIT_PARENT_MISMATCH", {"expected": source["baseSha"], "actual": (parents[0] or {}).get("sha")})
    merge_tree = (merge_commit.get("tree") or {}).get("sha")
    head_tree = (head_commit.get("tree") or {}).get("sha")
    require(re.fullmatch(r"[0-9a-f]{40}", merge_tree or "") is not None, "MERGE_TREE_INVALID")
    require(re.fullmatch(r"[0-9a-f]{40}", head_tree or "") is not None, "HEAD_TREE_INVALID")
    require(merge_tree == head_tree, "MERGE_TREE_HEAD_TREE_MISMATCH", {"mergeTree": merge_tree, "headTree": head_tree})

    policy = protocol["executionPolicy"]
    core = {
        "schemaVersion": 1,
        "protocolId": protocol["id"],
        "caseId": source["caseId"],
        "repository": repository,
        "provider": policy["provider"],
        "externalExecutor": policy["externalExecutor"],
        "sourceAuthorizationId": source["authorizationId"],
        "sourceAuthorizationSha256": canonical_digest(source),
        "mergeExecutionRequestId": request["requestId"],
        "mergeExecutionRequestSha256": canonical_digest(request),
        "externalMergeReceiptSha256": canonical_digest(receipt),
        "pullRequestNumber": number,
        "baseBranch": source["baseBranch"],
        "authorizedBaseSha": source["baseSha"],
        "headBranch": source["headBranch"],
        "headSha": source["headSha"],
        "expectedHeadSha": source["expectedHeadSha"],
        "changedPaths": changed_paths,
        "verificationClasses": verification_classes,
        "mergeMethod": source["mergeMethod"],
        "mergeSha": receipt["mergeSha"],
        "mergeParentSha": source["baseSha"],
        "mergeTreeSha": merge_tree,
        "headTreeSha": head_tree,
        "postMergeBaseSha": ref_object.get("sha"),
        "authorizationConsumption": policy["successfulConsumptionReason"],
        "authorizationUseCount": policy["successfulUseCount"],
        "consumptionState": policy["successfulConsumptionState"],
        "providerCompareAndSwap": policy["providerCompareAndSwap"],
        "providerSingleUseBoundary": policy["providerSingleUseBoundary"],
        "secondConsumptionStateSource": "PROVIDER_PR_MERGED_STATE",
        "autoMerge": False,
        "directMainUpdate": False,
        "repositoryAdministration": False,
        "consumerAdoption": False,
    }
    return {
        **core,
        "status": protocol["outputs"]["attestedStatus"],
        "decision": protocol["outputs"]["attestedDecision"],
        "executionAttestationId": canonical_digest(core),
    }


def parse_args():
    parser = argparse.ArgumentParser(description="CG-20 post-merge execution and authorization consumption verifier")
    parser.add_argument("--protocol", required=True)
    parser.add_argument("--source-authorization", required=True)
    parser.add_argument("--execution-request", required=True)
    parser.add_argument("--external-receipt", required=True)
    parser.add_argument("--api-url", default=os.environ.get("GITHUB_API_URL", "https://api.github.com"))
    return parser.parse_args()


def main():
    args = parse_args()
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or ""
    try:
        out = verify(load(args.protocol), load(args.source_authorization), load(args.execution_request), load(args.external_receipt), token, args.api_url)
        print(json.dumps(out, indent=2, sort_keys=True))
        return 0
    except MergeExecutionVerificationError as exc:
        out = {"schemaVersion": 1, "status": "REJECTED", "decision": "MERGE_EXECUTION_NOT_ATTESTED", "reason": exc.reason}
        if exc.detail is not None:
            out["detail"] = exc.detail
        print(json.dumps(out, indent=2, sort_keys=True))
        return 2


if __name__ == "__main__":
    sys.exit(main())
