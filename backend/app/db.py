"""MongoDB connection, indexes, and operations shared by API routes."""

import os
from contextlib import contextmanager
from dataclasses import asdict
from datetime import date, datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import TypeVar

from dotenv import load_dotenv
from pymongo import MongoClient, ReturnDocument

from .database_schema import MODEL_COLLECTIONS, ensure_indexes

load_dotenv(Path(__file__).resolve().parents[1] / ".env")
T = TypeVar("T")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _mongo_value(value):
    if isinstance(value, datetime):
        return as_utc(value).replace(tzinfo=None)
    if isinstance(value, date):
        return value.isoformat()
    return value


def _model_key(model):
    return MODEL_COLLECTIONS[model.__name__][1]


class MongoStore:
    def __init__(self, database, client=None, session=None):
        self.database = database
        self.client = client
        self.session = session

    def _collection(self, model):
        return self.database[MODEL_COLLECTIONS[model.__name__][0]]

    def _options(self):
        return {"session": self.session} if self.session is not None else {}

    def _decode(self, model: type[T], document) -> T | None:
        if document is None:
            return None
        data = dict(document)
        data[_model_key(model)] = data.pop("_id")
        if model.__name__ == "DoctorProfile" and isinstance(data.get("registration_date"), str):
            data["registration_date"] = date.fromisoformat(data["registration_date"])
        return model(**data)

    def next_id(self, model) -> int:
        row = self.database["counters"].find_one_and_update(
            {"_id": model.__name__}, {"$inc": {"value": 1}}, upsert=True,
            return_document=ReturnDocument.AFTER, **self._options())
        return row["value"]

    def insert(self, item: T) -> T:
        model = type(item)
        key = _model_key(model)
        if getattr(item, key) is None:
            setattr(item, key, self.next_id(model))
        document = {name: _mongo_value(value) for name, value in asdict(item).items()}
        document["_id"] = document.pop(key)
        self._collection(model).insert_one(document, **self._options())
        return item

    def save(self, item: T) -> T:
        model = type(item)
        key = _model_key(model)
        document = {name: _mongo_value(value) for name, value in asdict(item).items()}
        document["_id"] = document.pop(key)
        result = self._collection(model).replace_one({"_id": document["_id"]}, document, **self._options())
        if result.matched_count != 1:
            raise RuntimeError(f"Missing {model.__name__} {document['_id']}")
        return item

    def get(self, model: type[T], key) -> T | None:
        return self._decode(model, self._collection(model).find_one({"_id": key}, **self._options()))

    def find(self, model: type[T], query=None, *, sort=None, limit=0) -> list[T]:
        cursor = self._collection(model).find(query or {}, **self._options())
        if sort:
            cursor = cursor.sort(sort)
        if limit:
            cursor = cursor.limit(limit)
        return [self._decode(model, row) for row in cursor]

    def first(self, model: type[T], query=None, *, sort=None) -> T | None:
        return next(iter(self.find(model, query, sort=sort, limit=1)), None)

    def update(self, model: type[T], query: dict, changes: dict, *, increments: dict | None = None) -> T | None:
        operation = {"$set": {key: _mongo_value(value) for key, value in changes.items()}} if changes else {}
        if increments:
            operation["$inc"] = increments
        row = self._collection(model).find_one_and_update(
            query, operation, return_document=ReturnDocument.AFTER, **self._options())
        return self._decode(model, row)

    def delete(self, item) -> None:
        self.delete_one(type(item), {"_id": getattr(item, _model_key(type(item)))})

    def delete_one(self, model: type[T], query: dict) -> bool:
        return self._collection(model).delete_one(query, **self._options()).deleted_count == 1

    def run_transaction(self, operation):
        if self.client is None or self.session is not None:
            return operation(self)
        with self.client.start_session() as session:
            return session.with_transaction(lambda active: operation(MongoStore(self.database, self.client, active)))

    @contextmanager
    def transaction(self):
        # Atlas and local replica sets support transactions. Test doubles opt out.
        if self.client is None:
            yield self
            return
        with self.client.start_session() as session:
            with session.start_transaction():
                yield MongoStore(self.database, self.client, session)


@lru_cache
def store() -> MongoStore:
    uri = os.getenv("MONGODB_URI")
    if not uri:
        raise RuntimeError("Set MONGODB_URI to an Atlas or replica-set connection string")
    client = MongoClient(uri, serverSelectionTimeoutMS=5000, tz_aware=True)
    database = client[os.getenv("MONGODB_DB_NAME", "mediconnect")]
    database.command("ping")
    ensure_indexes(database)
    return MongoStore(database, client)


def get_store():
    yield store()
