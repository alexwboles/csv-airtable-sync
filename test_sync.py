import io
import json
import tempfile
import unittest
from pathlib import Path

from sync import AirtableClient, batches, prepare_rows, write_rejections


CONFIG = {
    "key_column": "id",
    "key_field": "External ID",
    "field_mapping": {"id": "External ID", "name": "Name", "amount": "Amount"},
    "required": ["id", "name"],
    "types": {"amount": "number"},
}


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return json.dumps(self.payload).encode()


class SyncTests(unittest.TestCase):
    def test_validation_mapping_types_and_rejections(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "input.csv"
            path.write_text(
                "id,name,amount\nA,Alpha,10.5\nB,,2\nA,Duplicate,3\nC,Charlie,not-a-number\n",
                encoding="utf-8",
            )
            records, rejected = prepare_rows(path, CONFIG)
            self.assertEqual(records, [{"fields": {"External ID": "A", "Name": "Alpha", "Amount": 10.5}}])
            self.assertEqual(len(rejected), 3)
            self.assertIn("missing required", rejected[0].reason)
            self.assertIn("duplicate key", rejected[1].reason)
            self.assertIn("invalid literal", rejected[2].reason)

    def test_missing_mapped_column_fails_before_upload(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "input.csv"
            path.write_text("id,name\nA,Alpha\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "missing mapped columns"):
                prepare_rows(path, CONFIG)

    def test_batches_respect_airtable_limit(self):
        groups = list(batches([{"fields": {"n": i}} for i in range(23)]))
        self.assertEqual([len(group) for group in groups], [10, 10, 3])

    def test_client_uses_patch_upsert_and_batches(self):
        requests = []

        def opener(request, timeout):
            requests.append(request)
            return FakeResponse({"records": []})

        records = [{"fields": {"External ID": str(index)}} for index in range(11)]
        client = AirtableClient("secret", "app 1", "Customers", opener=opener)
        client.upsert(records, "External ID")
        self.assertEqual(len(requests), 2)
        self.assertEqual(requests[0].method, "PATCH")
        payload = json.loads(requests[0].data)
        self.assertEqual(len(payload["records"]), 10)
        self.assertEqual(payload["performUpsert"]["fieldsToMergeOn"], ["External ID"])
        self.assertNotIn("secret", requests[0].full_url)

    def test_rejections_are_machine_readable(self):
        with tempfile.TemporaryDirectory() as directory:
            csv_path = Path(directory) / "input.csv"
            output_path = Path(directory) / "rejected.jsonl"
            csv_path.write_text("id,name,amount\nA,,1\n", encoding="utf-8")
            _, rejected = prepare_rows(csv_path, CONFIG)
            write_rejections(output_path, rejected)
            event = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual(event["row_number"], 2)
            self.assertIn("missing required", event["reason"])


if __name__ == "__main__":
    unittest.main()
