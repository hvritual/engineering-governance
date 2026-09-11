#!/usr/bin/env python3
import argparse
import json
import re
import sys
from pathlib import Path, PurePosixPath


SHA40 = re.compile(r"^[0-9a-f]{40}$")


class VerificationError(Exception):
    def __init__(self, reason, detail=None):
        super().__init__(reason)
        self.reason = reason
        self.detail = detail


def load(path):
    return json.loads(Path(path).read_text())


def require(condition, reason, detail=None):
    if not condition:
        raise VerificationError(reason, detail)


def emit(value):
    print(json.dumps(value, indent=2, sort_keys=True))


def parse_args():
    parser = argparse.ArgumentParser(
        description="CG-07 deterministic post-merge integrity and recovery verifier"
    )
    parser.add_argument("--protocol", required=True)
    parser.add_argument("--receipt", required=True)
    parser.add_argument("--observed", required=True)
    return parser.parse_args()


def valid_sha(value):
    return isinstance(value, str) and SHA40.fullmatch(value) is not None


def canonical_paths(value, reason):
    require(isinstance(value, list), reason, {"problem": "not_a_list"})
    result = []
    for item in value:
        require(isinstance(item, str) and item, reason, {"problem": "invalid_path", "path": item})
        path = PurePosixPath(item)
        require(not path.is_absolute(), reason, {"problem": "absolute_path", "path": item})
        require(".." not in path.parts, reason, {"problem": "parent_escape", "path": item})
        normalized = path.as_posix()
        require(normalized == item, reason, {"problem": "non_canonical_path", "path": item})
        result.append(item)
    require(len(result) == len(set(result)), reason, {"problem": "duplicate_path"})
    return sorted(result)


