from decimal import Decimal

from django.test import SimpleTestCase

from Swiftcart.utils import convert_pkr_to_usd_cents, convert_usd_cents_to_pkr


class TestCurrencyHelpers(SimpleTestCase):
    def test_convert_pkr_to_usd_cents_uses_exchange_rate(self):
        self.assertEqual(convert_pkr_to_usd_cents(Decimal("1000.00"), exchange_rate=250), 400)

    def test_convert_usd_cents_to_pkr_uses_exchange_rate(self):
        self.assertEqual(convert_usd_cents_to_pkr(400, exchange_rate=250), Decimal("1000.00"))
