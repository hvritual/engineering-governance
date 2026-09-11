#!/usr/bin/env python3
import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

HEX40 = re.compile(r"^[0-9a-f]{40}$")
HEX64 = re.compile(r"^[0-9a-f]{64}$")


class ReconcileError(Exception):
    def __init__(self, reason, detail=None):
        super().__init__(reason)
        self.reason = reason
        self.detail = detail


def load_json(path):
    try:
        return json.loads(Path(path).read_text())
    except Exception as exc:
        raise ReconcileError("INVALID_JSON", {"path": str(path), "error": str(exc)}) from exc


def sha256_file(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def reject_unless(condition, reason, detail=None):
    if not condition:
        raise ReconcileError(reason, detail)


def write_result(path, value):
    text = json.dumps(value, indent=2, sort_keys=True, separators=(",", ": ")) + "\n"
    if path:
        Path(path).write_text(text)
    sys.stdout.write(text)


def parse_args():
    p = argparse.ArgumentParser(description="CG-05 trusted shadow delivery receipt reconciler")
    p.add_argument("--protocol", required=True)
    p.add_argument("--plan", required=True)
    p.add_argument("--profile", required=True)
    p.add_argument("--lock", required=True)
    p.add_argument("--candidate", required=True)
    p.add_argument("--receipt", action="append", default=[], required=True)
    p.add_argument("--protocol-blob", required=True)
    p.add_argument("--plan-blob", required=True)
    p.add_argument("--profile-blob", required=True)
    p.add_argument("--lock-blob", required=True)
    p.add_argument("--output")
    return p.parse_args()


def main():
    args = parse_args()
    try:
        protocol = load_json(args.protocol)
        plan = load_json(args.plan)
        profile = load_json(args.profile)
        lock = load_json(args.lock)
        candidate = load_json(args.candidate)

        for label, value in (
            ("protocolBlob", args.protocol_blob),
            ("planBlob", args.plan_blob),
            ("profileBlob", args.profile_blob),
            ("lockBlob", args.lock_blob),
        ):
            reject_unless(HEX40.match(value) is not None, "INVALID_GIT_BLOB_ID", {"field": label, "value": value})

        reject_unless(protocol.get("schemaVersion") == 1, "PROTOCOL_SCHEMA_MISMATCH")
        reject_unless(protocol.get("status") in ("CANDIDATE", "QUALIFIED_TRUSTED_SHADOW_GATE"), "PROTOCOL_NOT_QUALIFIED_INPUT")
        reject_unless(plan.get("schemaVersion") == 1, "PLAN_SCHEMA_MISMATCH")
        reject_unless(plan.get("status") in ("CANDIDATE", "QUALIFIED"), "PLAN_NOT_QUALIFIED_INPUT")
        reject_unless(profile.get("status") == "ADOPTED_EXTERNAL_SHADOW", "PROFILE_NOT_ACTIVE_ADOPTION")
        reject_unless(lock.get("status") == "ACTIVE", "LOCK_NOT_ACTIVE")

        repo = candidate.get("repository")
        source = candidate.get("sourceCommit")
        reject_unless(repo == plan.get("consumer", {}).get("repository"), "CANDIDATE_REPOSITORY_MISMATCH")
        reject_unless(HEX40.match(source or "") is not None, "INVALID_CANDIDATE_SOURCE_SHA", {"sourceCommit": source})
        reject_unless(profile.get("consumer", {}).get("repository") == repo, "PROFILE_CONSUMER_MISMATCH")
        reject_unless(profile.get("id") == plan.get("adoption", {}).get("profileId"), "PROFILE_ID_MISMATCH")
        reject_unless(lock.get("id") == plan.get("adoption", {}).get("lockId"), "LOCK_ID_MISMATCH")
        reject_unless(lock.get("profileId") == profile.get("id"), "LOCK_PROFILE_MISMATCH")
        reject_unless(args.profile_blob == plan.get("adoption", {}).get("profileBlob"), "PROFILE_BLOB_MISMATCH")
        reject_unless(args.lock_blob == plan.get("adoption", {}).get("lockBlob"), "LOCK_BLOB_MISMATCH")

        expected = [item.get("id") for item in plan.get("requiredChecks", [])]
        reject_unless(expected and all(isinstance(x, str) and x for x in expected), "INVALID_REQUIRED_CHECK_SET")
        reject_unless(len(expected) == len(set(expected)), "DUPLICATE_REQUIRED_CHECK_ID_IN_PLAN")
        expected_set = set(expected)

        receipts = []
        seen = set()
        for receipt_path in args.receipt:
            receipt = load_json(receipt_path)
            check_id = receipt.get("checkId")
            reject_unless(check_id not in seen, "DUPLICATE_CHECK_RECEIPT", {"checkId": check_id})
            seen.add(check_id)
            reject_unless(check_id in expected_set, "UNKNOWN_OR_SUBSTITUTED_CHECK", {"checkId": check_id})
            reject_unless(receipt.get("schemaVersion") == 1, "RECEIPT_SCHEMA_MISMATCH", {"checkId": check_id})
            reject_unless(receipt.get("status") == "PASS", "REQUIRED_CHECK_FAILED", {"checkId": check_id, "status": receipt.get("status")})

            rc = receipt.get("candidate", {})
            reject_unless(rc.get("repository") == repo, "RECEIPT_REPOSITORY_MISMATCH", {"checkId": check_id})
            reject_unless(rc.get("sourceCommit") == source, "RECEIPT_SOURCE_MISMATCH", {"checkId": check_id})

            rp = receipt.get("profile", {})
            rl = receipt.get("lock", {})
            rplan = receipt.get("plan", {})
            reject_unless(rp.get("id") == profile.get("id") and rp.get("blob") == args.profile_blob, "RECEIPT_PROFILE_MISMATCH", {"checkId": check_id})
            reject_unless(rl.get("id") == lock.get("id") and rl.get("blob") == args.lock_blob, "RECEIPT_LOCK_MISMATCH", {"checkId": check_id})
            reject_unless(rplan.get("id") == plan.get("id") and rplan.get("blob") == args.plan_blob, "RECEIPT_PLAN_MISMATCH", {"checkId": check_id})

            execution = receipt.get("execution", {})
            reject_unless(isinstance(execution.get("run"), int) and execution["run"] > 0, "INVALID_EXECUTION_ID", {"checkId": check_id, "field": "run"})
            job_identity = execution.get("job")
            reject_unless((isinstance(job_identity, int) and job_identity > 0) or (isinstance(job_identity, str) and job_identity.strip()), "INVALID_EXECUTION_ID", {"checkId": check_id, "field": "job"})
            reject_unless(isinstance(execution.get("attempt"), int) and execution["attempt"] > 0, "INVALID_EXECUTION_ID", {"checkId": check_id, "field": "attempt"})
            reject_unless(HEX40.match(execution.get("workflowHead") or "") is not None, "INVALID_WORKFLOW_HEAD", {"checkId": check_id})

            evidence = receipt.get("evidence", {})
            evidence_rel = evidence.get("path")
            evidence_digest = evidence.get("sha256")
            reject_unless(isinstance(evidence_rel, str) and evidence_rel, "MISSING_EVIDENCE_PATH", {"checkId": check_id})
            reject_unless(HEX64.match(evidence_digest or "") is not None, "INVALID_EVIDENCE_DIGEST", {"checkId": check_id})
            evidence_path = Path(receipt_path).parent / evidence_rel
            reject_unless(evidence_path.is_file(), "MISSING_EVIDENCE_FILE", {"checkId": check_id, "path": str(evidence_path)})
            actual_digest = sha256_file(evidence_path)
            reject_unless(actual_digest == evidence_digest, "EVIDENCE_DIGEST_MISMATCH", {"checkId": check_id, "expected": evidence_digest, "actual": actual_digest})

            receipts.append({
                "checkId": check_id,
                "receiptSha256": sha256_file(receipt_path),
                "evidenceSha256": actual_digest,
                "run": execution["run"],
                "job": job_identity,
                "attempt": execution["attempt"],
                "workflowHead": execution["workflowHead"],
            })

        actual_set = set(seen)
        reject_unless(actual_set == expected_set, "REQUIRED_CHECK_SET_MISMATCH", {
            "expected": sorted(expected_set),
            "actual": sorted(actual_set),
            "missing": sorted(expected_set - actual_set),
            "unexpected": sorted(actual_set - expected_set),
        })

        result = {
            "schemaVersion": 1,
            "status": "PASS",
            "decision": "QUALIFIED_TRUSTED_SHADOW_GATE_PASS",
            "protocol": {"id": protocol.get("id"), "blob": args.protocol_blob},
            "plan": {"id": plan.get("id"), "version": plan.get("version"), "blob": args.plan_blob},
            "candidate": {"repository": repo, "sourceCommit": source},
            "profile": {"id": profile.get("id"), "blob": args.profile_blob},
            "lock": {"id": lock.get("id"), "blob": args.lock_blob},
            "requiredCheckSet": sorted(expected_set),
            "componentReceipts": sorted(receipts, key=lambda x: x["checkId"]),
            "repositoryBlocking": False,
        }
        write_result(args.output, result)
        return 0
    except ReconcileError as exc:
        value = {"schemaVersion": 1, "status": "REJECTED", "reason": exc.reason}
        if exc.detail is not None:
            value["detail"] = exc.detail
        write_result(args.output, value)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
