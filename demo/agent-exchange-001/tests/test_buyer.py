import copy
import json
from pathlib import Path
import tempfile
import unittest
import subprocess
import sys
import os

from exchange.buyer import prepare, authorize, review_hash, standing
from exchange.evidence import export_selected
from exchange.kernel import Exchange, ExchangeError


class BuyerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.x = Exchange(Path(self.tmp.name) / 'x')
        self.x.bootstrap(); self.listing = self.x.registry.all()[0]
        self.input = Path(self.tmp.name) / 'input.txt'
        self.input.write_text('{"nonce":"review-fixture"}\npublic digest\n')
        self.plan = prepare(self.x, self.listing['listing_id'], self.input, 60, 1)

    def tearDown(self): self.tmp.cleanup()

    def approve(self): return authorize(self.x, self.plan, review_hash(self.plan))

    def test_owner_review_freezes_job_with_bounded_budget(self):
        approved = self.approve(); state = standing(self.x, approved['agent_identity'])
        self.assertEqual(state['standing'], 'ACTIVE')
        self.assertEqual(state['allowance']['granted'], self.listing['price'])
        a = self.x.read_json('jobs.json')[approved['job_id']]['agreement']
        self.assertEqual(a['seller'], self.plan['worker'])
        self.assertEqual(a['input_sha256'], self.plan['input_sha256'])
        self.assertEqual(a['amount'], self.plan['agreed_price'])

    def test_approval_digest_required(self):
        with self.assertRaises(ValueError): authorize(self.x, self.plan, '0' * 64)
        self.assertEqual(self.x.read_json('jobs.json'), {})

    def test_changed_input_refused_before_authorization(self):
        self.input.write_text('{"nonce":"different"}\nchanged\n')
        with self.assertRaises(ValueError): self.approve()
        self.assertEqual(self.x.read_json('jobs.json'), {})

    def test_changed_offer_or_revoked_worker_refused(self):
        self.x.revoke_seller(self.listing['listing_id'], 'fixture')
        with self.assertRaises(ValueError): self.approve()
        self.assertEqual(self.x.read_json('jobs.json'), {})

    def test_budget_and_expiry_validation(self):
        for budget, hours in ((1, 1), (60, 0), (60, -1), (60, float('nan')), (60, 25)):
            with self.assertRaises(ValueError): prepare(self.x, self.listing['listing_id'], self.input, budget, hours)

    def test_cli_fractional_expiry_is_canonical(self):
        plan = prepare(self.x, self.listing['listing_id'], self.input, 60, 0.5)
        self.assertEqual(plan['hours'], '0.5')
        result = authorize(self.x, plan, review_hash(plan))
        self.assertTrue(result['job_id'])

    def test_actual_cli_review_to_receipt_and_revocation(self):
        env = {**os.environ, 'PYTHONPATH': str(Path(__file__).resolve().parents[1])}
        def call(*args):
            result = subprocess.run([sys.executable, '-m', 'exchange.buyer', '--home', str(self.x.home), *map(str, args)],
                                    env=env, text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            return json.loads(result.stdout)
        review_file = Path(self.tmp.name) / 'review.json'
        review = call('review', '--listing', self.listing['listing_id'], '--input', self.input,
                      '--max-budget', '60', '--hours', '0.5', '--out', review_file)
        approved = call('authorize', '--review', review_file, '--approve-sha256', review['approve_sha256'])
        self.assertEqual(call('status', '--agent', approved['agent_identity'])['standing'], 'ACTIVE')
        self.assertEqual(call('complete', '--job', approved['job_id'])['verdict'], 'accepted')
        self.assertEqual(len(call('evidence', '--job', approved['job_id'], '--out', Path(self.tmp.name)/'selected.json')['jobs']), 1)
        self.assertEqual(call('revoke', '--agent', approved['agent_identity'])['standing'], 'NOT ACTIVE')
        self.assertEqual(call('revoke-worker', '--listing', self.listing['listing_id'])['standing'], 'revoked')

    def test_repeated_approval_is_one_commission(self):
        a = self.approve(); b = self.approve()
        self.assertEqual(a['job_id'], b['job_id']); self.assertTrue(b['replayed'])
        self.assertEqual(len(self.x.read_json('jobs.json')), 1)

    def test_revoked_agent_cannot_receive_new_commission(self):
        a = self.approve(); self.x.cli_ok('revoke', '--caller', 'owner', '--of', a['agent_identity'])
        self.assertEqual(standing(self.x, a['agent_identity'])['standing'], 'NOT ACTIVE')
        with self.assertRaises(ExchangeError) as caught:
            self.x.commission(self.listing, agent_name=a['agent_identity'])
        self.assertIn('MANDATE_REVOKED', str(caught.exception))

    def test_replacement_does_not_inherit_grant(self):
        self.approve(); self.x.cli_ok('init-identity', '--name', 'replacement', '--role', 'agent')
        with self.assertRaises(ExchangeError) as caught:
            self.x.commission(self.listing, agent_name='replacement')
        self.assertIn('MANDATE', str(caught.exception))
        self.assertNotIn(self.x.principal('replacement'), self.x.read_json('allowances.json'))

    def test_selected_evidence_preserves_signed_records_and_sources(self):
        a = self.approve(); job = self.x.read_json('jobs.json')[a['job_id']]
        before = (self.x.chome / 'jobs.json').read_bytes()
        packet = export_selected(self.x, [a['job_id'], a['job_id']])
        self.assertEqual(len(packet['jobs']), 1)
        self.assertEqual(packet['jobs'][0]['agreement']['signature'], job['agreement_signature'])
        self.assertEqual(before, (self.x.chome / 'jobs.json').read_bytes())
        self.assertNotIn('input_path', json.dumps(packet))
