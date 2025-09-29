from rest_framework.test import APITestCase
from website.models import Tag


class MemberTagAPITestCase(APITestCase):
    def test_create_member_tag(self):
        url = "/api/member_tag/"
        data = {"number": 1, "tag_id": "123ab"}
        response = self.client.post(url, data, format="json")
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["number"], 1)
        self.assertEqual(response.data["tag_id"], "123ab")
        self.assertIsNone(response.data["last_seen_at"])

    def test_list_member_tags(self):
        url = "/api/member_tag/"
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 0)

    def test_touch_member_tag_updates_last_seen(self):
        tag = Tag.objects.create(number=1, tag_id="123ab")

        url = "/api/member_tag/touch/"
        response = self.client.post(url, {"tag_id": tag.tag_id}, format="json")

        self.assertEqual(response.status_code, 200)
        tag.refresh_from_db()
        self.assertIsNotNone(tag.last_seen_at)
        self.assertEqual(response.data["id"], tag.id)
        self.assertEqual(response.data["tag_id"], tag.tag_id)
        self.assertIsNotNone(response.data["last_seen_at"])
