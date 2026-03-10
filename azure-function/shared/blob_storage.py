"""Azure Blob Storage helper – short-lived SAS token generation."""

import logging
import os
from datetime import datetime, timedelta, timezone

from azure.storage.blob import BlobSasPermissions, generate_blob_sas


def _parse_connection_string(conn_str: str) -> dict[str, str]:
    """Extract key=value pairs from a storage connection string."""
    parts = {}
    for segment in conn_str.split(";"):
        if "=" in segment:
            key, _, val = segment.partition("=")
            parts[key.strip()] = val.strip()
    return parts


def _get_storage_credentials() -> tuple[str, str, str]:
    """Return (account_name, account_key, container) from environment.

    Looks for a dedicated blob connection string first
    (UPDATE_AGENT_BLOB_CONNECTION_STRING), then falls back to the
    general storage connection strings.
    """
    container = os.environ.get("UPDATE_AGENT_BLOB_CONTAINER", "packages")

    conn_str = os.environ.get("UPDATE_AGENT_BLOB_CONNECTION_STRING", "")
    if not conn_str:
        conn_str = os.environ.get(
            "AZURE_STORAGE_CONNECTION_STRING",
            os.environ.get("AzureWebJobsStorage", ""),
        )

    account_name = ""
    account_key = ""
    if conn_str:
        parts = _parse_connection_string(conn_str)
        account_name = parts.get("AccountName", "")
        account_key = parts.get("AccountKey", "")

    return account_name, account_key, container


def generate_download_url(blob_path: str) -> tuple[str, str]:
    """Return (sas_url, expiry_iso) for a package blob.

    The SAS token is read-only and expires after SAS_TOKEN_EXPIRY_MINUTES
    (default 15 minutes).  Returns empty strings if storage account key
    is not configured.
    """
    account_name, account_key, container = _get_storage_credentials()

    if not account_name or not account_key:
        logging.warning(
            "SAS token generation skipped: "
            "AZURE_STORAGE_ACCOUNT or STORAGE_ACCOUNT_KEY not set"
        )
        return "", ""

    expiry_minutes = int(os.environ.get("SAS_TOKEN_EXPIRY_MINUTES", "15"))
    expiry = datetime.now(timezone.utc) + timedelta(minutes=expiry_minutes)

    try:
        token = generate_blob_sas(
            account_name=account_name,
            container_name=container,
            blob_name=blob_path,
            account_key=account_key,
            permission=BlobSasPermissions(read=True),
            expiry=expiry,
        )
    except Exception as exc:
        logging.error("SAS token generation failed: %s", exc)
        return "", ""

    url = (
        f"https://{account_name}.blob.core.windows.net"
        f"/{container}/{blob_path}?{token}"
    )
    return url, expiry.isoformat()
