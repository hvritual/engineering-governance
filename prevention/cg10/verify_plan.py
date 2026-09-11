#!/usr/bin/env python3
import argparse
import json
import sys
from pathlib import Path


class VerificationError(Exception):
    def __init__(self, reason, detail=None):
        super().__init__(reason)
        self.reason = reason
        self.detail = detail


def require(condition, reason, detail=None):
    if not condition:
        raise VerificationError(reason, detail)


def load(path):
    return json.loads(Path(path).read_text())


def find_template(protocol, strategy):
    matches = [item for item in protocol.get("strategyPlans", []) if item.get("strategy") == strategy]
    require(len(matches) <= 1, "AMBIGUOUS_PLAN_TEMPLATE", {"strategy": strategy})
    return matches[0] if matches else None


def source_case_for_input(plan_input, cg09_cases):
    if "cg09CaseId" in plan_input:
        case_id = plan_input["cg09CaseId"]
        require(case_id in cg09_cases, "CG09_CASE_NOT_FOUND", {"caseId": case_id})
        return cg09_cases[case_id]
    case = plan_input.get("inlineCg09Case")
    require(isinstance(case, dict), "INLINE_CG09_CASE_MISSING")
    return case


def verify_common(protocol, plan, plan_input, source_case):
    constraints = protocol["globalPlanConstraints"]
    case_id = plan_input["caseId"]
    require(plan.get("caseId") == case_id, "PLAN_CASE_ID_MISMATCH")
    require(source_case.get("caseId") == case_id, "SOURCE_CASE_ID_MISMATCH")
    require(plan.get("executionAuthorization") == constraints["executionAuthorization"], "CG10_EXECUTION_AUTHORIZATION_FORBIDDEN")
    require(plan.get("executionAuthorized") is False, "CG10_EXECUTION_AUTHORIZATION_FORBIDDEN")
    require(plan.get("consumerAdoption") == constraints["consumerAdoption"], "CONSUMER_ADOPTION_IMPLICIT")

    scope = plan.get("scope")
    require(isinstance(scope, dict), "PLAN_SCOPE_INVALID")
    require(scope.get("qualified") == source_case.get("scope", {}).get("qualified"), "QUALIFIED_SCOPE_BINDING_MISMATCH")
    require(scope.get("proposed") == scope.get("qualified"), "APPLICABILITY_SCOPE_EXCEEDED", {
        "qualified": scope.get("qualified"),
        "proposed": scope.get("proposed"),
    })

    require(plan.get("sourceRefs") == source_case.get("sourceRefs", []), "SOURCE_REFERENCE_BINDING_MISMATCH")
    require(plan.get("planningEvidence") == plan_input.get("planningEvidence", {}), "PLANNING_EVIDENCE_BINDING_MISMATCH")
    require(plan.get("futureChangeIntent") == plan_input.get("futureChangeIntent", []), "FUTURE_CHANGE_INTENT_BINDING_MISMATCH")

    automatic = plan.get("automaticChangeTargets")
    require(isinstance(automatic, list), "AUTOMATIC_CHANGE_TARGETS_INVALID")
    require(automatic == [], "CG10_AUTOMATIC_CHANGE_TARGET_FORBIDDEN")

    mutation_classes = plan.get("requestedMutationClasses")
    require(isinstance(mutation_classes, list), "REQUESTED_MUTATION_CLASSES_INVALID")
    forbidden = set(constraints.get("forbiddenMutationClasses", []))
    intersection = sorted(forbidden.intersection(mutation_classes))
    require(not intersection, "CG10_FORBIDDEN_MUTATION_CLASS", {"classes": intersection})
    require(mutation_classes == [], "CG10_EXECUTION_MUTATION_REQUESTED", {"classes": mutation_classes})


