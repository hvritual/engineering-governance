#!/usr/bin/env python3
import argparse
import copy
import importlib.util
import json
import sys
from pathlib import Path


class PlanBuildError(Exception):
    def __init__(self, reason, detail=None):
        super().__init__(reason)
        self.reason = reason
        self.detail = detail


def require(condition, reason, detail=None):
    if not condition:
        raise PlanBuildError(reason, detail)


def load(path):
    return json.loads(Path(path).read_text())


def load_cg09_verifier(path):
    spec = importlib.util.spec_from_file_location("cg09_verify_strategy", path)
    require(spec is not None and spec.loader is not None, "CG09_VERIFIER_LOAD_FAILED")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def template_by_strategy(protocol, strategy):
    matches = [item for item in protocol.get("strategyPlans", []) if item.get("strategy") == strategy]
    require(len(matches) <= 1, "AMBIGUOUS_PLAN_TEMPLATE", {"strategy": strategy})
    return matches[0] if matches else None


def validate_planning_evidence(strategy, evidence):
    require(isinstance(evidence, dict), "PLANNING_EVIDENCE_INVALID")
    if strategy == "HYBRID_RECURRENCE_GUARD":
        behavior = evidence.get("authoritativeBehavioralProof", {})
        mutation = evidence.get("recurrenceMutationControl", {})
        detector = evidence.get("advisoryStaticDetector", {})
        require(behavior.get("present") is True, "AUTHORITATIVE_BEHAVIORAL_PROOF_MISSING")
        require(mutation.get("present") is True, "RECURRENCE_MUTATION_CONTROL_MISSING")
        require(detector.get("present") is True, "ADVISORY_STATIC_DETECTOR_MISSING")
        require(detector.get("authority") == "ADVISORY", "STATIC_DETECTOR_AUTHORITY_INVALID")
    elif strategy == "AUTHORITY_RECONCILIATION":
        inventory = evidence.get("authoritativeInventory", {})
        surfaces = evidence.get("reconciliationSurfaces")
        negatives = evidence.get("negativeIntegrityControls")
        growth = evidence.get("validGrowthControl", {})
        require(inventory.get("present") is True and inventory.get("identity"), "AUTHORITATIVE_INVENTORY_MISSING")
        require(isinstance(surfaces, list) and surfaces, "RECONCILIATION_SURFACES_MISSING")
        require(isinstance(negatives, list) and negatives, "NEGATIVE_INTEGRITY_CONTROLS_MISSING")
        require(growth.get("present") is True, "VALID_GROWTH_CONTROL_MISSING")


def build_plan(protocol, cg09_protocol, cg09_cases, cg09_module, plan_input):
    case_id = plan_input.get("caseId")
    require(isinstance(case_id, str) and case_id, "PLAN_CASE_ID_INVALID")

    if "cg09CaseId" in plan_input:
        cg09_id = plan_input["cg09CaseId"]
        require(cg09_id in cg09_cases, "CG09_CASE_NOT_FOUND", {"caseId": cg09_id})
        case = copy.deepcopy(cg09_cases[cg09_id])
    else:
        case = copy.deepcopy(plan_input.get("inlineCg09Case"))
        require(isinstance(case, dict), "INLINE_CG09_CASE_MISSING")

    require(case.get("caseId") == case_id, "CASE_ID_BINDING_MISMATCH", {
        "planCaseId": case_id,
        "strategyCaseId": case.get("caseId"),
    })

    strategy = cg09_module.select_strategy(cg09_protocol, case)
    constraints = protocol["globalPlanConstraints"]
    planning_evidence = copy.deepcopy(plan_input.get("planningEvidence", {}))

    common = {
        "caseId": case_id,
        "strategy": strategy,
        "executionAuthorization": constraints["executionAuthorization"],
        "executionAuthorized": False,
        "consumerAdoption": constraints["consumerAdoption"],
        "scope": copy.deepcopy(case.get("scope")),
        "sourceRefs": copy.deepcopy(case.get("sourceRefs", [])),
        "automaticChangeTargets": [],
        "requestedMutationClasses": [],
        "futureChangeIntent": copy.deepcopy(plan_input.get("futureChangeIntent", [])),
        "planningEvidence": planning_evidence,
    }

    if strategy == cg09_protocol["fallbackStrategy"]:
        missing = planning_evidence.get("missingEvidence")
        require(isinstance(missing, list) and missing, "FALLBACK_MISSING_EVIDENCE_REASONS_REQUIRED")
        fallback = protocol["fallback"]
        common.update({
            "planKind": fallback["planKind"],
            "disposition": fallback["disposition"],
            "futureAllowedChangeClasses": [],
            "controls": {},
            "verificationClasses": [],
            "forbiddenIntents": [],
            "missingEvidence": copy.deepcopy(missing),
        })
        return common

    template = template_by_strategy(protocol, strategy)
    require(template is not None, "PLAN_TEMPLATE_NOT_FOUND", {"strategy": strategy})
    validate_planning_evidence(strategy, planning_evidence)
    common.update({
        "planKind": template["planKind"],
        "disposition": constraints["positivePlanDisposition"],
        "futureAllowedChangeClasses": copy.deepcopy(template["allowedFutureChangeClasses"]),
        "controls": copy.deepcopy(template["requiredControls"]),
        "verificationClasses": copy.deepcopy(template["requiredVerificationClasses"]),
        "forbiddenIntents": copy.deepcopy(template["forbiddenIntents"]),
        "missingEvidence": [],
    })
    return common


