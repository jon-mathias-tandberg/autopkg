#!/bin/bash
# setup_update_agent.sh – One-time Intune Platform Script
#
# Creates the Update Agent configuration directory and config file.
# Deploy once before update_agent.sh.
#
# IMPORTANT: Edit the api_base_url and api_key values below before deploying.

set -euo pipefail

AGENT_DIR="/Library/Application Support/UpdateAgent"
mkdir -p "${AGENT_DIR}/logs" "${AGENT_DIR}/cache"

# Only write config if it doesn't exist (don't overwrite manual changes)
if [[ ! -f "${AGENT_DIR}/config.json" ]]; then
    cat > "${AGENT_DIR}/config.json" << 'EOF'
{
    "api_base_url": "https://YOUR-FUNCTION-APP.azurewebsites.net/api",
    "api_key": "YOUR-FUNCTION-KEY",
    "max_deferrals": 3,
    "dialog_timeout_seconds": 300,
    "work_hours_start": 8,
    "work_hours_end": 17,
    "scan_paths": ["/Applications", "/Applications/Utilities"]
}
EOF
    chmod 600 "${AGENT_DIR}/config.json"
    echo "Update Agent configured at ${AGENT_DIR}"
else
    echo "Config already exists, skipping"
fi
