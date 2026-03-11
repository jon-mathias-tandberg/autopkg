#!/usr/bin/env python3
"""Post-AutoPkg: upload packages to Blob Storage and register versions.

Reads autopkg_results.plist from Cache to find package info,
uploads to Azure Blob Storage, and calls register-version API.
"""

import glob
import json
import os
import plistlib
import subprocess
import sys
import urllib.request

CACHE_DIR = os.path.expanduser("~/Library/AutoPkg/Cache")
REPORT_PLIST = "/tmp/autopkg.plist"
BLOB_CONTAINER = os.environ.get("UPDATE_AGENT_BLOB_CONTAINER", "packages")
BLOB_ACCOUNT = os.environ.get("UPDATE_AGENT_BLOB_ACCOUNT", "autopkgapi")


def find_packages_from_autopkg_output() -> list[dict]:
    """Parse AutoPkg output log to extract bundleID, version, and package paths."""
    packages = []

    for recipe_dir in sorted(os.listdir(CACHE_DIR)):
        recipe_path = os.path.join(CACHE_DIR, recipe_dir)
        if not os.path.isdir(recipe_path):
            continue

        receipts_dir = os.path.join(recipe_path, "receipts")
        if not os.path.isdir(receipts_dir):
            continue

        # Read the most recent receipt
        receipt_files = sorted(
            [f for f in os.listdir(receipts_dir) if f.endswith(".plist")],
            reverse=True,
        )
        if not receipt_files:
            continue

        try:
            with open(os.path.join(receipts_dir, receipt_files[0]), "rb") as f:
                receipt_data = plistlib.load(f)
        except Exception:
            continue

        # Receipt is a list of dicts, each with "Recipe input" and "Output"
        if isinstance(receipt_data, list):
            env = {}
            for step in receipt_data:
                if isinstance(step, dict):
                    # Merge Recipe input
                    ri = step.get("Recipe input", {})
                    if isinstance(ri, dict):
                        env.update(ri)
                    # Merge Output
                    out = step.get("Output", {})
                    if isinstance(out, dict):
                        env.update(out)
        elif isinstance(receipt_data, dict):
            env = receipt_data.get("Environment", receipt_data)
        else:
            continue

        # Extract metadata from merged env
        bundle_id = env.get("bundleID", env.get("bundleid", env.get("BUNDLE_ID", "")))
        version = env.get("version", "")
        app_name = env.get("NAME", env.get("display_name", ""))

        if not bundle_id or not version:
            continue

        # Find .pkg or .dmg
        pkg_file = None
        for search_dir in [recipe_path, os.path.join(recipe_path, "downloads")]:
            if not os.path.isdir(search_dir):
                continue
            for fn in os.listdir(search_dir):
                if fn.endswith(".pkg") or fn.endswith(".dmg"):
                    pkg_file = os.path.join(search_dir, fn)
                    break
            if pkg_file:
                break

        if not pkg_file:
            continue

        packages.append({
            "bundle_id": bundle_id,
            "version": version,
            "app_name": app_name,
            "filepath": pkg_file,
            "filename": os.path.basename(pkg_file),
        })

    return packages


def upload_to_blob(filepath: str, blob_path: str) -> bool:
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


def register_version(bundle_id, app_name, version, blob_path) -> bool:
    api_url = os.environ.get("UPDATE_AGENT_API_URL", "")
    api_key = os.environ.get("UPDATE_AGENT_API_KEY", "")
    if not api_url or not api_key:
        return False
    url = f"{api_url}/register-version?code={api_key}"
    payload = json.dumps({
        "bundle_id": bundle_id, "app_name": app_name,
        "latest_version": version, "blob_path": blob_path,
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
    packages = find_packages_from_autopkg_output()
    if not packages:
        print("ℹ️  No packages with metadata found")
        return
    print(f"📋 Found {len(packages)} package(s):\n")
    for pkg in packages:
        bid, ver, name = pkg["bundle_id"], pkg["version"], pkg["app_name"]
        fn, fp = pkg["filename"], pkg["filepath"]
        blob_path = f"{bid}/{fn}"
        print(f"📦 {name} ({bid}) v{ver}")
        print(f"   File: {fn} ({os.path.getsize(fp)/1024/1024:.1f} MB)")
        print(f"   ⬆️  Uploading...")
        upload_to_blob(fp, blob_path)
        register_version(bid, name, ver, blob_path)
        print()
    print("📦 === DONE ===")


if __name__ == "__main__":
    main()
