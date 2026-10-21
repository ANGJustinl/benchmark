"""Recompute published aggregates from sanitized derived tables; zero model calls."""
import csv
from collections import Counter
import hashlib
import json
from pathlib import Path

DATA = Path(__file__).resolve().parent / 'data'
SEGMENTS = ('frozen', 'recovery150', 'retry150', 'recovery300', 'uncapped')


def truth(value):
    if value not in ('True', 'False', ''):
        raise ValueError('Invalid nullable Boolean')
    return {'True': True, 'False': False, '': None}[value]


def compute():
    manifest = json.loads((DATA / 'SHA256.json').read_text())
    for name, expected in manifest.items():
        path = DATA / name
        if path.parent != DATA or path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError('Analysis input changed: ' + name)
    expected = json.loads((DATA / 'expected-summary.json').read_text())
    rows = list(csv.DictReader((DATA / 'stages.csv').open()))
    diags = list(csv.DictReader((DATA / 'cutoff-diagnostics.csv').open()))
    keys = [(r['segment'], r['trial_id'], r['phase']) for r in rows]
    if len(set(keys)) != len(keys):
        raise ValueError('Duplicate stage within segment')
    diagnostic_map = {(r['segment'], r['trial_id'], r['phase']): r for r in diags}
    if len(diagnostic_map) != len(diags):
        raise ValueError('Duplicate cutoff diagnosis')
    passed = set()
    summary = []
    for segment in SEGMENTS:
        group = [r for r in rows if r['segment'] == segment]
        counts = Counter(r['status'] for r in group)
        for r in group:
            key = (r['trial_id'], r['phase'])
            if key in passed:
                raise ValueError('Already-passed phase invoked again')
            if truth(r['success']):
                passed.add(key)
        summary.append({'segment': segment, 'stages': len(group), 'passed': counts['passed'],
                        'budget_stopped': counts['budget_exhausted'], 'infra_failed': counts['infra_error'],
                        'known_response_tokens': sum(int(r['known_total_tokens']) for r in group)})
    for r in rows:
        if int(r['known_input_tokens']) + int(r['known_output_tokens']) != int(r['known_total_tokens']):
            raise ValueError('Inconsistent Token total')
        if int(r['known_cached_input_tokens']) > int(r['known_input_tokens']):
            raise ValueError('Cached input exceeds input')
    for observed, reference in zip(summary, expected['segment_summaries']):
        for key, original in [('stages', 'model_stages_with_records'), ('passed', 'passed'),
                              ('budget_stopped', 'budget_stopped'), ('infra_failed', 'infrastructure_failed'),
                              ('known_response_tokens', 'known_total_tokens')]:
            if observed[key] != reference[original]:
                raise ValueError('Published segment mismatch: ' + observed['segment'])
    budgets = [r for r in rows if r['status'] == 'budget_exhausted']
    if {(r['segment'], r['trial_id'], r['phase']) for r in budgets} != diagnostic_map.keys():
        raise ValueError('Missing or unexpected cutoff diagnoses')
    for r in diags:
        if not truth(r['base_passed']) or int(r['passed']) != int(r['observations']):
            raise ValueError('Cutoff diagnosis does not pass completely')
    resumed = [r for r in rows if truth(r['resumed_from_saved_code'])]
    frozen = [r for r in rows if r['segment'] == 'frozen']
    frozen_functional = sum(truth(r['base_passed']) is True or
                            truth(diagnostic_map.get(('frozen', r['trial_id'], r['phase']), {}).get('base_passed', '')) is True
                            for r in frozen)
    arms = [{'condition': c, 'initial_completed': sum(truth(r['success']) is True for r in frozen if r['condition'] == c and r['phase'] == 'initial'),
             'initial_skill_loaded': sum(truth(r['initial_skill_loaded']) is True for r in frozen if r['condition'] == c and r['phase'] == 'initial')}
            for c in 'ABC']
    totals = {'recorded_stage_executions': len(rows), 'unique_passed_phases': len(passed),
              'completed_response_receipts': sum(int(r['completed_response_receipts']) for r in rows),
              'known_response_tokens': sum(int(r['known_total_tokens']) for r in rows),
              'cutoff_events': len(budgets), 'cutoff_contract_passed': len(diags),
              'frozen_invoked_artifacts_base_passed': frozen_functional,
              'saved_code_resumptions': len(resumed),
              'resumptions_production_python_unchanged': sum(truth(r['production_py_unchanged']) is True for r in resumed),
              'resumption_known_response_tokens': sum(int(r['known_total_tokens']) for r in resumed)}
    checks = [(totals['known_response_tokens'], expected['totals_recorded']['known_total_tokens']),
              (totals['unique_passed_phases'], expected['data_quality']['final_unique_passed_phases']),
              (totals['completed_response_receipts'], expected['totals_recorded']['completed_response_receipts']),
              (totals['frozen_invoked_artifacts_base_passed'], expected['original_functional_artifacts']['base_contract_passed']),
              (totals['resumptions_production_python_unchanged'], expected['saved_code_resume']['production_py_byte_identical']),
              (totals['resumption_known_response_tokens'], expected['saved_code_resume']['known_response_tokens'])]
    for observed, reference in checks:
        if observed != reference:
            raise ValueError('Published aggregate mismatch')
    for arm, reference in zip(arms, expected['frozen_arms']):
        if arm['initial_completed'] != reference['initial_passed'] or arm['initial_skill_loaded'] != reference['initial_skill_loaded']:
            raise ValueError('Original arm mismatch')
    return {'evidence_kind': 'aggregation_of_sanitized_derived_observations', 'model_calls': 0,
            'segments': summary, 'original_initial_arms': arms, 'totals': totals,
            'unreceipted_inflight_starts': expected['unrecorded_inflight_starts'],
            'full_all_attempt_usage': None, 'monetary_cost': None, 'all_checks_passed': True}


if __name__ == '__main__':
    print(json.dumps(compute(), indent=2))