def verify_hybrid(protocol, plan):
    template = find_template(protocol, "HYBRID_RECURRENCE_GUARD")
    require(template is not None, "HYBRID_TEMPLATE_MISSING")
    require(plan.get("planKind") == template["planKind"], "PLAN_KIND_MISMATCH")
    require(plan.get("disposition") == protocol["globalPlanConstraints"]["positivePlanDisposition"], "PLAN_DISPOSITION_MISMATCH")
    require(plan.get("futureAllowedChangeClasses") == template["allowedFutureChangeClasses"], "FUTURE_CHANGE_CLASS_DRIFT")
    require(plan.get("verificationClasses") == template["requiredVerificationClasses"], "VERIFICATION_CLASS_DRIFT")
    require(plan.get("forbiddenIntents") == template["forbiddenIntents"], "FORBIDDEN_INTENT_DRIFT")
    require(plan.get("missingEvidence") == [], "QUALIFIED_PLAN_HAS_MISSING_EVIDENCE")

    controls = plan.get("controls", {})
    authority = controls.get("authoritativeProof", {})
    detector = controls.get("staticDetector", {})
    mutation = controls.get("recurrenceMutationControl", {})
    require(authority.get("kind") == "BEHAVIORAL_RUNTIME_REGRESSION", "AUTHORITATIVE_BEHAVIORAL_PROOF_DROPPED")
    require(authority.get("authority") == "AUTHORITATIVE", "AUTHORITATIVE_BEHAVIORAL_PROOF_DROPPED")
    require(authority.get("required") is True, "AUTHORITATIVE_BEHAVIORAL_PROOF_DROPPED")
    require(authority.get("retention") == "MUST_RETAIN", "AUTHORITATIVE_BEHAVIORAL_PROOF_DROPPED")
    require(detector.get("authority") != "AUTHORITATIVE", "STATIC_RULE_PROMOTED_TO_AUTHORITY")
    require(detector.get("authority") == "ADVISORY", "STATIC_DETECTOR_AUTHORITY_INVALID")
    require(detector.get("required") is True, "ADVISORY_STATIC_DETECTOR_MISSING")
    require(detector.get("maySubstituteForRuntimeProof") is False, "STATIC_FINDINGS_AS_RUNTIME_PROOF_FORBIDDEN")
    require(mutation.get("required") is True, "RECURRENCE_MUTATION_CONTROL_MISSING")

    evidence = plan.get("planningEvidence", {})
    require(evidence.get("authoritativeBehavioralProof", {}).get("present") is True, "AUTHORITATIVE_BEHAVIORAL_PROOF_MISSING")
    require(evidence.get("recurrenceMutationControl", {}).get("present") is True, "RECURRENCE_MUTATION_CONTROL_MISSING")
    require(evidence.get("advisoryStaticDetector", {}).get("authority") == "ADVISORY", "STATIC_DETECTOR_AUTHORITY_INVALID")


def verify_authority_reconciliation(protocol, plan):
    template = find_template(protocol, "AUTHORITY_RECONCILIATION")
    require(template is not None, "AUTHORITY_RECONCILIATION_TEMPLATE_MISSING")
    require(plan.get("planKind") == template["planKind"], "PLAN_KIND_MISMATCH")
    require(plan.get("disposition") == protocol["globalPlanConstraints"]["positivePlanDisposition"], "PLAN_DISPOSITION_MISMATCH")
    require(plan.get("futureAllowedChangeClasses") == template["allowedFutureChangeClasses"], "FUTURE_CHANGE_CLASS_DRIFT")
    require(plan.get("verificationClasses") == template["requiredVerificationClasses"], "VERIFICATION_CLASS_DRIFT")
    require(plan.get("forbiddenIntents") == template["forbiddenIntents"], "FORBIDDEN_INTENT_DRIFT")
    require(plan.get("missingEvidence") == [], "QUALIFIED_PLAN_HAS_MISSING_EVIDENCE")

    controls = plan.get("controls", {})
    require(controls.get("deriveExpectedFromAuthority") is True, "AUTHORITY_DERIVATION_REQUIRED")
    require(controls.get("semanticReconciliation") is True, "SEMANTIC_RECONCILIATION_REQUIRED")
    require(controls.get("fixedReplacementCardinality") is False, "FIXED_REPLACEMENT_CARDINALITY_FORBIDDEN")
    require(controls.get("copiedLiteralInventory") is False, "COPIED_LITERAL_INVENTORY_FORBIDDEN")
    require(controls.get("validGrowthControl") is True, "VALID_GROWTH_CONTROL_REQUIRED")
    expected_negatives = sorted(template["requiredControls"]["requiredNegativeControlKinds"])
    actual_negatives = sorted(controls.get("requiredNegativeControlKinds", []))
    require(actual_negatives == expected_negatives, "NEGATIVE_INTEGRITY_MATRIX_INCOMPLETE", {
        "expected": expected_negatives,
        "actual": actual_negatives,
    })

    evidence = plan.get("planningEvidence", {})
    inventory = evidence.get("authoritativeInventory", {})
    require(inventory.get("present") is True and inventory.get("identity"), "AUTHORITATIVE_INVENTORY_MISSING")
    require(isinstance(evidence.get("reconciliationSurfaces"), list) and evidence["reconciliationSurfaces"], "RECONCILIATION_SURFACES_MISSING")
    negative_evidence = sorted(evidence.get("negativeIntegrityControls", []))
    require(negative_evidence == expected_negatives, "NEGATIVE_INTEGRITY_EVIDENCE_INCOMPLETE")
    require(evidence.get("validGrowthControl", {}).get("present") is True, "VALID_GROWTH_CONTROL_REQUIRED")


def verify_fallback(protocol, plan):
    fallback = protocol["fallback"]
    require(plan.get("planKind") == fallback["planKind"], "FALLBACK_PLAN_KIND_MISMATCH")
    require(plan.get("disposition") == fallback["disposition"], "FALLBACK_DISPOSITION_MISMATCH")
    require(plan.get("automaticChangeTargets") == [], "FALLBACK_CHANGE_TARGET_FORBIDDEN")
    require(plan.get("requestedMutationClasses") == [], "FALLBACK_CHANGE_TARGET_FORBIDDEN")
    require(plan.get("futureAllowedChangeClasses") == [], "FALLBACK_CHANGE_TARGET_FORBIDDEN")
    require(plan.get("futureChangeIntent") == [], "FALLBACK_CHANGE_TARGET_FORBIDDEN")
    require(plan.get("controls") == {}, "FALLBACK_CONTROL_FORBIDDEN")
    require(plan.get("verificationClasses") == [], "FALLBACK_VERIFICATION_CLASS_FORBIDDEN")
    missing = plan.get("missingEvidence")
    require(isinstance(missing, list) and missing, "FALLBACK_MISSING_EVIDENCE_REASONS_REQUIRED")


