"""POST /api/check-updates

Receives installed apps from a macOS client, compares against the
ManagedApps table, and returns available updates with short-lived
SAS download URLs.
"""

import json
import logging

import azure.functions as func

from shared.blob_storage import generate_download_url
from shared.table_storage import get_all_managed_apps

try:
    from packaging.version import InvalidVersion, Version

    def _newer(installed: str, latest: str) -> bool:
        try:
            return Version(latest) > Version(installed)
        except InvalidVersion:
            return installed != latest

except ImportError:
    def _newer(installed: str, latest: str) -> bool:
        return installed != latest


def main(req: func.HttpRequest) -> func.HttpResponse:
    try:
        body = req.get_json()
    except ValueError:
        return _json(400, {"error": "Invalid JSON"})

    installed_apps = body.get("installed_apps", [])
    if not installed_apps:
        return _json(400, {"error": "installed_apps is required"})

    try:
        managed = get_all_managed_apps()
    except Exception as exc:
        logging.error("Failed to read ManagedApps table: %s", exc)
        return _json(500, {"error": f"Table Storage error: {exc}"})

    updates, up_to_date, not_managed = [], [], []

    for app in installed_apps:
        bid = app.get("bundle_id", "")
        if not bid:
            continue

        entry = managed.get(bid)
        if entry is None:
            not_managed.append({"bundle_id": bid})
            continue

        latest = entry.get("latest_version", "")
        installed = app.get("version", "0")

        if not latest or not _newer(installed, latest):
            up_to_date.append({
                "bundle_id": bid,
                "app_name": entry.get("app_name", ""),
            })
            continue

        blob_path = entry.get("blob_path", "")
        dl_url, sas_exp = ("", "")
        dl_file = ""
        if blob_path:
            dl_url, sas_exp = generate_download_url(blob_path)
            dl_file = blob_path.rsplit("/", 1)[-1]

        updates.append({
            "bundle_id": bid,
            "app_name": entry.get("app_name", ""),
            "installed_version": installed,
            "latest_version": latest,
            "download_url": dl_url,
            "download_filename": dl_file,
            "sas_expires_at": sas_exp,
        })

    logging.info(
        "check-updates: %d updates, %d current, %d unmanaged",
        len(updates), len(up_to_date), len(not_managed),
    )

    return _json(200, {
        "updates_available": updates,
        "up_to_date": up_to_date,
        "not_managed": not_managed,
    })


def _json(status: int, body: dict) -> func.HttpResponse:
    return func.HttpResponse(
        json.dumps(body), status_code=status, mimetype="application/json",
    )
