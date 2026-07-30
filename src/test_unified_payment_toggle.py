from django.test import Client, TestCase, override_settings


class UnifiedPaymentToggleTests(TestCase):
    def setUp(self):
        from wallet.test_support import seed_site_context_fixtures
        seed_site_context_fixtures()
        self.client = Client()

    def test_disabled_by_default_redirects_home(self):
        response = self.client.get('/school/pay/', follow=True)

        self.assertEqual(response.status_code, 200)
        self.assertRedirects(response, '/')

    def test_disabled_by_default_blocks_initialize_endpoint(self):
        response = self.client.post('/school/pay/initialize/', {'payment_data': '{}'})

        self.assertEqual(response.status_code, 503)

    @override_settings(UNIFIED_PAYMENT_ENABLED=True)
    def test_renders_normally_when_enabled(self):
        response = self.client.get('/school/pay/')

        self.assertEqual(response.status_code, 200)
