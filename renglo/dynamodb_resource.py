"""Shared DynamoDB resource instances (reuse across model constructors)."""

from __future__ import annotations

import boto3

_resources: dict[str | None, object] = {}


def get_dynamodb_resource(region_name: str | None = None):
    """Return a cached boto3 DynamoDB resource for the given region."""
    key = region_name
    if key not in _resources:
        if region_name:
            _resources[key] = boto3.resource("dynamodb", region_name=region_name)
        else:
            _resources[key] = boto3.resource("dynamodb")
    return _resources[key]
