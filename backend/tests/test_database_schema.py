from dataclasses import fields
from unittest.mock import Mock

import pytest

from app import database_schema, models
from app.database_schema import FIELDS, MODEL_COLLECTIONS, apply_schema


def test_database_contract_covers_every_persisted_model_field():
    # A newly added model field must not be rejected by MongoDB in production.
    for model_name, (collection, primary_key) in MODEL_COLLECTIONS.items():
        model_fields = {field.name for field in fields(getattr(models, model_name))}
        model_fields.remove(primary_key)
        model_fields.add("_id")
        assert set(FIELDS[collection]) == model_fields


def test_failed_preflight_does_not_change_the_database(monkeypatch):
    database = Mock()
    monkeypatch.setattr(database_schema, "audit_database", lambda db: {"issues": ["broken reference"]})
    with pytest.raises(ValueError, match="broken reference"):
        apply_schema(database)
    assert database.mock_calls == []


def test_schema_setup_can_be_repeated_without_rewriting_business_documents(monkeypatch):
    database = Mock()
    database.list_collection_names.return_value = list(FIELDS)
    monkeypatch.setattr(database_schema, "audit_database", lambda db: {"issues": []})
    monkeypatch.setattr(database_schema, "ensure_indexes", lambda db: None)
    apply_schema(database)
    apply_schema(database)
    assert database.command.call_count == 2 * len(FIELDS)
    database.create_collection.assert_not_called()
    database.schema_migrations.update_one.assert_called()
    for call in database.schema_migrations.update_one.call_args_list:
        assert set(call.args[1]) == {"$setOnInsert"}
