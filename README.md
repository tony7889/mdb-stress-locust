# MongoDB Locust stress test

This test exercises:

- CRUD insert/update/delete/read against a regular collection.
- Inserts into a native MongoDB time-series collection.
- Reads filtered by `metadata.device_id` and a timestamp range, sorted newest-first, using a compound index on metadata plus time.

## Install

Create and activate a project-local virtual environment:

```bash
python3 -m venv .venv
source .venv/bin/activate
```

Upgrade `pip` and install the project dependencies:

```bash
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

Verify the installation:

```bash
python -c "import locust, pymongo, dotenv; print('Dependencies installed')"
locust --version
```

To leave the virtual environment:

```bash
deactivate
```
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

The default task distribution is 10% CRUD insert, 5% CRUD update, 5% CRUD delete, 30% indexed CRUD read, 10% time-series insert, and 40% indexed time-series read.

## Stress test details

Each Locust user repeatedly selects one task according to the weights above. Every MongoDB operation is reported as a separate Locust request named `mongodb`, with the operation name identifying the measured workload:

| Operation | Weight | Workload measured |
|---|---:|---|
| `crud_insert` | 10% | Inserts a generated document into the regular CRUD collection. |
| `crud_update` | 5% | Updates a document ID created by the same user. |
| `crud_delete` | 5% | Deletes a document ID created by the same user. |
| `crud_read` | 30% | Reads a document by its `_id` using the default unique MongoDB index. |
| `timeseries_insert` | 10% | Inserts a generated measurement into the native time-series collection. |
| `timeseries_index_read` | 40% | Reads measurements for one device over a time range, sorted newest-first. |

CRUD update, delete, and read use IDs retained locally by each user, so they do not add a lookup operation to the measured request. Time-series reads use the compound index on `metadata.device_id` and `timestamp` when the collection is configured with the default field names.

### Sample documents

CRUD inserts create documents with a unique UUID string as `_id`:

```javascript
{
  "_id": "<uuid-hex>",
  "tenant_id": 42,
  "status": "queued",
  "value": 637.42,
  "updated_at": ISODate("2026-09-23T12:00:00Z"),
  "payload": "<256 random ASCII characters>"
}
```

CRUD reads retrieve the selected document by `_id`. CRUD updates change `status`, `value`, and `updated_at`, and increment `update_count`. Deletes remove the selected document by `_id`.

Time-series inserts create measurements like this. The actual field names use `TS_META_FIELD` and `TS_TIME_FIELD`:

```javascript
{
  "metadata": {
    "device_id": "device-000042",
    "site": "hk"
  },
  "timestamp": ISODate("2026-09-23T12:00:00Z"),
  "temperature": 24.731,
  "humidity": 61.204,
  "pressure": 1012.876
}
```

### User scheduling and parallelism

- Each Locust user maintains its own task state and selects one task after each wait period; MongoDB client and collection objects are shared within the Locust process.
- The wait period is random between 10 ms and 100 ms after a task completes.
- Task weights are probabilities over time, not a fixed sequence. For example, 100 task selections will trend toward 10 CRUD inserts, 5 CRUD updates, 5 CRUD deletes, 30 CRUD reads, 10 time-series inserts, and 40 time-series reads.
- Users run concurrently under Locust. With `--users 100`, up to 100 users generate traffic in parallel, subject to MongoDB capacity and client/network limits.
- A user executes one task at a time. The MongoDB operation is synchronous for that user, so the next task starts after the current operation and wait period finish.
- Each user's CRUD ID list is private. A user can update or delete only documents inserted by that same user, and the list is limited by `MAX_LOCAL_CRUD_IDS`.
- MongoDB client, collection creation, time-series setup, and index creation are initialized once per Locust process, with a setup lock to avoid duplicate initialization.

### Indexes and query pattern

The regular CRUD collection relies on MongoDB's default unique `_id` index for CRUD reads, updates, and deletes. The test does not create additional CRUD indexes.

For the native time-series collection, the test creates this index using `TS_INDEX_NAME`:

```javascript
{
  "metadata.device_id": 1,
  "timestamp": -1
}
```

The indexed read filters by one `metadata.device_id` and a timestamp range covering the last `TS_READ_WINDOW_SECONDS` seconds, sorts by `timestamp` descending, projects only the metadata, timestamp, and temperature fields, and limits results to `TS_READ_LIMIT` documents. If custom `TS_META_FIELD` or `TS_TIME_FIELD` values are used, the generated index and query use those configured names.

### Short verification test

Use this bounded run to verify the environment and database connection before starting a longer load test:

```bash
.venv/bin/locust -f locustfile.py \
  --headless \
  --users 1 \
  --spawn-rate 1 \
  --run-time 30s
```

The latest verification run completed with 312 requests, 0 failures, and approximately 10.4 requests/sec:

| Operation | Requests | Avg | Median | P95 | Max |
|---|---:|---:|---:|---:|---:|
| CRUD insert | 40 | 31 ms | 13 ms | 150 ms | 148 ms |
| CRUD update | 14 | 45 ms | 12 ms | 220 ms | 218 ms |
| CRUD delete | 9 | 41 ms | 13 ms | 170 ms | 166 ms |
| CRUD indexed read | 99 | 23 ms | 8 ms | 140 ms | 158 ms |
| Time-series insert | 32 | 29 ms | 14 ms | 140 ms | 212 ms |
| Time-series indexed read | 118 | 41 ms | 13 ms | 160 ms | 164 ms |

This is a single-user smoke benchmark. Results vary with MongoDB deployment size, network latency, collection contents, payload size, and the selected user count and run time.

## Notes

- The test reports MongoDB operations as Locust request types named `mongodb`.
- Update/delete operate on IDs created by the same Locust user; this avoids an extra lookup operation and keeps the measured operation isolated.
- Existing `TS_COLLECTION` must already be a native time-series collection with matching `TS_META_FIELD` and `TS_TIME_FIELD` settings, or remove it before changing those settings.
