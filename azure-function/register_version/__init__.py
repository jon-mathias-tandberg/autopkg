"""POST /api/register-version

Called by the AutoPkg VersionReporter postprocessor after successful
packaging.  Upserts a managed app entry in Table Storage.
"""

import json
import logging

import azure.functions as func

from shared.table_storage import upsert_managed_app


def main(req: func.HttpRequest) -> func.HttpResponse:
    try:
        body = req.get_json()
    except ValueError:
        return _json(400, {"error": "Invalid JSON"})

    bid = body.get("bundle_id", "")
    ver = body.get("latest_version", "")

    if not bid or not ver:
        return _json(400, {"error": "bundle_id and latest_version are required"})

    upsert_managed_app(
        bundle_id=bid,
        app_name=body.get("app_name", ""),
        latest_version=ver,
        blob_path=body.get("blob_path", ""),
        intune_app_id=body.get("intune_app_id", ""),
        recipe_id=body.get("recipe_id", ""),
        min_os_version=body.get("min_os_version", ""),
    )

    logging.info("register-version: %s v%s", bid, ver)
    return _json(200, {"status": "ok", "bundle_id": bid, "latest_version": ver})


def _json(status: int, body: dict) -> func.HttpResponse:
    return func.HttpResponse(
        json.dumps(body), status_code=status, mimetype="application/json",
    )
