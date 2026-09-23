"""MongoDB CRUD + native time-series stress test for Locust.

Default task weights:
  - CRUD insert:       15%
  - CRUD update:       15%
  - CRUD delete:       10%
  - TS insert:         30%
  - TS indexed read:   30%

Run with:
  locust -f locustfile.py --host mongodb://localhost:27017

The --host value is optional; MONGODB_URI takes precedence when set.
"""

from __future__ import annotations

import os
import random
import string
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from dotenv import load_dotenv
from locust import User, between, events, task
from pymongo import ASCENDING, DESCENDING, MongoClient
from pymongo.collection import Collection
from pymongo.errors import CollectionInvalid, OperationFailure


load_dotenv()

MONGODB_URI = os.getenv("MONGODB_URI")
MONGODB_DB = os.getenv("MONGODB_DB", "stress_test")
CRUD_COLLECTION_NAME = os.getenv("CRUD_COLLECTION", "crud_documents")
TS_COLLECTION_NAME = os.getenv("TS_COLLECTION", "sensor_measurements")
TS_META_FIELD = os.getenv("TS_META_FIELD", "metadata")
TS_TIME_FIELD = os.getenv("TS_TIME_FIELD", "timestamp")
TS_GRANULARITY = os.getenv("TS_GRANULARITY", "seconds")
TS_INDEX_NAME = os.getenv("TS_INDEX_NAME", "metadata_device_timestamp")
TS_READ_WINDOW_SECONDS = int(os.getenv("TS_READ_WINDOW_SECONDS", "3600"))
TS_READ_LIMIT = int(os.getenv("TS_READ_LIMIT", "100"))
PAYLOAD_BYTES = int(os.getenv("PAYLOAD_BYTES", "256"))
DEVICE_COUNT = int(os.getenv("DEVICE_COUNT", "1000"))

_client: MongoClient | None = None
_db = None
_crud: Collection | None = None
_time_series: Collection | None = None
_setup_lock = threading.Lock()


def _now_ms() -> float:
    return time.perf_counter() * 1000.0


def _payload(size: int) -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(random.choices(alphabet, k=max(0, size)))


def _record_request(name: str, started_ms: float, response_length: int = 0, exception: Exception | None = None) -> None:
    events.request.fire(
        request_type="mongodb",
        name=name,
        response_time=max(0.0, _now_ms() - started_ms),
        response_length=response_length,
        exception=exception,
    )


def _setup_database(environment=None) -> None:
    """Create the collections and the time-series index once per Locust process."""
    global _client, _db, _crud, _time_series

    uri = MONGODB_URI or getattr(environment, "host", None) or "mongodb://localhost:27017"
    _client = MongoClient(
        uri,
        appname="locust-mongodb-stress-test",
        serverSelectionTimeoutMS=int(os.getenv("MONGODB_SERVER_SELECTION_TIMEOUT_MS", "10000")),
        connectTimeoutMS=int(os.getenv("MONGODB_CONNECT_TIMEOUT_MS", "10000")),
    )
    _client.admin.command("ping")
    _db = _client[MONGODB_DB]
    _crud = _db[CRUD_COLLECTION_NAME]

    if TS_COLLECTION_NAME not in _db.list_collection_names():
        try:
            _db.create_collection(
                TS_COLLECTION_NAME,
                timeseries={
                    "timeField": TS_TIME_FIELD,
                    "metaField": TS_META_FIELD,
                    "granularity": TS_GRANULARITY,
                },
            )
        except CollectionInvalid:
            # Another Locust worker may have created it concurrently.
            pass

    _time_series = _db[TS_COLLECTION_NAME]
    try:
        _time_series.create_index(
            [
                (f"{TS_META_FIELD}.device_id", ASCENDING),
                (TS_TIME_FIELD, DESCENDING),
            ],
            name=TS_INDEX_NAME,
        )
    except OperationFailure as exc:
        raise RuntimeError(
            f"Could not create the time-series index on {MONGODB_DB}.{TS_COLLECTION_NAME}. "
            "Check that the collection is a native time-series collection and that the "
            "configured time/meta fields match it."
        ) from exc


def _ensure_database(environment=None) -> None:
    """Initialize MongoDB even if a runner does not fire test_start first."""
    if _crud is not None and _time_series is not None:
        return
    with _setup_lock:
        if _crud is None or _time_series is None:
            _setup_database(environment)


@events.test_start.add_listener
def on_test_start(environment, **kwargs: Any) -> None:
    _ensure_database(environment)


