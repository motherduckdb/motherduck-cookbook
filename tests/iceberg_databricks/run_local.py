"""Run real Iceberg regression tests against the isolated Docker fixture."""
import os
from pathlib import Path
import subprocess
import sys

import boto3
from dotenv import dotenv_values

folder = Path(__file__).resolve().parent
values = dotenv_values(folder / '.env')
key, secret = values['DEV_ACCESS_KEY'], values['DEV_SECRET_KEY']
s3 = boto3.client('s3', endpoint_url='http://localhost:19000',
                  aws_access_key_id=key, aws_secret_access_key=secret)
if 'warehouse' not in [bucket['Name'] for bucket in s3.list_buckets()['Buckets']]:
    s3.create_bucket(Bucket='warehouse')
environment = {**os.environ, 'AWS_ACCESS_KEY_ID': key, 'AWS_SECRET_ACCESS_KEY': secret,
               'ICEBERG_TEST_ENDPOINT': 'http://localhost:18181',
               'ICEBERG_TEST_S3_ENDPOINT': 'localhost:19000'}
raise SystemExit(subprocess.call(
    [sys.executable, '-m', 'pytest', 'tests/test_iceberg_databricks_flights.py', '-q'],
    cwd=folder.parents[1], env=environment,
))
