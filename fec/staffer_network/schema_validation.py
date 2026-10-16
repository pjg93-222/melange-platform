# -*- coding: utf-8 -*-
"""Small dependency-free validator for the committed staffer JSON schemas.

Only the JSON-Schema keywords used by ``fec/staffer_network/schemas`` are
implemented. Unknown validation keywords are rejected before an instance is
checked; this keeps the dependency-free subset fail closed.
"""

from __future__ import annotations

from datetime import date, datetime
import json
import math
import re
from typing import Any, Mapping
from urllib.parse import urlparse


class SchemaValidationError(ValueError):
    pass


_SUPPORTED_SCHEMA_KEYS = {
    "$schema", "$id", "title", "description", "type", "const", "enum",
    "properties", "required", "additionalProperties", "items", "minItems",
    "maxItems", "uniqueItems", "minLength", "maxLength", "pattern", "format",
    "minimum", "maximum", "minProperties", "maxProperties", "allOf", "if",
    "then", "else",
}


def validate_schema_definition(schema: Mapping[str, Any], path: str = "$schema") -> None:
    """Reject unsupported keywords and malformed nested schema positions."""

    if not isinstance(schema, Mapping):
        raise SchemaValidationError(f"{path}: schema must be an object")
    unsupported = sorted(set(schema) - _SUPPORTED_SCHEMA_KEYS)
    if unsupported:
        raise SchemaValidationError(f"{path}: unsupported schema keywords {unsupported!r}")
    properties = schema.get("properties")
    if properties is not None:
        if not isinstance(properties, Mapping):
            raise SchemaValidationError(f"{path}.properties: must be an object")
        for name, child in properties.items():
            validate_schema_definition(child, f"{path}.properties.{name}")
    items = schema.get("items")
    if items is not None:
        validate_schema_definition(items, f"{path}.items")
    additional = schema.get("additionalProperties")
    if additional is not None and not isinstance(additional, bool):
        validate_schema_definition(additional, f"{path}.additionalProperties")
    all_of = schema.get("allOf")
    if all_of is not None:
        if not isinstance(all_of, list) or not all_of:
            raise SchemaValidationError(f"{path}.allOf: must be a nonempty array")
        for index, child in enumerate(all_of):
            validate_schema_definition(child, f"{path}.allOf[{index}]")
    conditional = schema.get("if")
    if conditional is not None:
        validate_schema_definition(conditional, f"{path}.if")
    for keyword in ("then", "else"):
        child = schema.get(keyword)
        if child is not None:
            if conditional is None:
                raise SchemaValidationError(
                    f"{path}.{keyword}: requires an if schema"
                )
            validate_schema_definition(child, f"{path}.{keyword}")


def _is_type(value: Any, type_name: str) -> bool:
    if type_name == "null":
        return value is None
    if type_name == "object":
        return isinstance(value, Mapping)
    if type_name == "array":
        return isinstance(value, list)
    if type_name == "string":
        return isinstance(value, str)
    if type_name == "boolean":
        return isinstance(value, bool)
    if type_name == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if type_name == "number":
        return (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and not (isinstance(value, float) and not math.isfinite(value))
        )
    raise SchemaValidationError(f"unsupported schema type: {type_name}")


def _json_equal(a: Any, b: Any) -> bool:
    """JSON equality: bool never equals a number; containers compare recursively."""

    if isinstance(a, bool) or isinstance(b, bool):
        return type(a) is type(b) and a == b
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(_json_equal(x, y) for x, y in zip(a, b))
    if isinstance(a, Mapping) and isinstance(b, Mapping):
        return a.keys() == b.keys() and all(_json_equal(a[k], b[k]) for k in a)
    return a == b


def _check_format(value: str, format_name: str, path: str) -> None:
    if format_name == "date":
        try:
            parsed = date.fromisoformat(value)
        except ValueError as exc:
            raise SchemaValidationError(f"{path}: invalid date") from exc
        if parsed.isoformat() != value:
            raise SchemaValidationError(f"{path}: date is not canonical ISO-8601")
        return
    if format_name == "date-time":
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise SchemaValidationError(f"{path}: invalid date-time") from exc
        if parsed.tzinfo is None:
            raise SchemaValidationError(f"{path}: date-time must include a timezone")
        return
    if format_name == "uri":
        parsed = urlparse(value)
        if not parsed.scheme or (parsed.scheme in {"http", "https"} and not parsed.netloc):
            raise SchemaValidationError(f"{path}: invalid URI")
        return
    raise SchemaValidationError(f"{path}: unsupported format {format_name!r}")


