from django.test import TestCase
from django.urls import reverse


class DemoViewsTest(TestCase):
    def test_home_multiple_returns_200(self):
        response = self.client.get(reverse("demo-home-multiple"))
        self.assertEqual(response.status_code, 200)

    def test_home_offseason_returns_200(self):
        response = self.client.get(reverse("demo-home-offseason"))
        self.assertEqual(response.status_code, 200)

    def test_home_single_returns_200(self):
        response = self.client.get(reverse("demo-home-single"))
        self.assertEqual(response.status_code, 200)

    def test_demo_root_returns_404(self):
        response = self.client.get("/demo/")
        self.assertEqual(response.status_code, 404)

    def test_demo_404_preview_returns_200(self):
        response = self.client.get(reverse("demo-404"))
        self.assertEqual(response.status_code, 200)

    def test_demo_403_preview_returns_200(self):
        response = self.client.get(reverse("demo-403"))
        self.assertEqual(response.status_code, 200)

    def test_demo_500_preview_returns_200(self):
        response = self.client.get(reverse("demo-500"))
        self.assertEqual(response.status_code, 200)