def parse_args():
    parser = argparse.ArgumentParser(description="CG-10 deterministic prevention plan builder")
    parser.add_argument("--protocol", required=True)
    parser.add_argument("--inputs", required=True)
    parser.add_argument("--cg09-protocol", required=True)
    parser.add_argument("--cg09-cases", required=True)
    parser.add_argument("--cg09-verifier", required=True)
    return parser.parse_args()


def main():
    args = parse_args()
    try:
        protocol = load(args.protocol)
        inputs = load(args.inputs)
        cg09_protocol = load(args.cg09_protocol)
        cg09_cases_doc = load(args.cg09_cases)
        cg09_module = load_cg09_verifier(args.cg09_verifier)

        require(protocol.get("schemaVersion") == 1, "PROTOCOL_SCHEMA_MISMATCH")
        require(protocol.get("id") == "CG10-PREVENTION-PLAN-CONTRACT", "WRONG_PROTOCOL")
        require(protocol.get("status") in {"CANDIDATE", "QUALIFIED"}, "UNEXPECTED_PROTOCOL_STATUS")
        require(inputs.get("schemaVersion") == 1, "INPUT_SCHEMA_MISMATCH")
        require(inputs.get("protocolId") == protocol.get("id"), "INPUT_PROTOCOL_MISMATCH")
        require(cg09_protocol.get("id") == "CG09-EVIDENCE-DRIVEN-PREVENTION-STRATEGY", "WRONG_CG09_PROTOCOL")
        require(cg09_protocol.get("status") == "QUALIFIED", "CG09_NOT_QUALIFIED")
        require(cg09_cases_doc.get("protocolId") == cg09_protocol.get("id"), "CG09_CASE_PROTOCOL_MISMATCH")

        cg09_cases = {case["caseId"]: case for case in cg09_cases_doc.get("cases", [])}
        require(len(cg09_cases) == len(cg09_cases_doc.get("cases", [])), "DUPLICATE_CG09_CASE_ID")

        plans = [
            build_plan(protocol, cg09_protocol, cg09_cases, cg09_module, item)
            for item in inputs.get("planInputs", [])
        ]
        require(plans, "NO_PLAN_INPUTS")
        result = {
            "schemaVersion": 1,
            "protocolId": protocol["id"],
            "sourceStrategyProtocolId": cg09_protocol["id"],
            "status": "BUILT",
            "plans": plans,
        }
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    except PlanBuildError as exc:
        value = {
            "schemaVersion": 1,
            "status": "REJECTED",
            "decision": "PREVENTION_PLAN_NOT_BUILT",
            "reason": exc.reason,
        }
        if exc.detail is not None:
            value["detail"] = exc.detail
        print(json.dumps(value, indent=2, sort_keys=True))
        return 2


if __name__ == "__main__":
    sys.exit(main())