@events.test_stop.add_listener
def on_test_stop(environment, **kwargs: Any) -> None:
    global _client, _db, _crud, _time_series
    if _client is not None:
        _client.close()
    _client = None
    _db = None
    _crud = None
    _time_series = None


class MongoDBStressUser(User):
    """Locust user generating CRUD and indexed native time-series traffic."""

    wait_time = between(0.01, 0.10)

    # 40% CRUD, 60% time-series; time-series is split evenly between writes/reads.
    @task(15)
    def crud_insert(self) -> None:
        assert _crud is not None
        document_id = uuid.uuid4().hex
        document = {
            "_id": document_id,
            "tenant_id": random.randrange(1, 100),
            "status": random.choice(["new", "queued", "active", "complete"]),
            "value": random.random() * 1000,
            "updated_at": datetime.now(timezone.utc),
            "payload": _payload(PAYLOAD_BYTES),
        }
        started = _now_ms()
        try:
            result = _crud.insert_one(document)
            self._remember_id(document_id)
            _record_request("crud_insert", started, 1 if result.acknowledged else 0)
        except Exception as exc:  # noqa: BLE001 - report database errors to Locust
            _record_request("crud_insert", started, exception=exc)

    @task(15)
    def crud_update(self) -> None:
        assert _crud is not None
        document_id = self._choose_id()
        if document_id is None:
            return
        started = _now_ms()
        try:
            result = _crud.update_one(
                {"_id": document_id},
                {
                    "$set": {
                        "status": random.choice(["queued", "active", "complete"]),
                        "value": random.random() * 1000,
                        "updated_at": datetime.now(timezone.utc),
                    },
                    "$inc": {"update_count": 1},
                },
            )
            _record_request("crud_update", started, result.modified_count)
        except Exception as exc:  # noqa: BLE001
            _record_request("crud_update", started, exception=exc)

    @task(10)
    def crud_delete(self) -> None:
        assert _crud is not None
        document_id = self._choose_id()
        if document_id is None:
            return
        started = _now_ms()
        try:
            result = _crud.delete_one({"_id": document_id})
            if result.deleted_count:
                self._forget_id(document_id)
            _record_request("crud_delete", started, result.deleted_count)
        except Exception as exc:  # noqa: BLE001
            _record_request("crud_delete", started, exception=exc)

    @task(30)
    def time_series_insert(self) -> None:
        assert _time_series is not None
        started = _now_ms()
        document = {
            TS_META_FIELD: {
                "device_id": f"device-{random.randrange(DEVICE_COUNT):06d}",
                "site": random.choice(["hk", "ny", "london", "sydney"]),
            },
            TS_TIME_FIELD: datetime.now(timezone.utc),
            "temperature": round(random.uniform(10.0, 40.0), 3),
            "humidity": round(random.uniform(20.0, 95.0), 3),
            "pressure": round(random.uniform(980.0, 1040.0), 3),
        }
        try:
            result = _time_series.insert_one(document)
            _record_request("timeseries_insert", started, 1 if result.acknowledged else 0)
        except Exception as exc:  # noqa: BLE001
            _record_request("timeseries_insert", started, exception=exc)

    @task(30)
    def time_series_index_read(self) -> None:
        assert _time_series is not None
        device_id = f"device-{random.randrange(DEVICE_COUNT):06d}"
        end = datetime.now(timezone.utc)
        start = end - timedelta(seconds=TS_READ_WINDOW_SECONDS)
        started = _now_ms()
        try:
            cursor = (
                _time_series.find(
                    {
                        f"{TS_META_FIELD}.device_id": device_id,
                        TS_TIME_FIELD: {"$gte": start, "$lt": end},
                    },
                    {"_id": 0, TS_META_FIELD: 1, TS_TIME_FIELD: 1, "temperature": 1},
                )
                .sort(TS_TIME_FIELD, DESCENDING)
                .limit(TS_READ_LIMIT)
            )
            rows = list(cursor)
            _record_request("timeseries_index_read", started, len(rows))
        except Exception as exc:  # noqa: BLE001
            _record_request("timeseries_index_read", started, exception=exc)

    def on_start(self) -> None:
        _ensure_database(getattr(self, "environment", None))
        self._ids: list[str] = []
        self._max_local_ids = int(os.getenv("MAX_LOCAL_CRUD_IDS", "1000"))

    def _remember_id(self, document_id: str) -> None:
        self._ids.append(document_id)
        if len(self._ids) > self._max_local_ids:
            del self._ids[0]

    def _choose_id(self) -> str | None:
        return random.choice(self._ids) if self._ids else None

    def _forget_id(self, document_id: str) -> None:
        try:
            self._ids.remove(document_id)
        except ValueError:
            pass
