from rest_framework.test import APITestCase


class MemberTagAPITestCase(APITestCase):
    def test_create_member_tag(self):
        url = "/api/member_tag/"
        data = {"number": 1, "tag_id": "123ab"}
        response = self.client.post(url, data, format="json")
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["number"], 1)
        self.assertEqual(response.data["tag_id"], "123ab")

    def test_list_member_tags(self):
        url = "/api/member_tag/"
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 0)
