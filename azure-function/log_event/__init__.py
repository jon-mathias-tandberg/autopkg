"""POST /api/log-event

Logs update events (updated, deferred, forced, scheduled) from
macOS clients for auditing and compliance reporting.
"""

import json
import logging

import azure.functions as func

from shared.table_storage import log_update_event

VALID_ACTIONS = {"updated", "deferred", "forced", "scheduled"}


def main(req: func.HttpRequest) -> func.HttpResponse:
    try:
        body = req.get_json()
    except ValueError:
        return func.HttpResponse(
            json.dumps({"error": "Invalid JSON"}),
            status_code=400,
            mimetype="application/json",
        )

    device_id = body.get("device_id", "")
    bundle_id = body.get("bundle_id", "")
    action = body.get("action", "")

    if not device_id or not bundle_id or action not in VALID_ACTIONS:
        return func.HttpResponse(
            json.dumps(
                {
                    "error": "device_id, bundle_id, and valid action are required",
                    "valid_actions": sorted(VALID_ACTIONS),
                }
            ),
            status_code=400,
            mimetype="application/json",
        )

    log_update_event(
        device_id=device_id,
        bundle_id=bundle_id,
        action=action,
        from_version=body.get("from_version", ""),
        to_version=body.get("to_version", ""),
    )

    logging.info("log-event: %s %s %s", device_id[:8], bundle_id, action)

    return func.HttpResponse(
        json.dumps({"status": "ok"}),
        status_code=200,
        mimetype="application/json",
    )
