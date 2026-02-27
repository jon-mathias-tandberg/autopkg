"""Azure Table Storage helpers for the Update Agent API."""

import os
from datetime import datetime, timezone

from azure.data.tables import TableClient, TableServiceClient


def _get_table_client(table_name: str) -> TableClient:
    conn_str = os.environ["STORAGE_CONNECTION_STRING"]
    service = TableServiceClient.from_connection_string(conn_str)
    service.create_table_if_not_exists(table_name)
    return service.get_table_client(table_name)


def get_managed_apps_table() -> TableClient:
    table_name = os.environ.get("TABLE_MANAGED_APPS", "ManagedApps")
    return _get_table_client(table_name)


def get_update_events_table() -> TableClient:
    table_name = os.environ.get("TABLE_UPDATE_EVENTS", "UpdateEvents")
    return _get_table_client(table_name)


def get_managed_app(bundle_id: str) -> dict | None:
    """Look up a single managed app by bundle ID."""
    table = get_managed_apps_table()
    try:
        return table.get_entity(partition_key="apps", row_key=bundle_id)
    except Exception:
        return None


def get_all_managed_apps() -> dict[str, dict]:
    """Return all managed apps keyed by bundle ID."""
    table = get_managed_apps_table()
    apps = {}
    for entity in table.query_entities("PartitionKey eq 'apps'"):
        apps[entity["RowKey"]] = dict(entity)
    return apps


def upsert_managed_app(
    bundle_id: str,
    app_name: str,
    latest_version: str,
    blob_path: str,
    intune_app_id: str = "",
    recipe_id: str = "",
    min_os_version: str = "",
) -> None:
    """Insert or update a managed app entry."""
    table = get_managed_apps_table()
    entity = {
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
    table.upsert_entity(entity)


def log_update_event(
    device_id: str,
    bundle_id: str,
    action: str,
    from_version: str = "",
    to_version: str = "",
) -> None:
    """Log an update event (updated, deferred, forced, scheduled)."""
    table = get_update_events_table()
    now = datetime.now(timezone.utc)
    entity = {
        "PartitionKey": device_id,
        "RowKey": f"{now.strftime('%Y%m%dT%H%M%S')}_{bundle_id}",
        "bundle_id": bundle_id,
        "action": action,
        "from_version": from_version,
        "to_version": to_version,
        "timestamp_utc": now.isoformat(),
    }
    table.create_entity(entity)
