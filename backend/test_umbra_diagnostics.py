import json
import unittest
from unittest.mock import Mock

from backend.umbra_broker import receipt, OrderSubmissionError, ShareConnectBT
from backend.test_umbra_execution import ExecutionTests
from backend import strategies as s


class DiagnosticTests(unittest.TestCase):
    def test_receipt_variants(self):
        for key in ('rmsCode', 'rmscode'):
            self.assertEqual(receipt(json.dumps({'status': 200, 'data': {'orderId': '123', key: 'RMS'}})),
                             {'order_id': '123', 'rms_code': 'RMS'})

    def test_uncertain_responses(self):
        for response in ('not json', None, {'status': 500, 'message': 'Internal error'},
                         {'status': 200, 'data': {'orderId': '123'}},
                         {'status': 200, 'data': {'orderStatus': 'Rejected', 'orderId': '123'}},
                         {'status': 200, 'data': [{'orderId': '123'}]}):
            with self.subTest(response=response), self.assertRaises(OrderSubmissionError) as result:
                receipt(response)
            self.assertFalse(result.exception.rejected)

    def test_explicit_rejection_and_redaction(self):
        with self.assertRaises(OrderSubmissionError) as result:
            receipt({'status': 200, 'data': {'orderStatus': 'Rejected', 'errorCode': 'E42',
                    'errorMsg': 'Invalid product for account-123 token-secret https://broker/?token=abc',
                    'access_token': 'never-store-this'}}, secrets=['account-123', 'token-secret'])
        self.assertTrue(result.exception.rejected)
        diagnostic = json.dumps(result.exception.diagnostic)
        for secret in ('account-123', 'token-secret', 'never-store-this', 'token=abc'):
            self.assertNotIn(secret, diagnostic)
        self.assertEqual(result.exception.diagnostic['errorCode'], 'E42')

    def test_transport_exception_does_not_leak_or_retry(self):
        broker = object.__new__(ShareConnectBT)
        broker.customer_id, broker.login_id = 'a', 'b'
        broker.client = Mock()
        broker.client.placeOrder.side_effect = TimeoutError('secret URL and credentials')
        with self.assertRaises(OrderSubmissionError) as result:
            broker.place('AAA', 123, 'BUY', 1, '100', stop_price='99', target_price='107')
        self.assertFalse(result.exception.rejected)
        self.assertNotIn('credentials', str(result.exception))
        broker.client.placeOrder.assert_called_once()


class DiagnosticExecutionTests(unittest.TestCase):
    setUp = ExecutionTests.setUp
    tick = ExecutionTests.tick

    def test_rejection_persisted_without_retry(self):
        self.broker.place = Mock(side_effect=OrderSubmissionError({'message': 'Invalid product', 'code': 'E42'}, rejected=True))
        self.tick(); self.tick()
        run = s.all_runs()[0]
        self.assertEqual(run['orders'][0]['state'], 'rejected')
        self.assertEqual(run['orders'][0]['broker_diagnostic']['code'], 'E42')
        self.broker.place.assert_called_once()

    def test_uncertainty_persisted_and_pauses(self):
        self.broker.place = Mock(side_effect=OrderSubmissionError({'message': 'Missing receipt', 'stage': 'response'}))
        self.tick(); self.tick()
        run = s.all_runs()[0]
        self.assertEqual(run['state'], 'attention')
        self.assertIn('Missing receipt', run['orders'][0]['message'])
        self.broker.place.assert_called_once()
