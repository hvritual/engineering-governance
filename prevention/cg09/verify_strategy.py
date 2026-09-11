#!/usr/bin/env python3
import argparse
import copy
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


def resolve_path(value, dotted_path):
    current = value
    for part in dotted_path.split('.'):
        if not isinstance(current, dict) or part not in current:
            return None, False
        current = current[part]
    return current, True


def condition_matches(case, condition):
    value, present = resolve_path(case, condition['path'])
    if not present:
        return False
    if 'equals' in condition:
        return value == condition['equals']
    raise VerificationError('UNSUPPORTED_STRATEGY_CONDITION', condition)


def apply_global_constraints(case, constraints):
    for constraint in constraints:
        kind = constraint.get('type')
        path = constraint.get('path')
        reason = constraint.get('reason') or 'GLOBAL_CONSTRAINT_FAILED'
        value, present = resolve_path(case, path)
        require(present, 'GLOBAL_CONSTRAINT_PATH_MISSING', {'caseId': case.get('caseId'), 'path': path})
        if kind == 'equals':
            require(value == constraint.get('value'), reason, {
                'caseId': case.get('caseId'),
                'path': path,
                'expected': constraint.get('value'),
                'actual': value,
            })
        elif kind == 'same_as':
            other_path = constraint.get('otherPath')
            other, other_present = resolve_path(case, other_path)
            require(other_present, 'GLOBAL_CONSTRAINT_PATH_MISSING', {
                'caseId': case.get('caseId'), 'path': other_path,
            })
            require(value == other, reason, {
                'caseId': case.get('caseId'),
                'path': path,
                'otherPath': other_path,
                'qualified': other,
                'proposed': value,
            })
        else:
            raise VerificationError('UNSUPPORTED_GLOBAL_CONSTRAINT', constraint)


def validate_strategy_definition(strategy):
    require(isinstance(strategy.get('name'), str) and strategy['name'], 'STRATEGY_NAME_INVALID')
    for key in ('matchAll', 'requiresAll', 'forbids'):
        require(isinstance(strategy.get(key), list), 'STRATEGY_RULES_INVALID', {
            'strategy': strategy.get('name'), 'field': key,
        })
    for condition in strategy['matchAll'] + strategy['requiresAll']:
        require(isinstance(condition.get('path'), str) and condition['path'], 'STRATEGY_CONDITION_PATH_INVALID')
        require('equals' in condition, 'STRATEGY_CONDITION_EQUALS_MISSING', condition)
    for condition in strategy['forbids']:
        require(isinstance(condition.get('path'), str) and condition['path'], 'STRATEGY_FORBID_PATH_INVALID')
        require('equals' in condition, 'STRATEGY_FORBID_EQUALS_MISSING', condition)
        require(isinstance(condition.get('reason'), str) and condition['reason'], 'STRATEGY_FORBID_REASON_MISSING')


def select_strategy(protocol, case):
    apply_global_constraints(case, protocol.get('globalConstraints', []))

    strategies = protocol.get('strategies', [])
    matching = []
    for strategy in strategies:
        if all(condition_matches(case, condition) for condition in strategy['matchAll']):
            matching.append(strategy)

    require(len(matching) <= 1, 'AMBIGUOUS_STRATEGY_MATCH', {
        'caseId': case.get('caseId'),
        'strategies': [strategy['name'] for strategy in matching],
    })

    if not matching:
        return protocol['fallbackStrategy']

    strategy = matching[0]
    for forbidden in strategy['forbids']:
        if condition_matches(case, forbidden):
            raise VerificationError(forbidden['reason'], {
                'caseId': case.get('caseId'),
                'strategy': strategy['name'],
                'path': forbidden['path'],
                'forbiddenValue': forbidden['equals'],
            })

    if not all(condition_matches(case, condition) for condition in strategy['requiresAll']):
        return protocol['fallbackStrategy']

    return strategy['name']


def verify_source_refs(protocol, cases_document):
    protocol_refs = {
        (item.get('path'), item.get('blob'))
        for item in protocol.get('sourceAuthorities', [])
    }
    require(all(path and blob for path, blob in protocol_refs), 'PROTOCOL_SOURCE_AUTHORITY_INVALID')

    seen = set()
    for case in cases_document['cases']:
        refs = case.get('sourceRefs')
        require(isinstance(refs, list) and refs, 'CASE_SOURCE_REFS_MISSING', case.get('caseId'))
        for ref in refs:
            pair = (ref.get('path'), ref.get('blob'))
            require(pair in protocol_refs, 'CASE_SOURCE_REF_NOT_PROTOCOL_AUTHORITY', {
                'caseId': case.get('caseId'), 'sourceRef': ref,
            })
            require(pair not in seen or pair[0].startswith('corpus/'), 'DUPLICATE_NONCORPUS_SOURCE_REF', {
                'caseId': case.get('caseId'), 'sourceRef': ref,
            })
            seen.add(pair)


