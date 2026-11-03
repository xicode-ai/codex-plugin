import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / 'plugins' / 'source-business-prd'
SKILL = PLUGIN / 'skills' / 'source-business-prd'
SCRIPT = SKILL / 'scripts' / 'validate_analysis.py'
SPEC = importlib.util.spec_from_file_location('source_business_validator', SCRIPT)
VALIDATOR = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(VALIDATOR)


def minimal(root):
    return {
        'schema_version': '1.0',
        'run': {'id': 'R1', 'system': 'Orders', 'scope': 'cancel only',
                'audience': 'PM', 'generated_at': '2026-11-03T10:00:00+08:00',
                'status': 'draft', 'discovery_complete': False,
                'limitations': ['No runtime or database'], 'next_actions': ['Inspect remaining entries']},
        'repositories': [{'id': 'REPO1', 'root': str(root), 'commit': 'unversioned',
                          'dirty': True}],
        'data_sources': [], 'queries': [],
        'evidence': [{'id': 'E1', 'kind': 'code', 'repo_id': 'REPO1',
                      'path': 'service.py', 'symbol': 'cancel', 'line_start': 1,
                      'line_end': 2, 'sha256': hashlib.sha256(b'def cancel():\n    return "CANCELLED"\n').hexdigest(),
                      'observed': 'cancel returns CANCELLED', 'method': 'local source read',
                      'captured_at': '2026-11-03T10:00:00+08:00'}],
        'claims': [{'id': 'C1', 'kind': 'implementation', 'text': 'Cancel returns CANCELLED',
                    'scope': 'provided function', 'evidence_ids': ['E1'],
                    'counter_evidence_ids': [], 'limitations': ['Persistence not shown']}],
        'scenarios': [{'id': 'S1', 'name': 'Cancel', 'actor': 'Caller, identity unknown',
                       'trigger': 'invoke cancel', 'preconditions': [], 'claim_ids': ['C1'],
                       'steps': [{'text': 'Return result', 'claim_ids': ['C1']}],
                       'states': [], 'exceptions': [], 'handoff_ids': [],
                       'closure': 'boundary', 'outcome': 'result returned',
                       'gap': 'No caller/persistence available'}],
        'handoffs': [],
        'coverage': [{'id': 'V1', 'kind': 'repository', 'subject_id': 'REPO1', 'status': 'in_progress'},
                     {'id': 'V2', 'kind': 'scenario', 'subject_id': 'S1', 'status': 'analyzed'}],
        'documents': [],
    }


class AnalysisValidationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'service.py').write_text('def cancel():\n    return "CANCELLED"\n')
        self.payload = minimal(self.root)

    def errors(self, payload=None, **kwargs):
        return VALIDATOR.validate_analysis(self.payload if payload is None else payload, **kwargs)

    def assertError(self, text, **kwargs):
        self.assertTrue(any(text in error for error in self.errors(**kwargs)), self.errors(**kwargs))

    def test_minimal_draft_and_real_source_snapshot(self):
        self.assertEqual(self.errors(check_sources=True), [])

    def test_implementation_requires_code_evidence(self):
        self.payload['claims'][0]['evidence_ids'] = []
        self.assertError('code evidence')

    def test_hypothesis_requires_rationale_but_not_fabricated_facts(self):
        claim = self.payload['claims'][0]
        claim.update(kind='product_hypothesis', evidence_ids=[])
        self.assertError('rationale')
        claim['rationale'] = 'Possible operational value; needs stakeholder interview'
        self.assertEqual(self.errors(), [])

    def test_unknown_and_duplicate_ids(self):
        self.payload['claims'][0]['evidence_ids'] = ['missing']
        self.assertError('unknown reference')
        self.payload['claims'][0]['id'] = 'E1'
        self.assertError('duplicate id')

    def test_malformed_values_return_errors_instead_of_crashing(self):
        for key in self.payload:
            invalids = (None, 123, True, {}, [None], [{'id': []}])
            if key == 'run':
                invalids = (None, 123, True, [], [None])
            for invalid in invalids:
                payload = copy.deepcopy(self.payload)
                payload[key] = invalid
                with self.subTest(key=key, invalid=invalid):
                    self.assertTrue(self.errors(payload))
        self.assertTrue(self.errors([]))

    def test_wrong_reference_container_and_members(self):
        for invalid in (None, {}, 'E1', [None], [[]], [True]):
            with self.subTest(invalid=invalid):
                self.payload['claims'][0]['evidence_ids'] = invalid
                self.assertTrue(self.errors())

    def test_source_range_hash_and_timestamp_are_validated(self):
        evidence = self.payload['evidence'][0]
        for key, value in [('line_start', True), ('line_end', 0), ('sha256', 'fake'),
                           ('captured_at', '2026-11-03')]:
            old = evidence[key]
            evidence[key] = value
            with self.subTest(key=key):
                self.assertTrue(self.errors())
            evidence[key] = old

    def test_changed_source_and_line_out_of_range_are_reported(self):
        (self.root / 'service.py').write_text('changed\n')
        self.assertError('snapshot differs', check_sources=True)
        self.assertError('line range', check_sources=True)

    def test_relative_and_symlink_source_escape_are_rejected(self):
        self.payload['evidence'][0]['path'] = '../outside.py'
        self.assertError('relative contained path')
        self.payload['evidence'][0]['path'] = 'escape.py'
        with tempfile.TemporaryDirectory() as other:
            outside = Path(other) / 'outside.py'
            outside.write_text('private\n')
            (self.root / 'escape.py').symlink_to(outside)
            self.assertError('escapes root', check_sources=True)

    def test_structural_root_paths_are_portable_between_posix_and_windows(self):
        for root in ('/example/orders', 'C:\\example\\orders'):
            with self.subTest(root=root):
                self.payload['repositories'][0]['root'] = root
                self.assertEqual(self.errors(), [])

    def test_source_checks_require_remapping_foreign_roots(self):
        root = 'C:\\example\\orders' if Path('/example/orders').is_absolute() else '/example/orders'
        self.payload['repositories'][0]['root'] = root
        self.assertError('current platform', check_sources=True)

    def add_database(self):
        self.payload['data_sources'] = [{'id': 'DB1', 'alias': 'orders-readonly',
                                        'environment': 'test', 'schema': 'orders', 'status': 'available'}]
        self.payload['queries'] = [{
            'id': 'Q1', 'source_id': 'DB1', 'sql': 'SELECT status, COUNT(*) FROM orders GROUP BY status',
            'captured_at': '2026-11-03T10:00:00+08:00', 'window': 'all available test data',
            'tenant_filter': 'single tenant fixture', 'soft_delete_filter': 'no deletion column',
            'grain': 'one row per status', 'row_count': 0, 'truncated': False, 'outcome': 'succeeded'}]
        self.payload['evidence'].append({'id': 'E2', 'kind': 'database', 'query_id': 'Q1',
                                         'observed': 'No rows returned', 'method': 'dbhub readonly',
                                         'captured_at': '2026-11-03T10:00:00+08:00'})
        self.payload['claims'].append({'id': 'C2', 'kind': 'data_observation', 'text': 'Query returned zero rows',
                                       'scope': 'test DB at query time', 'evidence_ids': ['E2'],
                                       'counter_evidence_ids': [], 'limitations': ['Not proof of feature absence']})

    def test_database_observation_has_explicit_query_context(self):
        self.add_database()
        self.assertEqual(self.errors(), [])
        del self.payload['queries'][0]['tenant_filter']
        self.assertError('tenant_filter')

    def test_failed_query_cannot_support_observation(self):
        self.add_database()
        self.payload['queries'][0]['outcome'] = 'failed'
        self.assertError('successful query')

    def test_source_code_cannot_support_data_observation(self):
        self.payload['claims'][0]['kind'] = 'data_observation'
        self.assertError('database evidence')

    def add_handoff(self):
        self.payload['handoffs'] = [{
            'id': 'H1', 'scenario_id': 'S1', 'from_repo': 'REPO1', 'to_repo': None,
            'external_name': 'Missing consumer', 'transport': 'event', 'contract': 'cancelled.v1',
            'business_key': 'order_id', 'status': 'one_sided', 'producer_evidence_ids': ['E1'],
            'consumer_evidence_ids': [], 'issue': 'consumer not provided'}]
        self.payload['scenarios'][0]['handoff_ids'] = ['H1']
        self.payload['coverage'].append({'id': 'V3', 'kind': 'handoff', 'subject_id': 'H1', 'status': 'analyzed'})

    def test_one_sided_handoff_requires_boundary_and_matched_needs_both_ends(self):
        self.add_handoff()
        self.assertEqual(self.errors(), [])
        self.payload['scenarios'][0]['closure'] = 'closed'
        self.assertError('unresolved handoff')
        self.payload['handoffs'][0]['status'] = 'matched'
        self.assertError('both endpoints')

    def test_cross_repo_evidence_belongs_to_correct_endpoint(self):
        self.add_handoff()
        self.payload['repositories'].append({'id': 'REPO2', 'root': str(self.root / 'warehouse'),
                                               'commit': 'unversioned', 'dirty': True})
        self.payload['handoffs'][0].update(to_repo='REPO2', status='matched', consumer_evidence_ids=['E1'])
        self.assertError('endpoint repository')

    def test_scenario_and_handoff_must_link_to_each_other(self):
        self.add_handoff()
        self.payload['scenarios'][0]['handoff_ids'] = []
        self.assertError('reciprocal')

    def test_complete_requires_discovery_coverage_and_documents(self):
        self.payload['run']['status'] = 'complete'
        self.assertError('discovery_complete')
        self.assertError('unfinished coverage')
        self.assertError('documents')

    def test_complete_cannot_omit_coverage_or_exclusion_reason(self):
        self.payload['run'].update(status='complete', discovery_complete=True)
        self.payload['coverage'] = []
        self.assertError('missing coverage')
        self.payload['coverage'] = [{'id': 'V1', 'kind': 'repository', 'subject_id': 'REPO1', 'status': 'excluded'}]
        self.assertError('reason')

    def test_complete_scoped_analysis_accepts_explicit_external_boundary(self):
        self.payload['run'].update(status='complete', discovery_complete=True, next_actions=[])
        self.payload['coverage'][0]['status'] = 'analyzed'
        self.payload['documents'] = [{'path': 'README.md', 'claim_ids': ['C1'], 'scenario_ids': ['S1']}]
        docs = self.root / 'docs'
        docs.mkdir()
        (docs / 'README.md').write_text('# Cancel\nclaim:C1 scenario:S1\nExternal persistence is not in scope.\n')
        self.assertEqual(self.errors(check_sources=True, docs_root=docs), [])

    def test_coverage_counts_only_actual_items(self):
        self.payload['coverage'].append({'id': 'V3', 'kind': 'capability', 'subject_id': 'refund',
                                         'status': 'blocked', 'reason': 'no route'})
        counts = VALIDATOR.coverage_summary(self.payload)
        self.assertEqual(counts['analyzed'], 1)
        self.assertEqual(counts['in_progress'], 1)
        self.assertEqual(counts['blocked'], 1)

    def test_nested_scenario_claim_references_are_validated(self):
        self.payload['scenarios'][0]['states'] = [{'object': 'Order', 'from': 'NEW', 'to': 'CANCELLED',
                                                    'claim_ids': ['missing']}]
        self.assertError('unknown reference')

    def test_document_checks_references_links_and_fences(self):
        docs = self.root / 'docs'
        docs.mkdir()
        self.payload['documents'] = [{'path': 'README.md', 'claim_ids': ['C1'], 'scenario_ids': ['S1']}]
        readme = docs / 'README.md'
        readme.write_text('claim:C1 scenario:S1\n[broken](missing.md)\n```mermaid\n')
        self.assertError('local link', docs_root=docs)
        self.assertError('unclosed fence', docs_root=docs)
        readme.write_text('# Cancel\nclaim:C1 scenario:S1\n')
        self.assertEqual(self.errors(docs_root=docs), [])
        readme.write_text('claim:C1 scenario:S1 claim:UNKNOWN\n')
        self.assertError('unknown inline reference', docs_root=docs)

    def test_document_escape_rejected(self):
        self.payload['documents'] = [{'path': '../private.md', 'claim_ids': [], 'scenario_ids': []}]
        self.assertError('relative contained path')

    def test_document_links_accept_balanced_parentheses_and_escaped_destinations(self):
        docs = self.root / 'docs'
        docs.mkdir()
        (docs / 'architecture_(v1).md').write_text('# Diagram\n')
        (docs / 'with space.md').write_text('# Space\n')
        self.payload['documents'] = [{'path': 'README.md', 'claim_ids': ['C1'], 'scenario_ids': ['S1']}]
        for link in ('[diagram](architecture_(v1).md)',
                     '[diagram](architecture_\\(v1\\).md "A title")',
                     '[space](<with space.md>)',
                     '```text\n[example](nonexistent.md)\n```'):
            with self.subTest(link=link):
                (docs / 'README.md').write_text('claim:C1 scenario:S1\n' + link + '\n')
                self.assertEqual(self.errors(docs_root=docs), [])

    def test_errors_do_not_echo_input_values(self):
        self.payload['claims'][0]['id'] = 'SECRET_FAKE_VALUE'
        self.payload['claims'][0]['kind'] = 'SECRET_FAKE_VALUE'
        self.assertNotIn('SECRET_FAKE_VALUE', '\n'.join(self.errors()))

    def test_cli_handles_invalid_json_without_echoing_content(self):
        artifact = self.root / 'bad.json'
        artifact.write_text('{"password": "SECRET_FAKE_VALUE",')
        result = subprocess.run([sys.executable, str(SCRIPT), str(artifact)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertNotIn('SECRET_FAKE_VALUE', result.stdout + result.stderr)

    def test_cli_rejects_nonstandard_json_constants_in_extensions(self):
        artifact = self.root / 'invalid-constant.json'
        for value in ('NaN', 'Infinity', '-Infinity'):
            with self.subTest(value=value):
                artifact.write_text(json.dumps(self.payload)[:-1] + ', "extension": ' + value + '}')
                result = subprocess.run([sys.executable, str(SCRIPT), str(artifact)], capture_output=True, text=True)
                self.assertEqual(result.returncode, 2, result.stdout + result.stderr)

    def test_cli_structural_success_does_not_claim_semantic_validation(self):
        artifact = self.root / 'analysis.json'
        artifact.write_text(json.dumps(self.payload))
        result = subprocess.run([sys.executable, str(SCRIPT), str(artifact), '--check-sources'],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('VALID_STRUCTURAL', result.stdout)


class PackageTests(unittest.TestCase):
    def test_shared_skill_is_self_contained_in_both_packages(self):
        for folder in ('.codex-plugin', '.claude-plugin'):
            manifest = json.loads((PLUGIN / folder / 'plugin.json').read_text())
            self.assertEqual(manifest['name'], 'source-business-prd')
            self.assertTrue((PLUGIN / manifest['skills'] / 'source-business-prd' / 'SKILL.md').is_file())
            self.assertFalse(set(manifest) & {'mcpServers', 'hooks', 'apps'})
        marketplace = json.loads((ROOT / 'marketplace.json').read_text())
        entry = next(item for item in marketplace['plugins'] if item['name'] == 'source-business-prd')
        self.assertEqual((ROOT / entry['source']).resolve(), PLUGIN)

    def test_distributable_example_validates(self):
        example = json.loads((SKILL / 'examples' / 'minimal-analysis.json').read_text())
        self.assertEqual(VALIDATOR.validate_analysis(example), [])


if __name__ == '__main__':
    unittest.main()
