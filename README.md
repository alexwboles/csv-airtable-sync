# CSV-to-Airtable Sync

A dependency-free Python tool for safely validating, mapping, and upserting CSV
records into Airtable. It matches the core requirements of a current Upwork
workflow-automation listing while remaining adaptable to other business data.

## Safety and reliability

- Dry-run is the default; Airtable is changed only with `--commit`.
- Validates the complete input before any network write.
- Rejects missing values, duplicate source keys, bad numeric/boolean values,
  and missing mapped columns with row-level reasons.
- Uses Airtable `performUpsert` to avoid duplicate records.
- Batches at Airtable's 10-record Web API limit.
- Handles rate limiting and keeps the personal access token in an environment
  variable rather than source code or URLs.
- Writes rejected rows to machine-readable JSONL for repair and replay.

## Run the verified sample

```powershell
python sync.py sample_customers.csv
python -m unittest -v
```

The sample contains two valid rows and two intentional rejections. The command
performs no network request unless `--commit` is supplied.

## Connect a client base

1. Copy and edit `sample_config.json` with the client's CSV columns and Airtable
   field names. The Airtable merge field should be a unique value.
2. Create a least-privilege Airtable personal access token with record-write
   access only to the required base.
3. Run and review the dry-run summary and `rejections.jsonl`.
4. After client approval, set `AIRTABLE_TOKEN` and commit:

```powershell
$env:AIRTABLE_TOKEN = "client-provided-token"
python sync.py client.csv --config client_config.json --base-id appXXXXXXXXXXXXXX --table Customers --commit
```

The tool never prints or stores the token. Production acceptance should verify
record counts and representative field values directly in the client's base.
