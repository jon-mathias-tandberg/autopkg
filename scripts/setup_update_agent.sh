#!/bin/bash
# setup_update_agent.sh – One-time Intune Platform Script
#
# Creates the Update Agent configuration and stores the API key
# securely in the macOS System Keychain.
#
# IMPORTANT: Edit the api_base_url and API_KEY values below before deploying.

set -euo pipefail

AGENT_DIR="/Library/Application Support/UpdateAgent"
KEYCHAIN="/Library/Keychains/System.keychain"
KEYCHAIN_SERVICE="UpdateAgentAPIKey"
KEYCHAIN_ACCOUNT="UpdateAgent"

# === EDIT THESE VALUES ===
API_BASE_URL="https://autopkg-api-func-XXXXX.norwayeast-01.azurewebsites.net/api"
API_KEY="YOUR-FUNCTION-KEY-FROM-APP-KEYS"
# =========================

mkdir -p "${AGENT_DIR}/logs" "${AGENT_DIR}/cache"

# Store API key in System Keychain (encrypted, only accessible by root)
if security find-generic-password -a "$KEYCHAIN_ACCOUNT" -s "$KEYCHAIN_SERVICE" "$KEYCHAIN" &>/dev/null; then
    # Update existing entry
    security delete-generic-password -a "$KEYCHAIN_ACCOUNT" -s "$KEYCHAIN_SERVICE" "$KEYCHAIN" &>/dev/null || true
fi
security add-generic-password \
    -a "$KEYCHAIN_ACCOUNT" \
    -s "$KEYCHAIN_SERVICE" \
    -w "$API_KEY" \
    -T /usr/bin/security \
    -T /usr/bin/curl \
    "$KEYCHAIN"
echo "✅ API key stored in System Keychain"

# Write config WITHOUT api_key (it's in Keychain now)
if [[ ! -f "${AGENT_DIR}/config.json" ]]; then
    cat > "${AGENT_DIR}/config.json" << EOF
{
    "api_base_url": "${API_BASE_URL}",
    "max_deferrals": 3,
    "dialog_timeout_seconds": 300,
    "work_hours_start": 8,
    "work_hours_end": 17,
    "scan_paths": ["/Applications", "/Applications/Utilities"]
}
EOF
    chmod 644 "${AGENT_DIR}/config.json"
    echo "✅ Config written to ${AGENT_DIR}/config.json"
else
    echo "ℹ️  Config already exists, skipping"
fi

echo "✅ Update Agent setup complete"
