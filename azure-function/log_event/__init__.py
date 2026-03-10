"""POST /api/log-event

Logs update events (updated, deferred, forced, scheduled) from
macOS clients for auditing and compliance.
"""

import json
import logging

import azure.functions as func

from shared.table_storage import log_update_event

_VALID = {"updated", "deferred", "forced", "scheduled"}


def main(req: func.HttpRequest) -> func.HttpResponse:
    try:
        body = req.get_json()
    except ValueError:
        return _json(400, {"error": "Invalid JSON"})

    device_id = body.get("device_id", "")
    bid = body.get("bundle_id", "")
    action = body.get("action", "")

    if not device_id or not bid or action not in _VALID:
        return _json(400, {
            "error": "device_id, bundle_id, and valid action required",
            "valid_actions": sorted(_VALID),
        })

    log_update_event(
        device_id=device_id,
        bundle_id=bid,
        action=action,
        from_version=body.get("from_version", ""),
        to_version=body.get("to_version", ""),
    )

    logging.info("log-event: %s %s %s", device_id[:8], bid, action)
    return _json(200, {"status": "ok"})


def _json(status: int, body: dict) -> func.HttpResponse:
    return func.HttpResponse(
        json.dumps(body), status_code=status, mimetype="application/json",
    )
