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

## Notes

- The test reports MongoDB operations as Locust request types named `mongodb`.
- Update/delete operate on IDs created by the same Locust user; this avoids an extra lookup operation and keeps the measured operation isolated.
- Existing `TS_COLLECTION` must already be a native time-series collection with matching `TS_META_FIELD` and `TS_TIME_FIELD` settings, or remove it before changing those settings.
