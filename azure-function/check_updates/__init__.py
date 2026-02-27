"""POST /api/check-updates

Receives a list of installed apps from a macOS client, compares against
managed app versions, and returns available updates with short-lived
SAS download URLs.
"""

import json
import logging

import azure.functions as func

from shared.blob_storage import generate_download_url
from shared.table_storage import get_all_managed_apps

from packaging.version import Version, InvalidVersion


def _is_update_available(installed: str, latest: str) -> bool:
    """Return True if latest is newer than installed."""
    try:
        return Version(latest) > Version(installed)
    except InvalidVersion:
        return installed != latest


def main(req: func.HttpRequest) -> func.HttpResponse:
    try:
        body = req.get_json()
    except ValueError:
        return func.HttpResponse(
            json.dumps({"error": "Invalid JSON"}),
            status_code=400,
            mimetype="application/json",
        )

    installed_apps = body.get("installed_apps", [])
    if not installed_apps:
        return func.HttpResponse(
            json.dumps({"error": "installed_apps is required"}),
            status_code=400,
            mimetype="application/json",
        )

    managed_apps = get_all_managed_apps()

    updates_available = []
    up_to_date = []
    not_managed = []

    for app in installed_apps:
        bundle_id = app.get("bundle_id", "")
        installed_version = app.get("version", "0")

        if not bundle_id:
            continue

        managed = managed_apps.get(bundle_id)
        if managed is None:
            not_managed.append({"bundle_id": bundle_id})
            continue

        latest_version = managed.get("latest_version", "")
        if not latest_version or not _is_update_available(
            installed_version, latest_version
        ):
            up_to_date.append(
                {
                    "bundle_id": bundle_id,
                    "app_name": managed.get("app_name", ""),
                }
            )
            continue

        blob_path = managed.get("blob_path", "")
        download_url = ""
        sas_expires_at = ""
        download_filename = ""

        if blob_path:
            download_url, sas_expires_at = generate_download_url(blob_path)
            download_filename = blob_path.rsplit("/", 1)[-1] if "/" in blob_path else blob_path

        updates_available.append(
            {
                "bundle_id": bundle_id,
                "app_name": managed.get("app_name", ""),
                "installed_version": installed_version,
                "latest_version": latest_version,
                "download_url": download_url,
                "download_filename": download_filename,
                "sas_expires_at": sas_expires_at,
            }
        )

    result = {
        "updates_available": updates_available,
        "up_to_date": up_to_date,
        "not_managed": not_managed,
    }

    logging.info(
        "check-updates: %d updates, %d up-to-date, %d not managed",
        len(updates_available),
        len(up_to_date),
        len(not_managed),
    )

    return func.HttpResponse(
        json.dumps(result),
        status_code=200,
        mimetype="application/json",
    )
