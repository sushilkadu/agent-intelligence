#!/usr/bin/env python3
"""Push a seed list of domains onto the `crawl-queue` SQS queue.

Usage:
    python scripts/load_seed_list.py scripts/seed_domains.txt
    python scripts/load_seed_list.py domains.csv --column domain

Respects the same env-var conventions as the rest of the service:
- `AWS_ENDPOINT_URL` set (e.g. to LocalStack's edge port) targets
  LocalStack; unset, boto3 resolves real AWS endpoints.
- `CRAWL_QUEUE_URL` (or --queue-url) names the target queue -- a
  LocalStack queue URL locally, a real SQS queue URL in production.
  No code change is needed to move from one to the other.

At demo scale this script is exercised against the ~15 domains in
scripts/seed_domains.txt. Scaling the seed list up to the target
500-1000 domains needs no code change, just a bigger input file.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

# Allow running as `python scripts/load_seed_list.py` without installing
# the crawler package.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import boto3

from crawler.config import (
    AWS_REGION,
    CRAWL_QUEUE_URL,
    boto3_client_kwargs,
)


def load_domains(path: Path, column: str | None) -> list[str]:
    """Read domains from a plain-text file (one per line, '#' comments
    ignored) or a CSV file (a named column, default: the first one).
    """
    text = path.read_text()
    if path.suffix.lower() == ".csv" or column:
        reader = csv.DictReader(text.splitlines())
        col = column or (reader.fieldnames[0] if reader.fieldnames else "domain")
        return [row[col].strip() for row in reader if row.get(col, "").strip()]
    return [line.strip() for line in text.splitlines() if line.strip() and not line.startswith("#")]


def get_sqs_client():
    return boto3.client("sqs", **boto3_client_kwargs())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("path", type=Path, help="Text (one domain/line) or CSV file of domains")
    parser.add_argument("--column", default=None, help="CSV column holding the domain (default: first column)")
    parser.add_argument("--queue-url", default=None, help="Override the CRAWL_QUEUE_URL env var")
    args = parser.parse_args(argv)

    queue_url = args.queue_url or CRAWL_QUEUE_URL
    if not queue_url:
        parser.error("no queue URL: pass --queue-url or set the CRAWL_QUEUE_URL env var")

    domains = load_domains(args.path, args.column)
    if not domains:
        print(f"no domains found in {args.path}", file=sys.stderr)
        return 1

    sqs_client = get_sqs_client()
    for domain in domains:
        sqs_client.send_message(QueueUrl=queue_url, MessageBody=json.dumps({"domain": domain}))
        print(f"enqueued {domain}")

    print(f"enqueued {len(domains)} domain(s) onto {queue_url} (region={AWS_REGION})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