def verify_document(protocol, inputs, cg09_cases_doc, plans_doc):
    require(protocol.get("schemaVersion") == 1, "PROTOCOL_SCHEMA_MISMATCH")
    require(protocol.get("id") == "CG10-PREVENTION-PLAN-CONTRACT", "WRONG_PROTOCOL")
    require(protocol.get("status") in {"CANDIDATE", "QUALIFIED"}, "UNEXPECTED_PROTOCOL_STATUS")
    require(inputs.get("protocolId") == protocol.get("id"), "INPUT_PROTOCOL_MISMATCH")
    require(plans_doc.get("protocolId") == protocol.get("id"), "PLAN_PROTOCOL_MISMATCH")
    require(plans_doc.get("sourceStrategyProtocolId") == "CG09-EVIDENCE-DRIVEN-PREVENTION-STRATEGY", "SOURCE_STRATEGY_PROTOCOL_MISMATCH")
    require(plans_doc.get("status") == "BUILT", "PLAN_DOCUMENT_NOT_BUILT")

    cg09_cases = {case["caseId"]: case for case in cg09_cases_doc.get("cases", [])}
    plan_inputs = inputs.get("planInputs", [])
    plans = plans_doc.get("plans", [])
    require(len(plan_inputs) == len(plans), "PLAN_COUNT_MISMATCH")
    by_case = {plan.get("caseId"): plan for plan in plans}
    require(len(by_case) == len(plans), "DUPLICATE_PLAN_CASE_ID")

    selected_strategies = set()
    real_plan_count = 0
    fallback_count = 0
    for plan_input in plan_inputs:
        case_id = plan_input.get("caseId")
        require(case_id in by_case, "PLAN_MISSING_FOR_INPUT", {"caseId": case_id})
        plan = by_case[case_id]
        source_case = source_case_for_input(plan_input, cg09_cases)
        verify_common(protocol, plan, plan_input, source_case)

        expected_strategy = source_case.get("expectedStrategy")
        require(plan.get("strategy") == expected_strategy, "STRATEGY_BINDING_MISMATCH", {
            "caseId": case_id,
            "expected": expected_strategy,
            "actual": plan.get("strategy"),
        })

        if plan["strategy"] == "HYBRID_RECURRENCE_GUARD":
            verify_hybrid(protocol, plan)
            real_plan_count += 1
            selected_strategies.add(plan["strategy"])
        elif plan["strategy"] == "AUTHORITY_RECONCILIATION":
            verify_authority_reconciliation(protocol, plan)
            real_plan_count += 1
            selected_strategies.add(plan["strategy"])
        elif plan["strategy"] == "NO_AUTOMATIC_CONTROL":
            verify_fallback(protocol, plan)
            fallback_count += 1
        else:
            raise VerificationError("UNKNOWN_PLAN_STRATEGY", {"strategy": plan["strategy"]})

    qualification = protocol["qualification"]
    require(real_plan_count >= qualification["minimumQualifiedRealPlans"], "INSUFFICIENT_QUALIFIED_REAL_PLANS")
    if qualification.get("requireDistinctStrategies"):
        require(len(selected_strategies) >= 2, "INSUFFICIENT_STRATEGY_DIVERSITY")
    if qualification.get("requireFallbackPlan"):
        require(fallback_count >= 1, "FALLBACK_PLAN_MISSING")

    return {
        "schemaVersion": 1,
        "status": "PASS",
        "decision": "BOUNDED_PREVENTION_PLANS_VERIFIED",
        "planCount": len(plans),
        "qualifiedRealPlanCount": real_plan_count,
        "fallbackPlanCount": fallback_count,
        "strategies": sorted(selected_strategies),
        "executionAuthorized": False,
        "consumerAdoption": False,
        "repositoryBlocking": False,
    }


def parse_args():
    parser = argparse.ArgumentParser(description="CG-10 deterministic prevention plan verifier")
    parser.add_argument("--protocol", required=True)
    parser.add_argument("--inputs", required=True)
    parser.add_argument("--cg09-cases", required=True)
    parser.add_argument("--plans", required=True)
    return parser.parse_args()


def main():
    args = parse_args()
    try:
        result = verify_document(
            load(args.protocol),
            load(args.inputs),
            load(args.cg09_cases),
            load(args.plans),
        )
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    except VerificationError as exc:
        value = {
            "schemaVersion": 1,
            "status": "REJECTED",
            "decision": "PREVENTION_PLAN_NOT_VERIFIED",
            "reason": exc.reason,
        }
        if exc.detail is not None:
            value["detail"] = exc.detail
        print(json.dumps(value, indent=2, sort_keys=True))
        return 2


if __name__ == "__main__":
    sys.exit(main())
