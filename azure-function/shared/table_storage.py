"""Azure Table Storage helpers for the Update Agent functions.

Tables are auto-created on first access.  Designed to coexist with
the existing autopkg-api-func functions.
"""

import os
from datetime import datetime, timezone

from azure.data.tables import TableServiceClient


def _get_connection_string() -> str:
    for key in (
        "AZURE_STORAGE_CONNECTION_STRING",
        "STORAGE_CONNECTION_STRING",
        "AzureWebJobsStorage",
    ):
        val = os.environ.get(key, "")
        if val and val != "UseDevelopmentStorage=true":
            return val
    raise RuntimeError(
        "No storage connection string found. "
        "Set AZURE_STORAGE_CONNECTION_STRING or AzureWebJobsStorage."
    )


def _get_table(table_name: str):
    conn = _get_connection_string()
    svc = TableServiceClient.from_connection_string(conn)
    svc.create_table_if_not_exists(table_name)
    return svc.get_table_client(table_name)


def _managed_apps_table():
    return _get_table(os.environ.get("TABLE_MANAGED_APPS", "ManagedApps"))


def _update_events_table():
    return _get_table(os.environ.get("TABLE_UPDATE_EVENTS", "UpdateEvents"))


# ── Managed Apps ──────────────────────────────────────────────


def get_all_managed_apps() -> dict[str, dict]:
    table = _managed_apps_table()
    return {
        e["RowKey"]: dict(e)
        for e in table.query_entities("PartitionKey eq 'apps'")
    }


def upsert_managed_app(
    bundle_id: str,
    app_name: str,
    latest_version: str,
    blob_path: str = "",
    intune_app_id: str = "",
    recipe_id: str = "",
    min_os_version: str = "",
) -> None:
    table = _managed_apps_table()
    table.upsert_entity(
        {
            "PartitionKey": "apps",
            "RowKey": bundle_id,
            "app_name": app_name,
            "latest_version": latest_version,
            "blob_path": blob_path,
            "intune_app_id": intune_app_id,
            "min_os_version": min_os_version,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "updated_by": recipe_id,
        }
    )


# ── Update Events ────────────────────────────────────────────


def log_update_event(
    device_id: str,
    bundle_id: str,
    action: str,
    from_version: str = "",
    to_version: str = "",
) -> None:
    table = _update_events_table()
    now = datetime.now(timezone.utc)
    table.create_entity(
        {
            "PartitionKey": device_id,
            "RowKey": f"{now.strftime('%Y%m%dT%H%M%S')}_{bundle_id}",
            "bundle_id": bundle_id,
            "action": action,
            "from_version": from_version,
            "to_version": to_version,
            "timestamp_utc": now.isoformat(),
        }
    )
