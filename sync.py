"""Validated CSV-to-Airtable upsert tool with a safe dry-run default."""

from __future__ import annotations

import argparse
import csv
import json
import os
import random
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Callable, Iterable


@dataclass(frozen=True)
class Rejection:
    row_number: int
    reason: str
    row: dict[str, str]


def convert(value: str, kind: str):
    value = value.strip()
    if kind == "string":
        return value
    if kind == "number":
        return float(value) if "." in value else int(value)
    if kind == "boolean":
        normalized = value.lower()
        if normalized in {"true", "yes", "1"}:
            return True
        if normalized in {"false", "no", "0"}:
            return False
        raise ValueError("expected true/false, yes/no, or 1/0")
    raise ValueError(f"unsupported type: {kind}")


def prepare_rows(csv_path: Path, config: dict) -> tuple[list[dict], list[Rejection]]:
    mapping: dict[str, str] = config["field_mapping"]
    required = set(config.get("required", []))
    types: dict[str, str] = config.get("types", {})
    source_key = config["key_column"]
    seen_keys: set[str] = set()
    records: list[dict] = []
    rejected: list[Rejection] = []

    with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        missing_columns = set(mapping) - set(reader.fieldnames or [])
        if missing_columns:
            raise ValueError(f"CSV is missing mapped columns: {', '.join(sorted(missing_columns))}")

        for row_number, row in enumerate(reader, start=2):
            try:
                empty_required = [name for name in required if not (row.get(name) or "").strip()]
                if empty_required:
                    raise ValueError(f"missing required value: {', '.join(sorted(empty_required))}")

                key = (row.get(source_key) or "").strip()
                if not key:
                    raise ValueError(f"missing key value: {source_key}")
                if key in seen_keys:
                    raise ValueError(f"duplicate key in CSV: {key}")

                fields = {}
                for source, destination in mapping.items():
                    raw = row.get(source, "")
                    if not raw.strip() and source not in required:
                        continue
                    fields[destination] = convert(raw, types.get(source, "string"))
                seen_keys.add(key)
                records.append({"fields": fields})
            except (ValueError, TypeError) as exc:
                rejected.append(Rejection(row_number, str(exc), dict(row)))

    return records, rejected


def batches(items: list[dict], size: int = 10) -> Iterable[list[dict]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


class AirtableClient:
    def __init__(
        self,
        token: str,
        base_id: str,
        table: str,
        opener: Callable = urllib.request.urlopen,
        sleeper: Callable[[float], None] = time.sleep,
    ):
        self.token = token
        self.base_id = base_id
        self.table = table
        self.opener = opener
        self.sleeper = sleeper

    @property
    def endpoint(self) -> str:
        return (
            "https://api.airtable.com/v0/"
            + urllib.parse.quote(self.base_id, safe="")
            + "/"
            + urllib.parse.quote(self.table, safe="")
        )

    def upsert(self, records: list[dict], merge_field: str) -> list[dict]:
        results: list[dict] = []
        for group in batches(records, 10):
            payload = {
                "records": group,
                "performUpsert": {"fieldsToMergeOn": [merge_field]},
                "typecast": True,
            }
            results.append(self._request(payload))
        return results

    def _request(self, payload: dict) -> dict:
        body = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            self.endpoint,
            data=body,
            method="PATCH",
            headers={
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
            },
        )
        for attempt in range(5):
            try:
                with self.opener(request, timeout=30) as response:
                    return json.loads(response.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                if exc.code != 429 or attempt == 4:
                    detail = exc.read().decode("utf-8", errors="replace")
                    raise RuntimeError(f"Airtable API error {exc.code}: {detail}") from exc
                retry_after = float(exc.headers.get("Retry-After", "30"))
                self.sleeper(retry_after + random.uniform(0, 0.25))
        raise AssertionError("unreachable")


def write_rejections(path: Path, rejected: list[Rejection]) -> None:
    path.write_text(
        "\n".join(json.dumps(asdict(item), sort_keys=True) for item in rejected)
        + ("\n" if rejected else ""),
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate and upsert CSV rows into Airtable")
    parser.add_argument("csv", type=Path)
    parser.add_argument("--config", type=Path, default=Path("sample_config.json"))
    parser.add_argument("--rejections", type=Path, default=Path("rejections.jsonl"))
    parser.add_argument("--commit", action="store_true", help="Write to Airtable; default is dry-run")
    parser.add_argument("--base-id")
    parser.add_argument("--table")
    args = parser.parse_args()

    config = json.loads(args.config.read_text(encoding="utf-8"))
    records, rejected = prepare_rows(args.csv, config)
    write_rejections(args.rejections, rejected)
    summary = {"valid": len(records), "rejected": len(rejected), "committed": False}

    if args.commit:
        token = os.environ.get("AIRTABLE_TOKEN")
        if not token or not args.base_id or not args.table:
            raise SystemExit("--commit requires AIRTABLE_TOKEN, --base-id, and --table")
        AirtableClient(token, args.base_id, args.table).upsert(records, config["key_field"])
        summary["committed"] = True

    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
