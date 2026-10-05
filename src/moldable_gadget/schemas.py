"""The portable, bounded JSON Schema profile shared with the desktop."""

from __future__ import annotations

import math
import re

from .protocol import Json, Object, object_value, string


def identifier(value: object) -> str:
    result = string(value, limit=64)
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", result):
        raise ValueError("Capability identifiers use letters, digits, dots, dashes or underscores.")
    return result


def check_scalar(schema: Object) -> None:
    if set(schema) - {"type", "minimum", "maximum", "maxLength", "enum"}:
        raise ValueError("Unsupported scalar schema keyword.")
    kind = schema.get("type")
    if kind not in ("boolean", "number", "integer", "string"):
        raise ValueError("Supported scalar types: boolean, number, integer, string.")
    for key in ("minimum", "maximum"):
        if key in schema and (
            kind not in ("number", "integer")
            or type(schema[key]) not in (int, float)
            or not math.isfinite(schema[key])
        ):
            raise ValueError("Invalid numeric schema bound.")
    if "minimum" in schema and "maximum" in schema and schema["minimum"] > schema["maximum"]:
        raise ValueError("Schema minimum exceeds maximum.")
    if "maxLength" in schema and (
        kind != "string"
        or type(schema["maxLength"]) is not int
        or not 0 <= schema["maxLength"] <= 1024
    ):
        raise ValueError("String maxLength must be 0–1024.")
    if "enum" in schema:
        choices = schema["enum"]
        if not isinstance(choices, list) or not 1 <= len(choices) <= 32:
            raise ValueError("An enum must contain 1–32 choices.")
        without_enum = {key: value for key, value in schema.items() if key != "enum"}
        for value in choices:
            validate_scalar(without_enum, value)


def validate_scalar(schema: Object, value: Json) -> None:
    kind = schema.get("type")
    if kind == "boolean":
        valid = type(value) is bool
    elif kind in ("number", "integer"):
        valid = type(value) in (int, float) and math.isfinite(value)
        if valid:
            valid = (
                (kind != "integer" or value % 1 == 0)
                and ("minimum" not in schema or value >= schema["minimum"])
                and ("maximum" not in schema or value <= schema["maximum"])
            )
    elif kind == "string":
        valid = isinstance(value, str) and len(value) <= schema.get("maxLength", 1024)
    else:
        valid = False
    if not valid or ("enum" in schema and value not in schema["enum"]):
        raise ValueError("Value does not match the approved capability schema.")


def check_input(schema: Object) -> None:
    if set(schema) - {"type", "properties", "required", "additionalProperties"}:
        raise ValueError("Unsupported input schema keyword.")
    if schema.get("type") != "object" or schema.get("additionalProperties") is not False:
        raise ValueError("Inputs must be objects with additionalProperties false.")
    properties = object_value(schema.get("properties"))
    required = schema.get("required", [])
    if len(properties) > 16 or not isinstance(required, list) or len(required) > len(properties):
        raise ValueError("Inputs may contain at most 16 declared properties.")
    if any(not isinstance(name, str) or name not in properties for name in required):
        raise ValueError("Required properties must be declared.")
    for name, child in properties.items():
        identifier(name)
        check_scalar(object_value(child))


def validate_input(schema: Object, value: Object) -> None:
    properties = object_value(schema.get("properties"))
    if any(name not in value for name in schema.get("required", [])):
        raise ValueError("Required action input is missing.")
    for name, item in value.items():
        if name not in properties:
            raise ValueError("Unknown action input property.")
        validate_scalar(object_value(properties[name]), item)
