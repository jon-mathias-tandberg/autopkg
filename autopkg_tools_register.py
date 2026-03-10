#!/usr/bin/env python3
"""Post-AutoPkg step: upload packages to Blob Storage and register versions.

Reads the AutoPkg report plist to find newly built/imported packages,
uploads them to the Azure Blob Storage 'packages' container, and calls
the register-version API endpoint.

Usage:
    python autopkg_tools/register_packages.py

Required environment variables:
    UPDATE_AGENT_BLOB_CONNECTION_STRING  - Blob Storage connection string
    UPDATE_AGENT_API_URL                 - Base URL for the Function App API
    UPDATE_AGENT_API_KEY                 - Function App key
"""

import json
import os
import plistlib
import subprocess
import sys
import urllib.request

REPORT_PLIST = "/tmp/autopkg.plist"
CACHE_DIR = os.path.expanduser("~/Library/AutoPkg/Cache")
BLOB_CONTAINER = os.environ.get("UPDATE_AGENT_BLOB_CONTAINER", "packages")


def load_report() -> dict:
    """Load the AutoPkg report plist."""
    if not os.path.isfile(REPORT_PLIST):
        print(f"⚠️  Report plist not found: {REPORT_PLIST}")
        return {}
    with open(REPORT_PLIST, "rb") as f:
        return plistlib.load(f)


def find_packages() -> list[dict]:
    """Extract built packages from AutoPkg cache directories."""
    packages = []

    for recipe_dir in os.listdir(CACHE_DIR):
        recipe_path = os.path.join(CACHE_DIR, recipe_dir)
        if not os.path.isdir(recipe_path):
            continue

        for root, dirs, files in os.walk(recipe_path):
            for filename in files:
                if not (filename.endswith(".pkg") or filename.endswith(".dmg")):
                    continue

                filepath = os.path.join(root, filename)
                # Try to find Info.plist or receipt for metadata
                info = extract_metadata(recipe_path, filename)
                if info.get("bundle_id"):
                    info["filepath"] = filepath
                    info["filename"] = filename
                    packages.append(info)

    return packages


def extract_metadata(recipe_path: str, filename: str) -> dict:
    """Try to extract bundle_id and version from recipe cache."""
    info = {"bundle_id": "", "version": "", "app_name": ""}

    # Look for receipts with version info
    receipts_dir = os.path.join(recipe_path, "receipts")
    if os.path.isdir(receipts_dir):
        for receipt_file in sorted(os.listdir(receipts_dir), reverse=True):
            if not receipt_file.endswith(".plist"):
                continue
            try:
                with open(os.path.join(receipts_dir, receipt_file), "rb") as f:
                    receipt = plistlib.load(f)
                env = receipt.get("Environment", {})
                info["bundle_id"] = env.get("bundleid", env.get("BUNDLE_ID", ""))
                info["version"] = env.get("version", env.get("VERSION", ""))
                info["app_name"] = env.get("NAME", "")
                if info["bundle_id"] and info["version"]:
                    break
            except Exception:
                continue

    return info


def upload_to_blob(filepath: str, blob_path: str) -> bool:
    """Upload a file to Azure Blob Storage using az CLI."""
    conn_str = os.environ.get("UPDATE_AGENT_BLOB_CONNECTION_STRING", "")
    if not conn_str:
        print(f"⚠️  UPDATE_AGENT_BLOB_CONNECTION_STRING not set, skipping upload")
        return False

    cmd = [
        "az", "storage", "blob", "upload",
        "--container-name", BLOB_CONTAINER,
        "--name", blob_path,
        "--file", filepath,
        "--connection-string", conn_str,
        "--overwrite", "true",
        "--no-progress",
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"❌ Blob upload failed: {result.stderr}")
        return False
    return True


def register_version(bundle_id: str, app_name: str, version: str, blob_path: str) -> bool:
    """Call the register-version API endpoint."""
    api_url = os.environ.get("UPDATE_AGENT_API_URL", "")
    api_key = os.environ.get("UPDATE_AGENT_API_KEY", "")

    if not api_url or not api_key:
        print("⚠️  UPDATE_AGENT_API_URL or UPDATE_AGENT_API_KEY not set")
        return False

    url = f"{api_url}/register-version?code={api_key}"
    payload = json.dumps({
        "bundle_id": bundle_id,
        "app_name": app_name,
        "latest_version": version,
        "blob_path": blob_path,
    }).encode()

    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req) as resp:
            body = json.loads(resp.read())
            print(f"  ✅ API: {body}")
            return True
    except Exception as exc:
        print(f"  ❌ API error: {exc}")
        return False


def main():
    print("📦 === REGISTER PACKAGES FOR UPDATE AGENT ===")

    packages = find_packages()

    if not packages:
        print("ℹ️  No packages found in AutoPkg cache")
        return

    print(f"📋 Found {len(packages)} package(s)")

    for pkg in packages:
        bundle_id = pkg["bundle_id"]
        version = pkg["version"]
        app_name = pkg["app_name"]
        filename = pkg["filename"]
        filepath = pkg["filepath"]
        blob_path = f"{bundle_id}/{filename}"

        print(f"\n📦 {app_name} ({bundle_id}) v{version}")
        print(f"   File: {filename}")

        # Upload to blob storage
        print(f"   ⬆️  Uploading to {BLOB_CONTAINER}/{blob_path}...")
        if upload_to_blob(filepath, blob_path):
            print(f"   ✅ Uploaded to blob storage")
        else:
            print(f"   ⚠️  Blob upload skipped/failed")

        # Register version via API
        print(f"   📝 Registering version...")
        register_version(bundle_id, app_name, version, blob_path)

    print("\n📦 === DONE ===")


if __name__ == "__main__":
    main()
