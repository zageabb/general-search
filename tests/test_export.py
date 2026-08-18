import unittest

from app import app


class ExportTest(unittest.TestCase):
    def setUp(self):
        self.client = app.test_client()

    def test_exports_complete_chat_as_markdown(self):
        response = self.client.post("/api/export", json={
            "title": "Climate research",
            "document_names": ["brief.pdf"],
            "messages": [
                {"role": "user", "content": "What changed?", "attachments": ["brief.pdf"]},
                {"role": "assistant", "content": "**A useful answer.**", "sources": [
                    {"title": "Example source", "url": "https://example.com/report"}
                ]},
            ],
        })

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, "text/markdown")
        self.assertIn("Climate research.md", response.headers["Content-Disposition"])
        markdown = response.get_data(as_text=True)
        self.assertIn("# Climate research", markdown)
        self.assertIn("**Documents in context:** brief.pdf", markdown)
        self.assertIn("## You\n\nWhat changed?", markdown)
        self.assertIn("## General Search\n\n**A useful answer.**", markdown)
        self.assertIn("[Example source](https://example.com/report)", markdown)

    def test_rejects_empty_chat(self):
        response = self.client.post("/api/export", json={"messages": []})

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["message"], "Start a chat before saving it.")

    def test_ignores_invalid_messages_and_sources(self):
        response = self.client.post("/api/export", json={
            "messages": [
                {"role": "system", "content": "hidden"},
                {"role": "user", "content": "Hello", "sources": [None, {"title": "No URL"}]},
            ]
        })

        self.assertEqual(response.status_code, 200)
        markdown = response.get_data(as_text=True)
        self.assertNotIn("hidden", markdown)
        self.assertNotIn("### Sources", markdown)
