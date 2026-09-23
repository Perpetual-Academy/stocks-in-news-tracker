import unittest
from decimal import Decimal
from unittest.mock import Mock
from backend.umbra_broker import bracket_prices, limit_payload, ShareConnectBT
from backend import test_umbra_execution as execution
from backend import strategies as s


class BracketTests(unittest.TestCase):
    def test_approved_seven_percent_default_for_legacy_settings(self):
        settings = s.UmbraSettings.model_validate({'order_value': '5000', 'entry_time': '10:35'})
        self.assertEqual(settings.profit_target_percent, Decimal('7'))
        self.assertEqual(bracket_prices('100', 'BUY', settings.profit_target_percent, '.05'), (Decimal('99'), Decimal('107')))
        self.assertEqual(bracket_prices('100', 'SELL', settings.profit_target_percent, '.05'), (Decimal('101'), Decimal('93')))

    def test_buy_and_sell_levels_match_one_percent_stop(self):
        self.assertEqual(bracket_prices('100', 'BUY', '2', '.05'), (Decimal('99'), Decimal('102')))
        self.assertEqual(bracket_prices('100', 'SELL', '2', '.05'), (Decimal('101'), Decimal('98')))

    def test_rounding_and_missing_tick(self):
        stop, target = bracket_prices('101.05', 'BUY', '2', '.05')
        self.assertEqual((stop, target), (Decimal('100.05'), Decimal('103.05')))
        for tick in [None, '0', 'NaN']:
            with self.subTest(tick=tick), self.assertRaises(ValueError):
                bracket_prices('100', 'BUY', '2', tick)
        with self.assertRaises(ValueError):
            bracket_prices('100', 'BUY', None, '.05')

    def test_user_example_payload_structure(self):
        p = limit_payload('account', 'login', 'SBIN', 123, 'BUY', 1, '590', stop_price='570', target_price='620')
        self.assertEqual((p['productType'], p['orderType']), ('BIGTRADEPLUS', 'BKT'))
        self.assertEqual((p['price'], p['childSlPrice'], p['bookProfitPrice']), ('590', '570', '620'))
        for side in ['BUY', 'SELL']:
            with self.assertRaises(ValueError):
                limit_payload('a', 'l', 'S', 1, side, 1, '100', stop_price='100', target_price='102')

    def test_opposing_bt_and_btplus_positions_do_not_cancel_safety_check(self):
        b = object.__new__(ShareConnectBT)
        b.positions = Mock(return_value=[dict(tradingSymbol='AAA', exchange='NC', productType=p, netQty=q)
                                        for p,q in [('BIGTRADE', 4), ('BIGTRADEPLUS', -4)]])
        self.assertEqual(b.net_position('AAA'), 8)


class BracketExecutionTests(unittest.TestCase):
    setUp = execution.ExecutionTests.setUp
    tick = execution.ExecutionTests.tick

    def test_explicitly_cleared_target_never_submits(self):
        with s.database() as db:
            db.execute("UPDATE settings SET payload=? WHERE id='umbra'", (s.UmbraSettings(order_value='1000', entry_time='09:30', profit_target_percent=None).model_dump_json(),))
        self.tick()
        self.assertEqual(self.broker.calls, [])

    def test_saved_bracket_levels(self):
        self.tick()
        order = s.all_runs()[0]['orders'][0]
        self.assertEqual(order['product'], 'BT+')
        self.assertEqual(Decimal(order['stop_price']), Decimal('102.00'))
        self.assertEqual(Decimal(order['target_price']), Decimal('99.00'))
