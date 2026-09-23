# MongoDB Locust stress test

This test exercises:

- CRUD insert/update/delete against a regular collection.
- Inserts into a native MongoDB time-series collection.
- Reads filtered by `metadata.device_id` and a timestamp range, sorted newest-first, using a compound index on metadata plus time.

## Install

```bash
python -m pip install -r requirements.txt
```

## Configure

The recommended configuration uses environment variables:

```bash
export MONGODB_URI='mongodb://localhost:27017'
export MONGODB_DB='stress_test'
```

Useful overrides:

| Variable | Default | Purpose |
|---|---:|---|
| `CRUD_COLLECTION` | `crud_documents` | Regular CRUD collection |
| `TS_COLLECTION` | `sensor_measurements` | Native time-series collection |
| `TS_META_FIELD` | `metadata` | Time-series metadata field |
| `TS_TIME_FIELD` | `timestamp` | Time-series time field |
| `TS_GRANULARITY` | `seconds` | Native time-series granularity |
| `TS_INDEX_NAME` | `metadata_device_timestamp` | Compound index name |
| `TS_READ_WINDOW_SECONDS` | `3600` | Time window for indexed reads |
| `TS_READ_LIMIT` | `100` | Maximum documents returned per read |
| `DEVICE_COUNT` | `1000` | Number of logical devices |
| `PAYLOAD_BYTES` | `256` | CRUD payload size |
| `MAX_LOCAL_CRUD_IDS` | `1000` | Per-user IDs retained for update/delete |

The first run creates the time-series collection if it does not exist and creates this index:

```javascript
{ "metadata.device_id": 1, "timestamp": -1 }
```

## Run

Headless example:

```bash
locust -f locustfile.py \
  --headless \
  --users 100 \
  --spawn-rate 10 \
  --run-time 10m \
  --csv mongodb-stress
```

Web UI example:

```bash
locust -f locustfile.py
```

Then open `http://localhost:8089` and set users, spawn rate, and duration.

The default task distribution is 15% CRUD insert, 15% CRUD update, 10% CRUD delete, 30% time-series insert, and 30% indexed time-series read.

## Stress test details

Each Locust user repeatedly selects one task according to the weights above. Every MongoDB operation is reported as a separate Locust request named `mongodb`, with the operation name identifying the measured workload:

| Operation | Weight | Workload measured |
|---|---:|---|
| `crud_insert` | 15% | Inserts a generated document into the regular CRUD collection. |
| `crud_update` | 15% | Updates a document ID created by the same user. |
| `crud_delete` | 10% | Deletes a document ID created by the same user. |
| `timeseries_insert` | 30% | Inserts a generated measurement into the native time-series collection. |
| `timeseries_index_read` | 30% | Reads measurements for one device over a time range, sorted newest-first. |

CRUD update and delete use IDs retained locally by each user, so they do not add a lookup operation to the measured request. Time-series reads use the compound index on `metadata.device_id` and `timestamp` when the collection is configured with the default field names.

### Short verification test

Use this bounded run to verify the environment and database connection before starting a longer load test:

```bash
.venv/bin/locust -f locustfile.py \
  --headless \
  --users 1 \
  --spawn-rate 1 \
  --run-time 30s
```

The latest verification run completed with 342 requests, 0 failures, and 11.57 requests/sec:

| Operation | Requests | Avg | Median | P95 | Max |
|---|---:|---:|---:|---:|---:|
| CRUD insert | 57 | 20 ms | 11 ms | 99 ms | 158 ms |
| CRUD update | 58 | 29 ms | 12 ms | 140 ms | 306 ms |
| CRUD delete | 35 | 31 ms | 11 ms | 140 ms | 174 ms |
| Time-series insert | 83 | 40 ms | 14 ms | 140 ms | 402 ms |
| Time-series indexed read | 109 | 20 ms | 10 ms | 89 ms | 156 ms |

This is a single-user smoke benchmark. Results vary with MongoDB deployment size, network latency, collection contents, payload size, and the selected user count and run time.

## Notes

- The test reports MongoDB operations as Locust request types named `mongodb`.
- Update/delete operate on IDs created by the same Locust user; this avoids an extra lookup operation and keeps the measured operation isolated.
- Existing `TS_COLLECTION` must already be a native time-series collection with matching `TS_META_FIELD` and `TS_TIME_FIELD` settings, or remove it before changing those settings.