def verify_documents(protocol, cases_document):
    require(protocol.get('schemaVersion') == 1, 'PROTOCOL_SCHEMA_MISMATCH')
    require(protocol.get('id') == 'CG09-EVIDENCE-DRIVEN-PREVENTION-STRATEGY', 'WRONG_PROTOCOL')
    require(protocol.get('status') in {'CANDIDATE', 'QUALIFIED'}, 'UNEXPECTED_PROTOCOL_STATE')
    require(protocol.get('fallbackStrategy') == 'NO_AUTOMATIC_CONTROL', 'FALLBACK_STRATEGY_MISMATCH')

    ceiling = protocol.get('authorityCeiling', {})
    require(ceiling.get('publishesSelectionContractOnly') is True, 'AUTHORITY_CEILING_NOT_SELECTION_ONLY')
    require(ceiling.get('consumerAdoption') is False, 'AUTHORITY_CEILING_CONSUMER_ADOPTION')
    require(ceiling.get('repositoryBlocking') is False, 'AUTHORITY_CEILING_REPOSITORY_BLOCKING')
    require(ceiling.get('modifiesYunka') is False, 'AUTHORITY_CEILING_YUNKA_MUTATION')
    require(ceiling.get('modifiesConsumerSource') is False, 'AUTHORITY_CEILING_CONSUMER_MUTATION')

    strategies = protocol.get('strategies')
    require(isinstance(strategies, list) and strategies, 'STRATEGIES_MISSING')
    names = [strategy.get('name') for strategy in strategies]
    require(len(names) == len(set(names)), 'DUPLICATE_STRATEGY_NAME')
    require(protocol['fallbackStrategy'] not in set(names), 'FALLBACK_MUST_NOT_BE_EXPLICIT_STRATEGY')
    for strategy in strategies:
        validate_strategy_definition(strategy)

    require(cases_document.get('schemaVersion') == 1, 'CASES_SCHEMA_MISMATCH')
    require(cases_document.get('protocolId') == protocol['id'], 'CASES_PROTOCOL_MISMATCH')
    cases = cases_document.get('cases')
    require(isinstance(cases, list) and cases, 'CASES_MISSING')
    ids = [case.get('caseId') for case in cases]
    require(all(isinstance(case_id, str) and case_id for case_id in ids), 'CASE_ID_INVALID')
    require(len(ids) == len(set(ids)), 'DUPLICATE_CASE_ID')

    verify_source_refs(protocol, cases_document)

    decisions = []
    for case in cases:
        require(case.get('qualifiedRealCase') is True, 'CASE_NOT_QUALIFIED_REAL_PROBLEM', case.get('caseId'))
        selected = select_strategy(protocol, case)
        expected = case.get('expectedStrategy')
        require(expected in set(names) | {protocol['fallbackStrategy']}, 'CASE_EXPECTED_STRATEGY_INVALID', {
            'caseId': case.get('caseId'), 'expectedStrategy': expected,
        })
        require(selected == expected, 'CASE_STRATEGY_MISMATCH', {
            'caseId': case.get('caseId'), 'expected': expected, 'actual': selected,
        })
        decisions.append({'caseId': case['caseId'], 'strategy': selected})

    qualification = protocol.get('crossCaseQualification', {})
    minimum = qualification.get('minimumQualifiedRealCases')
    require(isinstance(minimum, int) and minimum >= 2, 'CROSS_CASE_MINIMUM_INVALID')
    require(len(cases) >= minimum, 'INSUFFICIENT_QUALIFIED_REAL_CASES', {
        'required': minimum, 'actual': len(cases),
    })
    require(qualification.get('noUniversalToolMandate') is True, 'UNIVERSAL_TOOL_MANDATE_NOT_FORBIDDEN')
    require(qualification.get('consumerAdoptionImplicit') is False, 'IMPLICIT_CONSUMER_ADOPTION_ALLOWED')
    require(qualification.get('incompleteEvidenceMustFallBack') is True, 'INCOMPLETE_EVIDENCE_FAIL_CLOSED_MISSING')

    if qualification.get('requireDistinctSelectedStrategies') is True:
        selected_nonfallback = {
            decision['strategy'] for decision in decisions
            if decision['strategy'] != protocol['fallbackStrategy']
        }
        require(len(selected_nonfallback) >= 2, 'CROSS_CASE_STRATEGY_COLLAPSE', {
            'strategies': sorted(selected_nonfallback),
        })

    return {
        'schemaVersion': 1,
        'status': 'PASS',
        'decision': 'EVIDENCE_DRIVEN_PREVENTION_STRATEGY_SELECTION_VERIFIED',
        'fallbackStrategy': protocol['fallbackStrategy'],
        'caseCount': len(cases),
        'decisions': decisions,
        'consumerAdoption': False,
        'repositoryBlocking': False,
    }


def parse_args():
    parser = argparse.ArgumentParser(description='CG-09 evidence-driven prevention strategy verifier')
    parser.add_argument('--protocol', required=True)
    parser.add_argument('--cases', required=True)
    return parser.parse_args()


def main():
    args = parse_args()
    try:
        result = verify_documents(load(args.protocol), load(args.cases))
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    except VerificationError as exc:
        value = {
            'schemaVersion': 1,
            'status': 'REJECTED',
            'decision': 'PREVENTION_STRATEGY_SELECTION_NOT_PROVEN',
            'reason': exc.reason,
            'consumerAdoption': False,
            'repositoryBlocking': False,
        }
        if exc.detail is not None:
            value['detail'] = exc.detail
        print(json.dumps(value, indent=2, sort_keys=True))
        return 2


if __name__ == '__main__':
    sys.exit(main())
