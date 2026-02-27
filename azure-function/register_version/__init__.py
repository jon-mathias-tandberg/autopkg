"""POST /api/register-version

Called by AutoPkg VersionReporter postprocessor after a successful
packaging run.  Registers (or updates) a managed app's latest version
and blob path in Table Storage.
"""

import json
import logging

import azure.functions as func

from shared.table_storage import upsert_managed_app


def main(req: func.HttpRequest) -> func.HttpResponse:
    try:
        body = req.get_json()
    except ValueError:
        return func.HttpResponse(
            json.dumps({"error": "Invalid JSON"}),
            status_code=400,
            mimetype="application/json",
        )

    bundle_id = body.get("bundle_id", "")
    latest_version = body.get("latest_version", "")

    if not bundle_id or not latest_version:
        return func.HttpResponse(
            json.dumps({"error": "bundle_id and latest_version are required"}),
            status_code=400,
            mimetype="application/json",
        )

    upsert_managed_app(
        bundle_id=bundle_id,
        app_name=body.get("app_name", ""),
        latest_version=latest_version,
        blob_path=body.get("blob_path", ""),
        intune_app_id=body.get("intune_app_id", ""),
        recipe_id=body.get("recipe_id", ""),
        min_os_version=body.get("min_os_version", ""),
    )

    logging.info("register-version: %s v%s", bundle_id, latest_version)

    return func.HttpResponse(
        json.dumps(
            {
                "status": "ok",
                "bundle_id": bundle_id,
                "latest_version": latest_version,
            }
        ),
        status_code=200,
        mimetype="application/json",
    )
