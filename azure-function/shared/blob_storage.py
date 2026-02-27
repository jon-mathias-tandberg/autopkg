"""Azure Blob Storage helpers – SAS token generation for package downloads."""

import os
from datetime import datetime, timedelta, timezone

from azure.storage.blob import BlobSasPermissions, generate_blob_sas


def generate_download_url(blob_path: str) -> tuple[str, str]:
    """Generate a short-lived SAS URL for downloading a package.

    Returns (download_url, expiry_iso) tuple.
    """
    account_name = os.environ["STORAGE_ACCOUNT_NAME"]
    account_key = os.environ["STORAGE_ACCOUNT_KEY"]
    container = os.environ.get("BLOB_CONTAINER_NAME", "packages")
    expiry_minutes = int(os.environ.get("SAS_TOKEN_EXPIRY_MINUTES", "15"))

    expiry = datetime.now(timezone.utc) + timedelta(minutes=expiry_minutes)

    sas_token = generate_blob_sas(
        account_name=account_name,
        container_name=container,
        blob_name=blob_path,
        account_key=account_key,
        permission=BlobSasPermissions(read=True),
        expiry=expiry,
    )

    url = f"https://{account_name}.blob.core.windows.net/{container}/{blob_path}?{sas_token}"
    return url, expiry.isoformat()
