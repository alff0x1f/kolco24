from django.test import SimpleTestCase


class DemoViewsTest(SimpleTestCase):
    def test_home_multiple_returns_200(self):
        response = self.client.get("/demo/home-multiple/")
        self.assertEqual(response.status_code, 200)

    def test_home_offseason_returns_200(self):
        response = self.client.get("/demo/home-offseason/")
        self.assertEqual(response.status_code, 200)

    def test_home_single_returns_200(self):
        response = self.client.get("/demo/home-single/")
        self.assertEqual(response.status_code, 200)

    def test_demo_root_returns_404(self):
        response = self.client.get("/demo/")
        self.assertEqual(response.status_code, 404)
