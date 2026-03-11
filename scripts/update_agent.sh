#!/bin/bash
# update_agent.sh – Intune Platform Script for macOS app updates
#
# Scans installed apps, checks for updates via Azure Function API,
# prompts user via osascript, and installs updates outside work hours.
# Forced updates (3+ deferrals) install immediately.
#
# Deploy: Intune > Devices > macOS > Shell scripts
#   Run as: root (not signed-in user)
#   Frequency: Every 8 hours (configurable)

set -euo pipefail

# ============================================================
# CONFIGURATION
# ============================================================
AGENT_DIR="/Library/Application Support/UpdateAgent"
CONFIG_FILE="${AGENT_DIR}/config.json"
DEFERRALS_FILE="${AGENT_DIR}/deferrals.json"
PENDING_FILE="${AGENT_DIR}/pending_updates.json"
LOG_FILE="${AGENT_DIR}/logs/update_agent.log"

# Defaults (overridden by config.json if present)
API_BASE_URL=""
API_KEY=""
MAX_DEFERRALS=3
DIALOG_TIMEOUT=300
WORK_HOURS_START=8
WORK_HOURS_END=17

# ============================================================
# INIT
# ============================================================
mkdir -p "${AGENT_DIR}/logs" "${AGENT_DIR}/cache"

KEYCHAIN="/Library/Keychains/System.keychain"
KEYCHAIN_SERVICE="UpdateAgentAPIKey"
KEYCHAIN_ACCOUNT="UpdateAgent"

load_config() {
    if [[ ! -f "$CONFIG_FILE" ]]; then
        log "ERROR" "Config file not found: ${CONFIG_FILE}"
        exit 1
    fi
    API_BASE_URL=$(python3 -c "import json; print(json.load(open('${CONFIG_FILE}'))['api_base_url'])")
    MAX_DEFERRALS=$(python3 -c "import json; print(json.load(open('${CONFIG_FILE}')).get('max_deferrals', 3))")
    DIALOG_TIMEOUT=$(python3 -c "import json; print(json.load(open('${CONFIG_FILE}')).get('dialog_timeout_seconds', 300))")
    WORK_HOURS_START=$(python3 -c "import json; print(json.load(open('${CONFIG_FILE}')).get('work_hours_start', 8))")
    WORK_HOURS_END=$(python3 -c "import json; print(json.load(open('${CONFIG_FILE}')).get('work_hours_end', 17))")

    # Read API key from System Keychain (secure, encrypted)
    API_KEY=$(security find-generic-password -a "$KEYCHAIN_ACCOUNT" -s "$KEYCHAIN_SERVICE" -w "$KEYCHAIN" 2>/dev/null) || {
        log "ERROR" "API key not found in System Keychain. Run setup_update_agent.sh first."
        exit 1
    }
}

# ============================================================
# LOGGING
# ============================================================
log() {
    echo "$(date '+%Y-%m-%d %H:%M:%S') [$1] $2" >> "$LOG_FILE"
}

# ============================================================
# USER CONTEXT HELPERS
# ============================================================
get_current_user() {
    stat -f "%Su" /dev/console
}

get_current_user_uid() {
    id -u "$(get_current_user)"
}

run_as_user() {
    local current_user uid
    current_user=$(get_current_user)
    uid=$(get_current_user_uid)

    if [[ "$current_user" == "root" || "$current_user" == "loginwindow" ]]; then
        log "WARN" "No user logged in, skipping dialog"
        return 1
    fi
    launchctl asuser "$uid" sudo -u "$current_user" "$@"
}

user_is_logged_in() {
    local current_user
    current_user=$(get_current_user)
    [[ "$current_user" != "root" && "$current_user" != "loginwindow" ]]
}