def main():
    args = parse_args()
    try:
        protocol = load(args.protocol)
        receipt = load(args.receipt)
        observed = load(args.observed)

        require(protocol.get("schemaVersion") == 1, "PROTOCOL_SCHEMA_MISMATCH")
        require(protocol.get("id") == "CG07-POST-MERGE-INTEGRITY-RECOVERY", "WRONG_PROTOCOL")
        require(protocol.get("status") in {"CANDIDATE", "QUALIFIED"}, "UNEXPECTED_PROTOCOL_STATE")
        ceiling = protocol.get("authorityCeiling", {})
        require(ceiling.get("repositoryBlocking") is False, "AUTHORITY_CEILING_REPOSITORY_BLOCKING")
        require(ceiling.get("changesCg06TrustSemantics") is False, "CG06_TRUST_SEMANTICS_CHANGED")
        require(ceiling.get("automaticDestructiveRecovery") is False, "DESTRUCTIVE_RECOVERY_AUTHORIZED")

        require(receipt.get("schemaVersion") == 1, "RECEIPT_SCHEMA_MISMATCH")
        require(receipt.get("protocolId") == protocol.get("id"), "RECEIPT_PROTOCOL_MISMATCH")
        require(observed.get("schemaVersion") == 1, "OBSERVED_SCHEMA_MISMATCH")

        delivery = receipt.get("delivery", {})
        required_fields = protocol.get("requiredIdentities", [])
        for field in required_fields:
            require(field in delivery, "RECEIPT_IDENTITY_MISSING", {"field": field})

        repository = delivery.get("repository")
        pr_number = delivery.get("pullRequest")
        base_branch = delivery.get("baseBranch")
        reviewed_head = delivery.get("reviewedHeadSha")
        pre_main = delivery.get("preMainSha")
        merge_method = delivery.get("mergeMethod")
        post_merge = delivery.get("postMergeSha")

        require(isinstance(repository, str) and repository.count("/") == 1, "REPOSITORY_IDENTITY_INVALID")
        require(isinstance(pr_number, int) and pr_number > 0, "PULL_REQUEST_IDENTITY_INVALID")
        require(base_branch == protocol.get("supportedDelivery", {}).get("requiredBaseBranch"), "BASE_BRANCH_MISMATCH")
        require(valid_sha(reviewed_head), "REVIEWED_HEAD_SHA_INVALID")
        require(valid_sha(pre_main), "PRE_MAIN_SHA_INVALID")
        require(valid_sha(post_merge), "POST_MERGE_SHA_INVALID")
        require(
            merge_method in protocol.get("supportedDelivery", {}).get("mergeMethods", []),
            "UNSUPPORTED_MERGE_METHOD",
            {"mergeMethod": merge_method},
        )
        reviewed_paths = canonical_paths(delivery.get("reviewedPaths"), "REVIEWED_PATHS_INVALID")

        merge_request = receipt.get("mergeRequest", {})
        require(merge_request.get("expectedHeadSha") == reviewed_head, "MERGE_REQUEST_HEAD_NOT_LOCKED")
        require(merge_request.get("nonForce") is True, "MERGE_REQUEST_NOT_NON_FORCE")

        recovery = receipt.get("recovery", {})
        allowed = set(protocol.get("recovery", {}).get("allowedDispositions", []))
        requested = recovery.get("requestedDisposition")
        require(requested in allowed, "RECOVERY_DISPOSITION_NOT_ALLOWED")
        require(recovery.get("automaticForcePush") is False, "AUTOMATIC_FORCE_PUSH_FORBIDDEN")
        require(recovery.get("automaticReset") is False, "AUTOMATIC_RESET_FORBIDDEN")
        require(recovery.get("automaticRevert") is False, "AUTOMATIC_REVERT_FORBIDDEN")
        require(
            requested == protocol.get("recovery", {}).get("integrityPassDisposition"),
            "RECOVERY_REQUEST_NOT_PASS_DISPOSITION",
        )

        require(observed.get("repository") == repository, "OBSERVED_REPOSITORY_MISMATCH")
        pr = observed.get("pullRequest", {})
        require(pr.get("number") == pr_number, "OBSERVED_PULL_REQUEST_MISMATCH")
        require(pr.get("state") == "closed", "PULL_REQUEST_NOT_CLOSED")
        require(pr.get("merged") is True, "PULL_REQUEST_NOT_MERGED")
        require(pr.get("baseBranch") == base_branch, "OBSERVED_BASE_BRANCH_MISMATCH")
        require(pr.get("headSha") == reviewed_head, "OBSERVED_REVIEWED_HEAD_MISMATCH")
        require(pr.get("mergeCommitSha") == post_merge, "OBSERVED_MERGE_SHA_MISMATCH")

        post = observed.get("postMergeCommit", {})
        require(post.get("sha") == post_merge, "POST_MERGE_COMMIT_IDENTITY_MISMATCH")
        parents = post.get("parents")
        require(isinstance(parents, list), "POST_MERGE_PARENTS_INVALID")
        if merge_method == "squash":
            require(parents == [pre_main], "SQUASH_PARENT_MISMATCH", {"expected": [pre_main], "actual": parents})

        observed_paths = canonical_paths(observed.get("pullRequestPaths"), "OBSERVED_PATHS_INVALID")
        require(
            observed_paths == reviewed_paths,
            "REVIEWED_PATH_SET_MISMATCH",
            {"expected": reviewed_paths, "actual": observed_paths},
        )

        current = observed.get("currentMain", {})
        current_sha = current.get("sha")
        relation = current.get("relationFromQualifiedMerge")
        contains = current.get("containsQualifiedMerge")
        require(valid_sha(current_sha), "CURRENT_MAIN_SHA_INVALID")
        require(relation in {"identical", "ahead"}, "CURRENT_MAIN_DOES_NOT_CONTAIN_QUALIFIED_MERGE", {"relation": relation})
        require(contains is True, "QUALIFIED_MERGE_NOT_IN_CURRENT_MAIN")
        if relation == "identical":
            require(current_sha == post_merge, "IDENTICAL_RELATION_SHA_MISMATCH")
        else:
            require(current_sha != post_merge, "AHEAD_RELATION_SHA_MISMATCH")

        emit(
            {
                "schemaVersion": 1,
                "status": "PASS",
                "decision": "POST_MERGE_INTEGRITY_VERIFIED",
                "repositoryBlocking": False,
                "repository": repository,
                "pullRequest": pr_number,
                "reviewedHeadSha": reviewed_head,
                "preMainSha": pre_main,
                "postMergeSha": post_merge,
                "currentMainSha": current_sha,
                "currentMainRelation": relation,
                "recoveryDisposition": protocol.get("recovery", {}).get("integrityPassDisposition"),
            }
        )
        return 0
    except VerificationError as exc:
        value = {
            "schemaVersion": 1,
            "status": "REJECTED",
            "decision": "POST_MERGE_INTEGRITY_NOT_PROVEN",
            "reason": exc.reason,
            "repositoryBlocking": False,
            "recoveryDisposition": "STOP_AND_INVESTIGATE",
        }
        if exc.detail is not None:
            value["detail"] = exc.detail
        emit(value)
        return 2


if __name__ == "__main__":
    sys.exit(main())
