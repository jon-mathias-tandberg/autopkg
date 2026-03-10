"""Azure Blob Storage helper – short-lived SAS token generation."""

import os
from datetime import datetime, timedelta, timezone

from azure.storage.blob import BlobSasPermissions, generate_blob_sas


def generate_download_url(blob_path: str) -> tuple[str, str]:
    """Return (sas_url, expiry_iso) for a package blob.

    The SAS token is read-only and expires after SAS_TOKEN_EXPIRY_MINUTES
    (default 15 minutes).
    """
    account_name = os.environ.get(
        "AZURE_STORAGE_ACCOUNT",
        os.environ.get("STORAGE_ACCOUNT_NAME", ""),
    )
    account_key = os.environ.get(
        "STORAGE_ACCOUNT_KEY",
        os.environ.get("AZURE_STORAGE_KEY", ""),
    )
    container = os.environ.get(
        "AZURE_STORAGE_CONTAINER",
        os.environ.get("BLOB_CONTAINER_NAME", "packages"),
    )
    expiry_minutes = int(os.environ.get("SAS_TOKEN_EXPIRY_MINUTES", "15"))

    expiry = datetime.now(timezone.utc) + timedelta(minutes=expiry_minutes)

    token = generate_blob_sas(
        account_name=account_name,
        container_name=container,
        blob_name=blob_path,
        account_key=account_key,
        permission=BlobSasPermissions(read=True),
        expiry=expiry,
    )

    url = (
        f"https://{account_name}.blob.core.windows.net"
        f"/{container}/{blob_path}?{token}"
    )
    return url, expiry.isoformat()