# ============================================================
# TIME HELPERS
# ============================================================
is_within_work_hours() {
    local hour dow
    hour=$((10#$(date +%H)))
    dow=$((10#$(date +%u)))  # 1=mon 7=sun
    [[ $dow -le 5 && $hour -ge $WORK_HOURS_START && $hour -lt $WORK_HOURS_END ]]
}

# ============================================================
# DEVICE ID
# ============================================================
get_device_id() {
    local serial
    serial=$(ioreg -d2 -c IOPlatformExpertDevice | awk -F'"' '/IOPlatformSerialNumber/{print $4}')
    echo -n "$serial" | shasum -a 256 | awk '{print $1}'
}

# ============================================================
# APP SCANNING
# ============================================================
scan_applications() {
    python3 << 'PYEOF'
import glob, json, os, plistlib

apps = []
for pattern in ["/Applications/*.app", "/Applications/Utilities/*.app"]:
    for app_dir in sorted(glob.glob(pattern)):
        plist_path = os.path.join(app_dir, "Contents", "Info.plist")
        if not os.path.isfile(plist_path):
            continue
        try:
            with open(plist_path, "rb") as f:
                info = plistlib.load(f)
        except Exception:
            continue
        bid = info.get("CFBundleIdentifier", "")
        if not bid:
            continue
        apps.append({
            "bundle_id": bid,
            "version": info.get("CFBundleShortVersionString", "0"),
            "name": info.get("CFBundleName", os.path.basename(app_dir).replace(".app", "")),
        })
print(json.dumps(apps))
PYEOF
}

# ============================================================
# API COMMUNICATION
# ============================================================
check_updates() {
    local installed_apps="$1"
    local device_id
    device_id=$(get_device_id)

    curl -sf -X POST "${API_BASE_URL}/check-updates" \
        -H "Content-Type: application/json" \
        -H "x-functions-key: ${API_KEY}" \
        -d "{\"device_id\":\"${device_id}\",\"installed_apps\":${installed_apps}}" \
        2>> "$LOG_FILE"
}

report_event() {
    local bundle_id="$1" action="$2" from_version="$3" to_version="$4"
    local device_id
    device_id=$(get_device_id)

    curl -sf -X POST "${API_BASE_URL}/log-event" \
        -H "Content-Type: application/json" \
        -H "x-functions-key: ${API_KEY}" \
        -d "{\"device_id\":\"${device_id}\",\"bundle_id\":\"${bundle_id}\",\"action\":\"${action}\",\"from_version\":\"${from_version}\",\"to_version\":\"${to_version}\"}" \
        2>> "$LOG_FILE" || true
}

# ============================================================
# DEFERRAL MANAGEMENT
# ============================================================
categorize_updates() {
    local updates_json="$1"
    python3 - "$updates_json" "$DEFERRALS_FILE" "$MAX_DEFERRALS" << 'PYEOF'
import json, os, sys

updates = json.loads(sys.argv[1])
deferrals_file = sys.argv[2]
max_def = int(sys.argv[3])

deferrals = {}
if os.path.isfile(deferrals_file):
    with open(deferrals_file) as f:
        deferrals = json.load(f)

promptable, forced = [], []
min_remaining = max_def

for u in updates:
    bid = u["bundle_id"]
    ver = u["latest_version"]
    entry = deferrals.get(bid, {})
    count = entry.get("count", 0) if entry.get("version_available") == ver else 0

    if count >= max_def:
        forced.append(u)
    else:
        remaining = max_def - count
        u["remaining_deferrals"] = remaining
        if remaining < min_remaining:
            min_remaining = remaining
        promptable.append(u)

result = {
    "promptable": promptable,
    "forced": forced,
    "min_remaining": min_remaining,
}
print(json.dumps(result))
PYEOF
}

increment_deferrals() {
    local updates_json="$1"
    python3 - "$updates_json" "$DEFERRALS_FILE" << 'PYEOF'
import json, os, sys
from datetime import datetime, timezone

updates = json.loads(sys.argv[1])
path = sys.argv[2]

data = {}
if os.path.isfile(path):
    with open(path) as f:
        data = json.load(f)

now = datetime.now(timezone.utc).isoformat()
for u in updates:
    bid = u["bundle_id"]
    ver = u.get("latest_version", "")
    entry = data.get(bid, {"count": 0})
    if entry.get("version_available") != ver:
        entry = {"count": 0, "first_seen": now}
    entry["count"] = entry.get("count", 0) + 1
    entry["version_available"] = ver
    entry["last_deferred"] = now
    data[bid] = entry

with open(path, "w") as f:
    json.dump(data, f, indent=2)
PYEOF
}

clear_deferrals() {
    local updates_json="$1"
    python3 - "$updates_json" "$DEFERRALS_FILE" << 'PYEOF'
import json, os, sys

updates = json.loads(sys.argv[1])
path = sys.argv[2]

if not os.path.isfile(path):
    sys.exit(0)

with open(path) as f:
    data = json.load(f)

for u in updates:
    data.pop(u["bundle_id"], None)

with open(path, "w") as f:
    json.dump(data, f, indent=2)
PYEOF
}

# ============================================================
# FORMAT APP LIST FOR DIALOG
# ============================================================
format_app_list() {
    local updates_json="$1"
    python3 -c "
import json, sys
updates = json.loads(sys.argv[1])
for u in updates:
    name = u.get('app_name', u['bundle_id'])
    old_v = u.get('installed_version', '?')
    new_v = u.get('latest_version', '?')
    print(f'  \u2022 {name}  ({old_v} \u2192 {new_v})')
" "$updates_json"
}

# ============================================================
# OSASCRIPT DIALOGS & NOTIFICATIONS
# ============================================================

# ── Notifications ──

# Find terminal-notifier (check common paths since sudo has limited PATH)
NOTIFIER=""
for _p in /opt/homebrew/bin/terminal-notifier /usr/local/bin/terminal-notifier; do
    [[ -x "$_p" ]] && NOTIFIER="$_p" && break
done

# Find app icon (.icns) for a given app name
find_app_icon() {
    local app_name="$1"
    local icon=""
    for app_path in "/Applications/${app_name}.app" "/Applications/Utilities/${app_name}.app"; do
        if [[ -d "$app_path" ]]; then
            icon=$(defaults read "${app_path}/Contents/Info" CFBundleIconFile 2>/dev/null) || icon=""
            if [[ -n "$icon" ]]; then
                [[ "$icon" != *.icns ]] && icon="${icon}.icns"
                icon="${app_path}/Contents/Resources/${icon}"
                [[ -f "$icon" ]] && echo "$icon" && return
            fi
        fi
    done
    echo ""
}

show_app_update_notification() {
    local app_name="$1"
    local new_version="$2"
    local bundle_id="${3:-}"

    local message="Versjon ${new_version} installeres neste gang du lukker ${app_name}."

    if [[ -n "$NOTIFIER" ]]; then
        local notifier_args=(
            -title "Oppdatering klar"
            -subtitle "$app_name"
            -message "$message"
            -sound default
        )
        [[ -n "$bundle_id" ]] && notifier_args+=(-sender "$bundle_id")
        run_as_user "$NOTIFIER" "${notifier_args[@]}" 2>/dev/null || true
    else
        run_as_user osascript -e "
            display notification \"${message}\" with title \"Oppdatering klar\" subtitle \"${app_name}\"
        " 2>/dev/null || true
    fi
}

show_notification() {
    local title="$1"
    local message="$2"

    if [[ -n "$NOTIFIER" ]]; then
        run_as_user "$NOTIFIER" \
            -title "$title" \
            -message "$message" 2>/dev/null || true
    else
        run_as_user osascript -e "
            display notification \"${message}\" with title \"${title}\"
        " 2>/dev/null || true
    fi
}

# ── Dialogs (blocking, for forced updates only) ──

show_forced_dialog() {
    local app_list="$1"

    run_as_user osascript -e "
        display alert \"Obligatorisk oppdatering\" message \"Følgende oppdateringer kan ikke utsettes lenger:\" & return & return & \"${app_list}\" & return & return & \"Lukk appene for å fullføre oppdateringen.\" as critical buttons {\"OK\"} default button \"OK\" giving up after 60
    " 2>/dev/null || true
}

# ============================================================
# WAIT FOR APP TO QUIT
# ============================================================
MAX_QUIT_WAIT=600       # Max seconds to wait for app to close (10 min)
QUIT_CHECK_INTERVAL=5   # Seconds between checks

app_is_running() {
    local bundle_id="$1"
    pgrep -f "CFBundleIdentifier.*${bundle_id}" &>/dev/null && return 0
    # More reliable: check via lsappinfo or osascript
    local running
    running=$(osascript -e "
        tell application \"System Events\"
            set bundleIds to bundle identifier of every process
        end tell
        if bundleIds contains \"${bundle_id}\" then
            return \"yes\"
        else
            return \"no\"
        end if
    " 2>/dev/null) || running="no"
    [[ "$running" == "yes" ]]
}

get_app_name_for_bundle() {
    local bundle_id="$1"
    osascript -e "
        tell application \"System Events\"
            try
                set appName to displayed name of (first process whose bundle identifier is \"${bundle_id}\")
                return appName
            on error
                return \"\"
            end try
        end tell
    " 2>/dev/null || echo ""
}

wait_for_app_to_quit() {
    local bundle_id="$1"

    if ! app_is_running "$bundle_id"; then
        return 0
    fi

    local app_display_name
    app_display_name=$(get_app_name_for_bundle "$bundle_id")
    if [[ -z "$app_display_name" ]]; then
        app_display_name="$bundle_id"
    fi

    log "INFO" "App running: ${app_display_name} (${bundle_id}) – asking user to close"

    local waited=0
    local prompted=false

    while app_is_running "$bundle_id"; do
        if [[ $waited -ge $MAX_QUIT_WAIT ]]; then
            log "WARN" "Timed out waiting for ${app_display_name} to quit after ${MAX_QUIT_WAIT}s"
            if user_is_logged_in; then
                run_as_user osascript -e "
                    display alert \"Kunne ikke oppdatere ${app_display_name}\" message \"Programmet ble ikke lukket innen tidsfristen. Oppdateringen vil bli forsøkt igjen ved neste kjøring.\" as warning buttons {\"OK\"} default button \"OK\" giving up after 30
                " 2>/dev/null || true
            fi
            return 1
        fi

        if ! $prompted && user_is_logged_in; then
            prompted=true
            # Ask user to close the app – non-blocking approach:
            # First try graceful quit via osascript, then show dialog
            local user_choice
            user_choice=$(run_as_user osascript -e "
                try
                    set result to display dialog \"${app_display_name} må lukkes for å installere oppdateringen.\" & return & return & \"Vil du lukke ${app_display_name} nå?\" with title \"Oppdatering venter\" buttons {\"Lukk ${app_display_name}\", \"Vent\"} default button \"Lukk ${app_display_name}\" giving up after 120 with icon caution
                    if gave up of result then
                        return \"wait\"
                    end if
                    return button returned of result
                on error
                    return \"wait\"
                end try
            " 2>/dev/null) || user_choice="wait"

            if [[ "$user_choice" == *"Lukk"* ]]; then
                log "INFO" "User chose to quit ${app_display_name}"
                osascript -e "
                    try
                        tell application id \"${bundle_id}\" to quit
                    end try
                " 2>/dev/null || true
                sleep 3
                # If still running after graceful quit, wait longer
                if app_is_running "$bundle_id"; then
                    log "INFO" "App still running after quit request, waiting..."
                fi
            fi
        fi

        sleep "$QUIT_CHECK_INTERVAL"
        waited=$((waited + QUIT_CHECK_INTERVAL))
    done

    log "INFO" "${app_display_name} has quit, proceeding with install"
    return 0
}

# ============================================================
# PACKAGE INSTALLATION (runs as root)
# ============================================================
install_update() {
    local bundle_id="$1"
    local download_url="$2"
    local download_filename="$3"

    if [[ -z "$download_url" ]]; then
        log "WARN" "No download URL for ${bundle_id}, skipping"
        return 1
    fi

    # Wait for the app to quit before installing
    if ! wait_for_app_to_quit "$bundle_id"; then
        log "WARN" "Skipping ${bundle_id} – app still running"
        return 1
    fi

    log "INFO" "Downloading ${bundle_id}: ${download_filename}"
    local temp_file="${AGENT_DIR}/cache/${download_filename}"

    if ! curl -sfL -o "$temp_file" "$download_url" 2>> "$LOG_FILE"; then
        log "ERROR" "Download failed for ${bundle_id}"
        rm -f "$temp_file"
        return 1
    fi

    # Final check right before overwriting – app might have reopened
    if app_is_running "$bundle_id"; then
        log "WARN" "App reopened during download, aborting install for ${bundle_id}"
        rm -f "$temp_file"
        return 1
    fi

    if [[ "$temp_file" == *.pkg ]]; then
        log "INFO" "Installing ${bundle_id} via pkg"
        if installer -pkg "$temp_file" -target / >> "$LOG_FILE" 2>&1; then
            log "INFO" "Successfully installed ${bundle_id}"
        else
            log "ERROR" "installer failed for ${bundle_id}"
            rm -f "$temp_file"
            return 1
        fi
    elif [[ "$temp_file" == *.dmg ]]; then
        log "INFO" "Installing ${bundle_id} via dmg"
        local mount_point
        mount_point=$(hdiutil attach -nobrowse -noautoopen "$temp_file" 2>> "$LOG_FILE" | tail -1 | awk '{$1=$2=""; print $0}' | xargs)
        if [[ -n "$mount_point" ]]; then
            local app_found=false
            for app_path in "${mount_point}"/*.app; do
                if [[ -d "$app_path" ]]; then
                    local app_name
                    app_name=$(basename "$app_path")
                    rm -rf "/Applications/${app_name}"
                    cp -R "$app_path" /Applications/
                    log "INFO" "Copied ${app_name} to /Applications"
                    app_found=true
                    break
                fi
            done
            hdiutil detach "$mount_point" -quiet 2>/dev/null || true
            if ! $app_found; then
                log "ERROR" "No .app found in dmg for ${bundle_id}"
                rm -f "$temp_file"
                return 1
            fi
        else
            log "ERROR" "Failed to mount dmg for ${bundle_id}"
            rm -f "$temp_file"
            return 1
        fi
    else
        log "ERROR" "Unknown file type for ${bundle_id}: ${download_filename}"
        rm -f "$temp_file"
        return 1
    fi

    rm -f "$temp_file"
    return 0
}

install_updates_from_json() {
    local updates_json="$1"
    local action="$2"

    python3 -c "
import json, sys
updates = json.loads(sys.argv[1])
for u in updates:
    print(u['bundle_id'], u.get('download_url',''), u.get('download_filename',''), u.get('installed_version',''), u.get('latest_version',''), sep='|||')
" "$updates_json" | while IFS='|||' read -r bid dl_url dl_file old_ver new_ver; do
        if install_update "$bid" "$dl_url" "$dl_file"; then
            report_event "$bid" "$action" "$old_ver" "$new_ver"
        else
            log "ERROR" "Failed to install ${bid}"
        fi
    done

    clear_deferrals "$updates_json"
}

# ============================================================
# MAIN
# ============================================================
main() {
    log "INFO" "========================================="
    log "INFO" "Update Agent started"

    load_config

    # ── STEP 1: Scan installed apps ──
    log "INFO" "Scanning installed apps..."
    local installed_apps
    installed_apps=$(scan_applications)

    # ── STEP 2: Check API for updates (always get fresh SAS URLs) ──
    log "INFO" "Checking for updates..."
    local api_response
    if ! api_response=$(check_updates "$installed_apps"); then
        log "ERROR" "API call failed"
        exit 1
    fi

    # ── STEP 3: Extract available updates ──
    local updates_available
    updates_available=$(python3 -c "import json,sys; print(json.dumps(json.loads(sys.argv[1]).get('updates_available',[])))" "$api_response")

    # ── STEP 3b: Try installing pending updates with fresh SAS URLs ──
    if [[ -f "$PENDING_FILE" ]]; then
        log "INFO" "Found pending updates – refreshing SAS URLs and attempting install"

        # Match pending bundle IDs against fresh API response
        local refreshed
        refreshed=$(python3 -c "
import json, sys
pending = json.loads(sys.argv[1])
fresh = json.loads(sys.argv[2])
fresh_map = {u['bundle_id']: u for u in fresh}
result = []
for p in pending:
    bid = p['bundle_id']
    if bid in fresh_map:
        result.append(fresh_map[bid])
print(json.dumps(result))
" "$(cat "$PENDING_FILE")" "$updates_available")

        local refreshed_count
        refreshed_count=$(python3 -c "import json,sys; print(len(json.loads(sys.argv[1])))" "$refreshed")

        if [[ "$refreshed_count" -gt 0 ]]; then
            install_updates_from_json "$refreshed" "updated"
        fi
        rm -f "$PENDING_FILE"
        log "INFO" "Pending updates processed"

        # Remove installed apps from updates_available
        updates_available=$(python3 -c "
import json, sys
available = json.loads(sys.argv[1])
installed = {u['bundle_id'] for u in json.loads(sys.argv[2])}
print(json.dumps([u for u in available if u['bundle_id'] not in installed]))
" "$updates_available" "$refreshed")
    fi

    local update_count
    update_count=$(python3 -c "import json,sys; print(len(json.loads(sys.argv[1])))" "$updates_available")

    if [[ "$update_count" -eq 0 ]]; then
        log "INFO" "No updates available"
        exit 0
    fi
    log "INFO" "${update_count} update(s) available"

    # ── STEP 4: Categorize into promptable vs forced ──
    local categorized
    categorized=$(categorize_updates "$updates_available")

    local forced_json promptable_json min_remaining
    forced_json=$(python3 -c "import json,sys; print(json.dumps(json.loads(sys.argv[1])['forced']))" "$categorized")
    promptable_json=$(python3 -c "import json,sys; print(json.dumps(json.loads(sys.argv[1])['promptable']))" "$categorized")
    min_remaining=$(python3 -c "import json,sys; print(json.loads(sys.argv[1])['min_remaining'])" "$categorized")

    local forced_count promptable_count
    forced_count=$(python3 -c "import json,sys; print(len(json.loads(sys.argv[1])))" "$forced_json")
    promptable_count=$(python3 -c "import json,sys; print(len(json.loads(sys.argv[1])))" "$promptable_json")

    # ── STEP 5: Force-install updates with 3+ deferrals (immediate) ──
    if [[ "$forced_count" -gt 0 ]]; then
        log "INFO" "Forcing IMMEDIATE install of ${forced_count} app(s) (3+ deferrals)"
        local forced_list
        forced_list=$(format_app_list "$forced_json")

        if user_is_logged_in; then
            show_forced_dialog "$forced_list"
        fi

        install_updates_from_json "$forced_json" "forced"
    fi

    # ── STEP 6: Notify user and queue updates for installation ──
    if [[ "$promptable_count" -gt 0 ]]; then
        # Queue updates for installation (wait-for-quit handles timing)
        echo "$promptable_json" > "$PENDING_FILE"
        log "INFO" "Queued ${promptable_count} update(s) for installation"

        # Show per-app notification if user is logged in
        if user_is_logged_in; then
            python3 -c "
import json, sys
for u in json.loads(sys.argv[1]):
    print(u.get('app_name', u['bundle_id']), u.get('latest_version', '?'), u['bundle_id'], sep='|||')
" "$promptable_json" | while IFS='|||' read -r app_name new_ver bid; do
                show_app_update_notification "$app_name" "$new_ver" "$bid"
                sleep 1
            done
        fi

        # Increment deferral counter
        increment_deferrals "$promptable_json"
        python3 -c "
import json, sys
for u in json.loads(sys.argv[1]):
    print(u['bundle_id'], u.get('installed_version',''), u.get('latest_version',''), sep='|||')
" "$promptable_json" | while IFS='|||' read -r bid old_ver new_ver; do
            report_event "$bid" "scheduled" "$old_ver" "$new_ver"
        done
    fi

    log "INFO" "Update Agent finished"
}

main "$@"