def validate_instance(
    value: Any,
    schema: Mapping[str, Any],
    path: str = "$",
    *,
    _schema_checked: bool = False,
) -> None:
    """Validate one JSON value against the repository's supported subset."""

    if not _schema_checked:
        validate_schema_definition(schema)

    if "const" in schema and not _json_equal(value, schema["const"]):
        raise SchemaValidationError(f"{path}: value does not match const")
    if "enum" in schema and not any(_json_equal(value, c) for c in schema["enum"]):
        raise SchemaValidationError(f"{path}: value not in enum")
    for child in schema.get("allOf", []):
        validate_instance(value, child, path, _schema_checked=True)
    conditional = schema.get("if")
    if conditional is not None:
        try:
            validate_instance(value, conditional, path, _schema_checked=True)
        except SchemaValidationError:
            branch = schema.get("else")
        else:
            branch = schema.get("then")
        if branch is not None:
            validate_instance(value, branch, path, _schema_checked=True)

    declared = schema.get("type")
    if declared is not None:
        type_names = [declared] if isinstance(declared, str) else list(declared)
        if not any(_is_type(value, name) for name in type_names):
            raise SchemaValidationError(f"{path}: expected type {type_names!r}")

    if isinstance(value, Mapping):
        properties = schema.get("properties", {})
        required = schema.get("required", [])
        missing = [key for key in required if key not in value]
        if missing:
            raise SchemaValidationError(f"{path}: missing required fields {missing!r}")
        extras = sorted(set(value) - set(properties))
        additional = schema.get("additionalProperties", True)
        if additional is False:
            if extras:
                raise SchemaValidationError(f"{path}: unexpected fields {extras!r}")
        elif isinstance(additional, Mapping):
            for key in extras:
                validate_instance(
                    value[key], additional, f"{path}.{key}", _schema_checked=True
                )
        for key, child in value.items():
            child_schema = properties.get(key)
            if child_schema is not None:
                validate_instance(
                    child, child_schema, f"{path}.{key}", _schema_checked=True
                )
        minimum_properties = schema.get("minProperties")
        if minimum_properties is not None and len(value) < minimum_properties:
            raise SchemaValidationError(
                f"{path}: fewer than {minimum_properties} properties"
            )
        maximum_properties = schema.get("maxProperties")
        if maximum_properties is not None and len(value) > maximum_properties:
            raise SchemaValidationError(
                f"{path}: more than {maximum_properties} properties"
            )

    if isinstance(value, list):
        minimum_items = schema.get("minItems")
        if minimum_items is not None and len(value) < minimum_items:
            raise SchemaValidationError(f"{path}: fewer than {minimum_items} items")
        maximum_items = schema.get("maxItems")
        if maximum_items is not None and len(value) > maximum_items:
            raise SchemaValidationError(f"{path}: more than {maximum_items} items")
        if schema.get("uniqueItems"):
            fingerprints = [json.dumps(item, sort_keys=True) for item in value]
            if len(fingerprints) != len(set(fingerprints)):
                raise SchemaValidationError(f"{path}: items are not unique")
        item_schema = schema.get("items")
        if item_schema is not None:
            for index, child in enumerate(value):
                validate_instance(
                    child, item_schema, f"{path}[{index}]", _schema_checked=True
                )

    if isinstance(value, str):
        minimum_length = schema.get("minLength")
        if minimum_length is not None and len(value) < minimum_length:
            raise SchemaValidationError(f"{path}: shorter than {minimum_length}")
        maximum_length = schema.get("maxLength")
        if maximum_length is not None and len(value) > maximum_length:
            raise SchemaValidationError(f"{path}: longer than {maximum_length}")
        pattern = schema.get("pattern")
        if pattern is not None and re.search(pattern, value) is None:
            raise SchemaValidationError(f"{path}: does not match {pattern!r}")
        format_name = schema.get("format")
        if format_name is not None:
            _check_format(value, format_name, path)

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if isinstance(value, float) and not math.isfinite(value):
            raise SchemaValidationError(f"{path}: number must be finite")
        minimum = schema.get("minimum")
        if minimum is not None and value < minimum:
            raise SchemaValidationError(f"{path}: below minimum {minimum}")
        maximum = schema.get("maximum")
        if maximum is not None and value > maximum:
            raise SchemaValidationError(f"{path}: above maximum {maximum}")
