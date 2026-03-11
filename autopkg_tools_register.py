#!/usr/bin/env python3
"""Post-AutoPkg step: upload packages to Blob Storage and register versions.

Reads the AutoPkg report plist AND receipt plists to find package info,
uploads to Azure Blob Storage, and calls register-version API.

Required environment variables:
    UPDATE_AGENT_API_URL  - Base URL for the Function App API
    UPDATE_AGENT_API_KEY  - Function App key
Optional:
    UPDATE_AGENT_BLOB_ACCOUNT - Storage account name (default: autopkgapi)
"""

import glob
import json
import os
import plistlib
import subprocess
import sys
import urllib.request

CACHE_DIR = os.path.expanduser("~/Library/AutoPkg/Cache")
BLOB_CONTAINER = os.environ.get("UPDATE_AGENT_BLOB_CONTAINER", "packages")
BLOB_ACCOUNT = os.environ.get("UPDATE_AGENT_BLOB_ACCOUNT", "autopkgapi")


def find_packages_from_cache() -> list[dict]:
    """Scan AutoPkg cache for packages with metadata from receipts."""
    packages = []

    if not os.path.isdir(CACHE_DIR):
        print(f"  Cache directory not found: {CACHE_DIR}")
        return packages

    for recipe_dir_name in os.listdir(CACHE_DIR):
        recipe_path = os.path.join(CACHE_DIR, recipe_dir_name)
        if not os.path.isdir(recipe_path):
            continue

        # Find the most recent receipt for metadata
        bundle_id, version, app_name = "", "", ""
        receipts_dir = os.path.join(recipe_path, "receipts")
        if os.path.isdir(receipts_dir):
            for receipt_file in sorted(os.listdir(receipts_dir), reverse=True):
                if not receipt_file.endswith(".plist"):
                    continue
                try:
                    with open(os.path.join(receipts_dir, receipt_file), "rb") as f:
                        receipt = plistlib.load(f)
                    env = receipt.get("Environment", {})
                    bundle_id = env.get("bundleid", env.get("bundleID", env.get("BUNDLE_ID", "")))
                    version = env.get("version", env.get("VERSION", ""))
                    app_name = env.get("NAME", env.get("name", ""))
                    if bundle_id and version:
                        break
                except Exception:
                    continue

        if not bundle_id or not version:
            continue

        # Find .pkg or .dmg files
        for search_dir in [recipe_path, os.path.join(recipe_path, "downloads")]:
            if not os.path.isdir(search_dir):
                continue
            for filename in os.listdir(search_dir):
                if not (filename.endswith(".pkg") or filename.endswith(".dmg")):
                    continue
                filepath = os.path.join(search_dir, filename)
                if not os.path.isfile(filepath):
                    continue
                packages.append({
                    "bundle_id": bundle_id,
                    "version": version,
                    "app_name": app_name,
                    "filepath": filepath,
                    "filename": filename,
                })
                break  # Take first match per recipe

    return packages


def upload_to_blob(filepath: str, blob_path: str) -> bool:
    """Upload using az CLI with OIDC login session."""
    cmd = [
        "az", "storage", "blob", "upload",
        "--account-name", BLOB_ACCOUNT,
        "--container-name", BLOB_CONTAINER,
        "--name", blob_path,
        "--file", filepath,
        "--overwrite", "true",
        "--auth-mode", "login",
        "--no-progress",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"  ❌ Upload failed: {result.stderr[:200]}")
        return False
    return True


def register_version(bundle_id: str, app_name: str, version: str, blob_path: str) -> bool:
    """Call register-version API."""
    api_url = os.environ.get("UPDATE_AGENT_API_URL", "")
    api_key = os.environ.get("UPDATE_AGENT_API_KEY", "")
    if not api_url or not api_key:
        print("  ⚠️  API URL/key not set")
        return False

    url = f"{api_url}/register-version?code={api_key}"
    payload = json.dumps({
        "bundle_id": bundle_id,
        "app_name": app_name,
        "latest_version": version,
        "blob_path": blob_path,
    }).encode()

    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req) as resp:
            print(f"  ✅ Registered: {json.loads(resp.read())}")
            return True
    except Exception as exc:
        print(f"  ❌ API error: {exc}")
        return False


def main():
    print("📦 === REGISTER PACKAGES FOR UPDATE AGENT ===")
    print(f"  Cache: {CACHE_DIR}")
    print(f"  Blob:  {BLOB_ACCOUNT}/{BLOB_CONTAINER}")

    packages = find_packages_from_cache()

    if not packages:
        print("ℹ️  No packages with metadata found")
        return

    print(f"📋 Found {len(packages)} package(s):\n")

    for pkg in packages:
        bid = pkg["bundle_id"]
        ver = pkg["version"]
        name = pkg["app_name"]
        filepath = pkg["filepath"]
        filename = pkg["filename"]
        blob_path = f"{bid}/{filename}"

        print(f"📦 {name} ({bid}) v{ver}")
        print(f"   File: {filename} ({os.path.getsize(filepath) / 1024 / 1024:.1f} MB)")

        print(f"   ⬆️  Uploading to {BLOB_CONTAINER}/{blob_path}...")
        if upload_to_blob(filepath, blob_path):
            print(f"   ✅ Uploaded")
        else:
            print(f"   ⚠️  Upload failed, registering version anyway")

        register_version(bid, name, ver, blob_path)
        print()

    print("📦 === DONE ===")


if __name__ == "__main__":
    main()
