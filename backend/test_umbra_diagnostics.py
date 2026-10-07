import json
import unittest
from unittest.mock import Mock

from backend.umbra_broker import receipt, OrderSubmissionError, ShareConnectBT
from backend.test_umbra_execution import ExecutionTests
from backend import strategies as s


class DiagnosticTests(unittest.TestCase):
    def test_full_response_retained_with_authentication_redacted(self):
        broker = object.__new__(ShareConnectBT)
        broker.customer_id, broker.login_id = '206577', 'AURELPD'
        broker._response_secrets = ['private-token']
        broker.client = Mock()
        broker.client.placeOrder.return_value = json.dumps({'status': 200, 'data': {
            'orderId': '123', 'rmsCode': 'NC', 'channelUser': 'AURELPD', 'price': '1215.8',
            'nested': {'access_token': 'private-token', 'note': 'private-token'}}})
        result = broker.place('AAA', 123, 'SELL', 4, '1215.8', stop_price='1227.9', target_price='1130.7')
        saved = result['broker_response']['data']
        self.assertEqual(saved['channelUser'], 'AURELPD')
        self.assertEqual(saved['price'], '1215.8')
        self.assertNotIn('private-token', json.dumps(result))

    def test_known_permission_rejection_retains_full_response(self):
        broker = object.__new__(ShareConnectBT)
        broker.customer_id, broker.login_id = '206577', 'AURELPD'
        broker.client = Mock()
        response = {'status': 400, 'message': "User don't have rights for BIGTRADEPLUS"}
        broker.client.placeOrder.return_value = response
        with self.assertRaises(OrderSubmissionError) as result:
            broker.place('AAA', 123, 'BUY', 1, '100', stop_price='99', target_price='107')
        self.assertTrue(result.exception.rejected)
        self.assertEqual(result.exception.diagnostic['response'], response)

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

    def test_request_is_durable_before_submission_and_settings_can_be_edited_after_attention(self):
        from backend.umbra_broker import limit_payload
        self.broker.prepare_payload = lambda *args, **kwargs: limit_payload('206577', 'AURELPD', *args, **kwargs)
        def place(*args, **kwargs):
            saved = s.all_runs()[0]['orders'][0]['broker_request']
            self.assertEqual(saved, kwargs['prepared_payload'])
            self.assertEqual(saved['channelUser'], 'AURELPD')
            raise OrderSubmissionError({'message': 'Missing receipt'})
        self.broker.place = Mock(side_effect=place)
        self.tick()
        s.store_control({'enabled': False})
        s.save_settings(s.UmbraSettings(order_value='1000', entry_time='10:30'))
        self.assertEqual(s.load_settings().entry_time, '10:30')
        self.assertEqual(s.all_runs()[0]['state'], 'attention')

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
