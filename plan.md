# Plan: macOS Update Agent med AutoPkg + Intune

## Prosjektoversikt

Bygge et komplett system for å oppdage, varsle og installere app-oppdateringer på bedriftens macOS-maskiner, integrert med eksisterende AutoPkg + Intune-pipeline.

### Arkitekturdiagram

```
┌──────────────────────────────────────────────────────────────────────────┐
│                         ADMIN / CI PIPELINE                             │
│                                                                         │
│  ┌─────────────┐    ┌──────────────────┐    ┌───────────────────────┐   │
│  │  AutoPkg    │───▶│ IntuneAppPackager│───▶│ Intune (Graph API)    │   │
│  │  Resepter   │    │ (upload .intune- │    │ - App catalog         │   │
│  │             │    │  mac)            │    │ - Assignments         │   │
│  └─────────────┘    └──────────────────┘    └───────────┬───────────┘   │
│        │                                                │               │
│        ▼                                                │               │
│  ┌─────────────────────┐                                │               │
│  │ VersionReporter     │                                │               │
│  │ (AutoPkg Post-      │                                │               │
│  │  processor)         │                                │               │
│  └─────────┬───────────┘                                │               │
│            │ POST /api/register-version                 │               │
│            ▼                                            ▼               │
│  ┌─────────────────────────────────────────────────────────────────┐    │
│  │                    Azure Function App                           │    │
│  │                                                                 │    │
│  │  POST /api/register-version  ← AutoPkg melder ny versjon       │    │
│  │  POST /api/check-updates     ← Klient sender bundle IDs        │    │
│  │  POST /api/request-app       ← Klient ber om ny app (fremtidig)│    │
│  │  GET  /api/managed-apps      ← Liste over tilgjengelige apper  │    │
│  │                                                                 │    │
│  │  ┌─────────────────┐  ┌──────────────────────┐                 │    │
│  │  │ Azure Table     │  │ Microsoft Graph API   │                 │    │
│  │  │ Storage         │  │ (Intune)              │                 │    │
│  │  │ - Versjoner     │  │ - Managed apps        │                 │    │
│  │  │ - App-metadata  │  │ - Device assignments  │                 │    │
│  │  └─────────────────┘  └──────────────────────┘                 │    │
│  └─────────────────────────────────────────────────────────────────┘    │
└──────────────────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────────────────┐
│                         macOS KLIENT                                    │
│                                                                         │
│  ┌──────────────────────────────────────────┐                           │
│  │  Intune Platform Script (kjører som root)│                           │
│  │  update_agent.sh                         │                           │
│  │                                          │                           │
│  │  1. Skann /Applications → bundle IDs     │                           │
│  │  2. POST /api/check-updates              │                           │
│  │  3. Motta oppdateringsliste              │                           │
│  │  4. Les utsettelsesteller                │                           │
│  │  5. Vis osascript-dialog til bruker       │  ← launchctl asuser       │
│  │     (via launchctl asuser)               │                           │
│  │  6a. "Oppdater" → installer direkte      │  ← Har root-tilgang!     │
│  │  6b. "Utsett"  → oppdater teller         │                           │
│  │  7. Etter 3 utsettelser → tving          │                           │
│  │  8. Logg resultat til API                │                           │
│  │                                          │                           │
│  │  ┌────────────────────────────────────┐  │                           │
│  │  │ /Library/Application Support/      │  │                           │
│  │  │   UpdateAgent/                     │  │                           │
│  │  │   ├── deferrals.json (utsettelser) │  │                           │
│  │  │   ├── config.json (innstillinger)  │  │                           │
│  │  │   └── cache/ (nedlastede pakker)   │  │                           │
│  │  └────────────────────────────────────┘  │                           │
│  └──────────────────────────────────────────┘                           │
│                                                                         │
│  ┌──────────────────────────────────────────┐                           │
│  │  osascript (innebygd macOS dialog)       │                           │
│  │  - AppleScript display dialog/alert      │                           │
│  │  - Liste over tilgjengelige oppdateringer│                           │
│  │  - "Oppdater i kveld" / "Utsett (X)"    │                           │
│  └──────────────────────────────────────────┘                           │
└──────────────────────────────────────────────────────────────────────────┘
```

### Hvorfor Intune Platform Script?

| Fordel | Beskrivelse |
|--------|-------------|
| **Root-tilgang** | Scriptet kjører som root → kan installere pakker direkte uten MDM roundtrip |
| **Innebygd scheduling** | Intune styrer intervallet (f.eks. hver 8. time) |
| **Enkel distribusjon** | Én fil lastes opp i Intune → ingen pakkering nødvendig |
| **Enkel oppdatering** | Oppdater scriptet i Intune-portalen → rulles ut automatisk |
| **Ingen ekstra komponenter** | Ingen LaunchAgent, ingen Python-runtime, ingen ekstra UI-verktøy å distribuere. `osascript` er innebygd. |
| **Compliance-rapportering** | Intune logger script-kjøringer automatisk |

> **Viktig**: Selv om scriptet kjører som root, vises `osascript`-dialoger i brukerens
> kontekst via `launchctl asuser`. Brukeren ser en vanlig macOS-dialog. Ingen ekstra
> avhengigheter – `osascript` er innebygd i macOS.

---

## Faseoversikt

| Fase | Beskrivelse | Estimat | Avhengigheter |
|------|-------------|---------|---------------|
| **1** | Azure Function App (API) | 2-3 dager | Azure-tilgang |
| **2** | AutoPkg VersionReporter postprocessor | 1 dag | Fase 1 |
| **3** | Platform Script (bash, kjernefunksjonalitet + UI + utsettelser) | 3-4 dager | Fase 1 |
| **4** | Intune-oppsett og testing | 1-2 dager | Fase 1-3 |
| **5** | App-request-funksjonalitet (valgfri) | 2-3 dager | Fase 1 |

---

## Fase 1: Azure Function App

### 1.1 Oppsett

- **Runtime**: Python 3.10 (matcher AutoPkg)
- **Hosting plan**: Consumption (serverless)
- **Storage**: Azure Table Storage
- **Autentisering**: 
  - Klient → Function: API-nøkkel (Function Key)
  - Function → Graph API: Managed Identity + App Registration

### 1.2 Storage-arkitektur

**Azure Table Storage – `ManagedApps`**

| Felt | Type | Beskrivelse |
|------|------|-------------|
| `PartitionKey` | string | `"apps"` (fast verdi) |
| `RowKey` | string | Bundle ID (f.eks. `org.mozilla.firefox`) |
| `app_name` | string | Visningsnavn |
| `latest_version` | string | Siste tilgjengelige versjon |
| `intune_app_id` | string | App-ID i Intune |
| `blob_path` | string | Sti til pakken i Blob Storage (f.eks. `packages/firefox/Firefox-115.0.1.pkg`) |
| `min_os_version` | string | Minimum macOS-versjon |
| `updated_at` | datetime | Sist oppdatert |
| `updated_by` | string | Hvem/hva som oppdaterte (AutoPkg resept-ID) |

**Azure Table Storage – `UpdateEvents`** (logging/audit)

| Felt | Type | Beskrivelse |
|------|------|-------------|
| `PartitionKey` | string | Enhetens serienummer (hashet) |
| `RowKey` | string | Timestamp + bundle ID |
| `action` | string | `"updated"`, `"deferred"`, `"forced"`, `"scheduled"` |
| `from_version` | string | Gammel versjon |
| `to_version` | string | Ny versjon |

**Azure Blob Storage – `packages` container**

AutoPkg laster opp ferdige .pkg/.dmg-filer hit. Klienten får **aldri** direkte tilgang – den får en kortlivet SAS-token fra Azure Function.

```
packages/
├── org.mozilla.firefox/
│   └── Firefox-115.0.1.pkg
├── com.google.Chrome/
│   └── GoogleChrome-121.0.pkg
└── com.microsoft.teams/
    └── Teams-24.2.pkg
```

### 1.3 API-endepunkter

#### `POST /api/check-updates`

Klienten sender sine installerte apper. API-et returnerer oppdateringer med **kortlivede SAS-tokens** for direkte nedlasting fra Azure Blob Storage.

**Request:**
```json
{
  "device_id": "hashed-serial",
  "installed_apps": [
    {"bundle_id": "org.mozilla.firefox", "version": "114.0.2"},
    {"bundle_id": "com.google.Chrome", "version": "120.0.6099.129"},
    {"bundle_id": "com.microsoft.Word", "version": "16.80"}
  ]
}
```

**Response:**
```json
{
  "updates_available": [
    {
      "bundle_id": "org.mozilla.firefox",
      "app_name": "Firefox",
      "installed_version": "114.0.2",
      "latest_version": "115.0.1",
      "download_url": "https://storageaccount.blob.core.windows.net/packages/org.mozilla.firefox/Firefox-115.0.1.pkg?sv=2023-11-03&st=2026-02-26T10%3A00%3A00Z&se=2026-02-26T10%3A15%3A00Z&sr=b&sp=r&sig=xxxxx",
      "download_filename": "Firefox-115.0.1.pkg",
      "sas_expires_at": "2026-02-26T10:15:00Z"
    }
  ],
  "up_to_date": [
    {"bundle_id": "com.google.Chrome", "app_name": "Google Chrome"}
  ],
  "not_managed": [
    {"bundle_id": "com.microsoft.Word"}
  ]
}
```

**SAS-token logikk i Azure Function:**

```python
from azure.storage.blob import BlobServiceClient, generate_blob_sas, BlobSasPermissions
from datetime import datetime, timedelta, timezone

def generate_download_sas(blob_path: str) -> str:
    """Generer en kortlivet SAS-token (15 min) for nedlasting av pakke."""
    sas_token = generate_blob_sas(
        account_name=STORAGE_ACCOUNT_NAME,
        container_name="packages",
        blob_name=blob_path,
        account_key=STORAGE_ACCOUNT_KEY,
        permission=BlobSasPermissions(read=True),
        expiry=datetime.now(timezone.utc) + timedelta(minutes=15),
    )
    return f"https://{STORAGE_ACCOUNT_NAME}.blob.core.windows.net/packages/{blob_path}?{sas_token}"
```

**Sikkerhet:**
- SAS-token utløper etter **15 minutter** (kun nok tid til nedlasting)
- Kun **lesertilgang** (read-only)
- Klienten får aldri storage account key
- Hvert API-kall genererer nye tokens (ikke gjenbrukbare)

#### `POST /api/register-version`

Kalles av AutoPkg etter vellykket pakking. Laster opp pakken til Blob Storage og registrerer versjonen.

**Request (fra AutoPkg):**
```json
{
  "bundle_id": "org.mozilla.firefox",
  "app_name": "Firefox",
  "latest_version": "115.0.1",
  "intune_app_id": "abc123",
  "recipe_id": "com.github.autopkg.intune.Firefox",
  "blob_path": "org.mozilla.firefox/Firefox-115.0.1.pkg"
}
```

**Logikk:**
1. Oppdater `ManagedApps`-tabell med ny versjon og blob_path
2. Returner bekreftelse

> **Merk**: Selve .pkg/.dmg-filen lastes opp til Blob Storage av AutoPkg-postprocessoren
> (eller manuelt). `register-version` registrerer bare metadata.

#### `POST /api/request-app` (Fase 5, fremtidig)

**Request:**
```json
{
  "device_id": "hashed-serial",
  "user_email": "bruker@firma.no",
  "bundle_id": "com.slack.Slack",
  "reason": "Trenger for prosjektsamarbeid"
}
```

### 1.4 Filer å opprette

```
azure-function/
├── host.json
├── local.settings.json
├── requirements.txt          # azure-functions, azure-data-tables, azure-storage-blob
├── check_updates/
│   ├── __init__.py           # Hovedlogikk + SAS-token-generering
│   └── function.json         # HTTP trigger config
├── register_version/
│   ├── __init__.py
│   └── function.json
├── log_event/
│   ├── __init__.py           # Logging av update/deferral events
│   └── function.json
└── shared/
    ├── __init__.py
    ├── table_storage.py      # Azure Table Storage helper
    ├── blob_storage.py       # Azure Blob Storage + SAS-token helper
    └── graph_client.py       # Microsoft Graph API helper (fremtidig)
```

---

## Fase 2: AutoPkg VersionReporter Postprocessor

### 2.1 Implementasjon

Ny fil: `Code/autopkglib/VersionReporter.py`

```python
class VersionReporter(Processor):
    """Rapporterer versjonsinfo til Azure Function API etter vellykket pakking."""
    
    input_variables = {
        "VERSION_REPORT_URL": {
            "required": True,
            "description": "URL til Azure Function /api/register-version"
        },
        "VERSION_REPORT_API_KEY": {
            "required": True,
            "description": "API-nøkkel for Azure Function"
        },
        "bundleid": {
            "required": True,
            "description": "Bundle ID for appen"
        },
        "version": {
            "required": True,
            "description": "Siste versjon av appen"
        },
        "NAME": {
            "required": False,
            "description": "Appnavn"
        }
    }
    
    output_variables = {
        "version_reported": {
            "description": "True hvis versjonen ble rapportert til API"
        }
    }
    
    def main(self):
        # POST til Azure Function med versjonsinfo
        ...
```

### 2.2 Bruk

```bash
autopkg run Firefox.intune \
  --post com.github.autopkg.VersionReporter \
  -k VERSION_REPORT_URL="https://myfunc.azurewebsites.net/api/register-version" \
  -k VERSION_REPORT_API_KEY="xxxx"
```

---

## Fase 3: Intune Platform Script (update_agent.sh)

> I stedet for en Python LaunchAgent bruker vi et Intune Platform Script (bash).
> Dette gir root-tilgang, innebygd scheduling, og eliminerer behovet for separat
> Python-runtime på klientene.

### 3.1 Installasjonstidspunkt-strategi

| Scenario | Når installeres oppdateringen? |
|----------|-------------------------------|
| Bruker velger "Oppdater" i dialog | **Utenfor arbeidstid** – scriptet registrerer valget og en LaunchDaemon-jobb kjører installasjonen kl 19:00-06:00 |
| Bruker velger "Utsett" (< 3 ganger) | Neste kjøring av scriptet spør igjen |
| Bruker har utsatt **3+ ganger** | **Umiddelbart** – tvungen installasjon uavhengig av tidspunkt |
| Timer utløper (ingen brukerrespons) | Teller som utsettelse |
| Ingen bruker innlogget | Stille installasjon uten dialog |

**Arbeidstid defineres i config** (default: 08:00-17:00 man-fre).

### 3.2 Filstruktur

Scriptet er **én enkelt bash-fil** som lastes opp direkte i Intune:

```
scripts/
└── update_agent.sh          # Hoved-scriptet (Intune Platform Script)
```

Støttefiler opprettes automatisk av scriptet ved første kjøring:

```
/Library/Application Support/UpdateAgent/
├── deferrals.json            # Utsettelsesteller per app
├── pending_updates.json      # Oppdateringer godkjent av bruker, venter på off-hours
├── config.json               # Konfigurasjon (API URL, nøkkel, arbeidstid, etc.)
├── cache/                    # Nedlastede .pkg/.dmg-filer (med SAS-token nedlasting)
└── logs/                     # Lokale logger
```

### 3.2 Scriptets hovedflyt

```bash
#!/bin/bash
# update_agent.sh – Intune Platform Script
# Kjører som root via Intune med konfigurerbart intervall

set -euo pipefail

# ============================================================
# KONFIGURASJON
# ============================================================
API_BASE_URL="https://yourfunc.azurewebsites.net/api"
API_KEY="din-api-nøkkel-her"               # Kan også hentes fra Keychain
MAX_DEFERRALS=3
AGENT_DIR="/Library/Application Support/UpdateAgent"
DEFERRALS_FILE="${AGENT_DIR}/deferrals.json"
LOG_FILE="${AGENT_DIR}/logs/update_agent.log"
WORK_HOURS_START=8                          # Arbeidstid start (kl 08:00)
WORK_HOURS_END=17                           # Arbeidstid slutt (kl 17:00)

# ============================================================
# HJELPEFUNKSJONER
# ============================================================

log() {
    echo "$(date '+%Y-%m-%d %H:%M:%S') [$1] $2" >> "$LOG_FILE"
}

get_current_user() {
    stat -f "%Su" /dev/console
}

get_current_user_uid() {
    id -u "$(get_current_user)"
}

# Kjør kommando i brukerens kontekst (for GUI-dialoger)
run_as_user() {
    local current_user
    current_user=$(get_current_user)
    local uid
    uid=$(get_current_user_uid)

    if [[ "$current_user" == "root" || "$current_user" == "loginwindow" ]]; then
        log "WARN" "Ingen bruker innlogget, hopper over dialog"
        return 1
    fi

    launchctl asuser "$uid" sudo -u "$current_user" "$@"
}

# ============================================================
# 1. SKANN INSTALLERTE APPER
# ============================================================

scan_applications() {
    # Genererer JSON-array med bundle IDs og versjoner
    local apps_json="["
    local first=true

    for app in /Applications/*.app /Applications/Utilities/*.app; do
        local plist="${app}/Contents/Info.plist"
        [[ -f "$plist" ]] || continue

        local bundle_id version app_name
        bundle_id=$(/usr/libexec/PlistBuddy -c "Print :CFBundleIdentifier" "$plist" 2>/dev/null) || continue
        version=$(/usr/libexec/PlistBuddy -c "Print :CFBundleShortVersionString" "$plist" 2>/dev/null) || version="0"
        app_name=$(/usr/libexec/PlistBuddy -c "Print :CFBundleName" "$plist" 2>/dev/null) || app_name=$(basename "$app" .app)

        [[ -z "$bundle_id" ]] && continue

        if [[ "$first" == true ]]; then
            first=false
        else
            apps_json+=","
        fi

        apps_json+="{\"bundle_id\":\"${bundle_id}\",\"version\":\"${version}\",\"name\":\"${app_name}\"}"
    done

    apps_json+="]"
    echo "$apps_json"
}

# ============================================================
# 2. SJEKK MOT API
# ============================================================

check_updates() {
    local installed_apps="$1"
    local device_serial
    device_serial=$(ioreg -d2 -c IOPlatformExpertDevice | awk -F'"' '/IOPlatformSerialNumber/{print $4}')
    local device_id
    device_id=$(echo -n "$device_serial" | shasum -a 256 | awk '{print $1}')

    local payload="{\"device_id\":\"${device_id}\",\"installed_apps\":${installed_apps}}"

    curl -s -X POST "${API_BASE_URL}/check-updates" \
        -H "Content-Type: application/json" \
        -H "x-functions-key: ${API_KEY}" \
        -d "$payload"
}

# ============================================================
# 3. UTSETTELSESLOGIKK
# ============================================================

get_deferral_count() {
    local bundle_id="$1"
    local version="$2"

    if [[ ! -f "$DEFERRALS_FILE" ]]; then
        echo "0"
        return
    fi

    # Sjekk om versjonen matcher (reset teller hvis ny versjon)
    local stored_version count
    stored_version=$(python3 -c "
import json, sys
with open('${DEFERRALS_FILE}') as f:
    d = json.load(f)
entry = d.get('${bundle_id}', {})
print(entry.get('version_available', ''))
" 2>/dev/null) || stored_version=""

    if [[ "$stored_version" != "$version" ]]; then
        echo "0"
        return
    fi

    count=$(python3 -c "
import json
with open('${DEFERRALS_FILE}') as f:
    d = json.load(f)
print(d.get('${bundle_id}', {}).get('count', 0))
" 2>/dev/null) || count="0"

    echo "$count"
}

increment_deferral() {
    local bundle_id="$1"
    local version="$2"

    python3 -c "
import json, os
from datetime import datetime, timezone

path = '${DEFERRALS_FILE}'
data = {}
if os.path.exists(path):
    with open(path) as f:
        data = json.load(f)

entry = data.get('${bundle_id}', {'count': 0})

# Reset hvis ny versjon
if entry.get('version_available') != '${version}':
    entry = {'count': 0, 'first_seen': datetime.now(timezone.utc).isoformat()}

entry['count'] = entry.get('count', 0) + 1
entry['version_available'] = '${version}'
entry['last_deferred'] = datetime.now(timezone.utc).isoformat()
data['${bundle_id}'] = entry

with open(path, 'w') as f:
    json.dump(data, f, indent=2)
"
}

clear_deferral() {
    local bundle_id="$1"

    python3 -c "
import json, os
path = '${DEFERRALS_FILE}'
if os.path.exists(path):
    with open(path) as f:
        data = json.load(f)
    data.pop('${bundle_id}', None)
    with open(path, 'w') as f:
        json.dump(data, f, indent=2)
"
}

# ============================================================
# 4. OSASCRIPT-DIALOGER (innebygd i macOS)
# ============================================================

show_update_dialog() {
    local updates_json="$1"
    local remaining_deferrals="$2"

    # Bygg appliste-tekst fra JSON
    local app_list
    app_list=$(echo "$updates_json" | python3 -c "
import json, sys
updates = json.load(sys.stdin)
lines = []
for u in updates:
    name = u.get('app_name', u['bundle_id'])
    old_v = u.get('installed_version', '?')
    new_v = u.get('latest_version', '?')
    lines.append(f'• {name}  ({old_v} → {new_v})')
print('\n'.join(lines))
")

    local defer_text="Utsett (${remaining_deferrals} igjen)"

    # Kjør osascript i brukerens kontekst
    local result
    result=$(run_as_user osascript -e "
        display dialog \"Følgende oppdateringer er tilgjengelige:\n\n${app_list}\n\nOppdateringene installeres utenfor arbeidstid.\" ¬
            with title \"Programvareoppdateringer\" ¬
            buttons {\"${defer_text}\", \"Oppdater i kveld\"} ¬
            default button \"Oppdater i kveld\" ¬
            giving up after 300
    " 2>&1) || true

    if echo "$result" | grep -q "Oppdater i kveld"; then
        return 0   # Bruker valgte oppdater
    elif echo "$result" | grep -q "gave up:true"; then
        return 4   # Timer utløpt
    else
        return 2   # Bruker valgte utsett
    fi
}

show_forced_dialog() {
    run_as_user osascript -e "
        display alert \"Obligatorisk oppdatering\" ¬
            message \"Du har utsatt disse oppdateringene maksimalt antall ganger.\n\nOppdateringene installeres nå.\" ¬
            as critical ¬
            buttons {\"OK\"} ¬
            default button \"OK\" ¬
            giving up after 60
    " 2>/dev/null || true
}

show_progress_notification() {
    run_as_user osascript -e "
        display notification \"Oppdateringene lastes ned og installeres. Du kan fortsette å jobbe.\" ¬
            with title \"Installerer oppdateringer\"
    " 2>/dev/null || true
}

is_within_work_hours() {
    local hour
    hour=$(date +%H)
    local dow
    dow=$(date +%u)  # 1=mandag, 7=søndag

    # Helg = utenfor arbeidstid
    if [[ "$dow" -ge 6 ]]; then
        return 1
    fi

    # Sjekk klokkeslett
    if [[ "$hour" -ge "$WORK_HOURS_START" && "$hour" -lt "$WORK_HOURS_END" ]]; then
        return 0  # I arbeidstid
    fi
    return 1  # Utenfor arbeidstid
}

# ============================================================
# 5. INSTALLER OPPDATERINGER (root-tilgang!)
# ============================================================

install_update() {
    local bundle_id="$1"
    local intune_app_id="$2"
    local download_url="$3"

    log "INFO" "Starter installasjon av ${bundle_id}"

    # Metode 1: Direkte installasjon via nedlastet pkg/dmg
    if [[ -n "$download_url" ]]; then
        local temp_file
        temp_file="${AGENT_DIR}/cache/$(basename "$download_url")"
        mkdir -p "${AGENT_DIR}/cache"

        curl -sL -o "$temp_file" "$download_url"

        if [[ "$temp_file" == *.pkg ]]; then
            installer -pkg "$temp_file" -target /
            log "INFO" "Installerte ${bundle_id} via pkg"
        elif [[ "$temp_file" == *.dmg ]]; then
            local mount_point
            mount_point=$(hdiutil attach -nobrowse -noautoopen "$temp_file" | tail -1 | awk '{print $NF}')
            local app_path
            app_path=$(find "$mount_point" -maxdepth 1 -name "*.app" | head -1)
            if [[ -n "$app_path" ]]; then
                cp -R "$app_path" /Applications/
                log "INFO" "Installerte ${bundle_id} via dmg"
            fi
            hdiutil detach "$mount_point" -quiet
        fi

        rm -f "$temp_file"
    fi

    # Metode 2: Trigger Intune sync som fallback
    # Company Portal refresh
    if command -v open &>/dev/null; then
        open "companyportal://refresh" 2>/dev/null || true
    fi

    clear_deferral "$bundle_id"
    log "INFO" "Installasjon fullført for ${bundle_id}"
}

# ============================================================
# 6. LOGG TIL API
# ============================================================

report_event() {
    local device_serial
    device_serial=$(ioreg -d2 -c IOPlatformExpertDevice | awk -F'"' '/IOPlatformSerialNumber/{print $4}')
    local device_id
    device_id=$(echo -n "$device_serial" | shasum -a 256 | awk '{print $1}')

    local bundle_id="$1"
    local action="$2"
    local from_version="$3"
    local to_version="$4"

    curl -s -X POST "${API_BASE_URL}/log-event" \
        -H "Content-Type: application/json" \
        -H "x-functions-key: ${API_KEY}" \
        -d "{
            \"device_id\": \"${device_id}\",
            \"bundle_id\": \"${bundle_id}\",
            \"action\": \"${action}\",
            \"from_version\": \"${from_version}\",
            \"to_version\": \"${to_version}\"
        }" 2>/dev/null || true
}

# ============================================================
# HOVEDLOGIKK
# ============================================================

main() {
    mkdir -p "${AGENT_DIR}/logs" "${AGENT_DIR}/cache"
    log "INFO" "Update Agent startet"

    # ── STEG 0: Installer ventende oppdateringer hvis utenfor arbeidstid ──
    if ! is_within_work_hours && [[ -f "${AGENT_DIR}/pending_updates.json" ]]; then
        log "INFO" "Utenfor arbeidstid – installerer ventende oppdateringer"

        # Sjekk om bruker er innlogget for notifikasjon
        local current_user
        current_user=$(get_current_user)
        if [[ "$current_user" != "root" && "$current_user" != "loginwindow" ]]; then
            show_installing_dialog
        fi

        python3 -c "
import json, sys
with open('${AGENT_DIR}/pending_updates.json') as f:
    updates = json.load(f)
for u in updates:
    print(u['bundle_id'], u.get('intune_app_id',''), u.get('download_url',''), u.get('installed_version',''), u.get('latest_version',''))
" | while read -r bid app_id dl_url old_ver new_ver; do
            install_update "$bid" "$app_id" "$dl_url"
            report_event "$bid" "updated" "$old_ver" "$new_ver"
        done

        rm -f "${AGENT_DIR}/pending_updates.json"
        log "INFO" "Ventende oppdateringer installert"
    fi

    # ── STEG 1: Sjekk at en bruker er innlogget (for dialog) ──
    local current_user
    current_user=$(get_current_user)
    if [[ "$current_user" == "root" || "$current_user" == "loginwindow" ]]; then
        log "INFO" "Ingen bruker innlogget – kun stille oppdateringer mulig"
        # Kunne installert tvungne oppdateringer her uten dialog
        exit 0
    fi

    # ── STEG 2: Skann installerte apper ──
    log "INFO" "Scanner installerte apper..."
    local installed_apps
    installed_apps=$(scan_applications)

    # 2. Sjekk mot API
    log "INFO" "Sjekker oppdateringer mot API..."
    local api_response
    api_response=$(check_updates "$installed_apps")

    # 3. Parse svar – del i promptable og forced
    local updates_available
    updates_available=$(echo "$api_response" | python3 -c "
import json, sys
resp = json.load(sys.stdin)
print(json.dumps(resp.get('updates_available', [])))
")

    local update_count
    update_count=$(echo "$updates_available" | python3 -c "import json,sys; print(len(json.load(sys.stdin)))")

    if [[ "$update_count" -eq 0 ]]; then
        log "INFO" "Ingen oppdateringer tilgjengelig"
        exit 0
    fi

    log "INFO" "${update_count} oppdateringer tilgjengelig"

    # 4. Kategoriser: promptable vs forced
    local promptable_json="[]"
    local forced_json="[]"
    local min_remaining=$MAX_DEFERRALS

    read -r promptable_json forced_json min_remaining < <(echo "$updates_available" | python3 -c "
import json, sys, subprocess

updates = json.load(sys.stdin)
promptable = []
forced = []
min_remaining = ${MAX_DEFERRALS}

for u in updates:
    bid = u['bundle_id']
    ver = u['latest_version']
    # Les utsettelsesteller via shell (allerede tilgjengelig fra scriptet)
    # Forenklet: les direkte fra filen
    import os
    deferrals_file = '${DEFERRALS_FILE}'
    count = 0
    if os.path.exists(deferrals_file):
        with open(deferrals_file) as f:
            data = json.load(f)
        entry = data.get(bid, {})
        if entry.get('version_available') == ver:
            count = entry.get('count', 0)

    if count >= ${MAX_DEFERRALS}:
        forced.append(u)
    else:
        remaining = ${MAX_DEFERRALS} - count
        u['remaining_deferrals'] = remaining
        if remaining < min_remaining:
            min_remaining = remaining
        promptable.append(u)

print(json.dumps(promptable), json.dumps(forced), min_remaining)
")

    # ── STEG 5: Håndter tvungne oppdateringer (3+ utsettelser → umiddelbart) ──
    local forced_count
    forced_count=$(echo "$forced_json" | python3 -c "import json,sys; print(len(json.load(sys.stdin)))")

    if [[ "$forced_count" -gt 0 ]]; then
        log "INFO" "Tvinger UMIDDELBAR oppdatering av ${forced_count} apper (3+ utsettelser)"

        # Bygg lesbar appliste for dialogen
        local forced_app_list
        forced_app_list=$(echo "$forced_json" | python3 -c "
import json, sys
for u in json.load(sys.stdin):
    name = u.get('app_name', u['bundle_id'])
    print(f'• {name}  ({u.get(\"installed_version\",\"?\")} → {u.get(\"latest_version\",\"?\")})')
")
        show_forced_dialog "$forced_app_list"

        echo "$forced_json" | python3 -c "
import json, sys
for u in json.load(sys.stdin):
    print(u['bundle_id'], u.get('intune_app_id',''), u.get('download_url',''), u.get('installed_version',''), u.get('latest_version',''))
" | while read -r bid app_id dl_url old_ver new_ver; do
            install_update "$bid" "$app_id" "$dl_url"
            report_event "$bid" "forced" "$old_ver" "$new_ver"
        done
    fi

    # ── STEG 6: Vis prompt for vanlige oppdateringer ──
    local promptable_count
    promptable_count=$(echo "$promptable_json" | python3 -c "import json,sys; print(len(json.load(sys.stdin)))")

    if [[ "$promptable_count" -gt 0 ]]; then
        # Bygg lesbar appliste for osascript-dialogen
        local promptable_app_list
        promptable_app_list=$(echo "$promptable_json" | python3 -c "
import json, sys
for u in json.load(sys.stdin):
    name = u.get('app_name', u['bundle_id'])
    print(f'• {name}  ({u.get(\"installed_version\",\"?\")} → {u.get(\"latest_version\",\"?\")})')
")
        show_update_dialog "$promptable_app_list" "$min_remaining"
        local dialog_exit=$?

        case $dialog_exit in
            0)  # Bruker valgte "Installer i kveld"
                log "INFO" "Bruker godkjente oppdatering – planlegger for utenfor arbeidstid"

                if is_within_work_hours; then
                    # Lagre til pending_updates.json – installeres utenfor arbeidstid
                    echo "$promptable_json" > "${AGENT_DIR}/pending_updates.json"
                    show_scheduled_dialog "Oppdateringene installeres i kveld"
                    echo "$promptable_json" | python3 -c "
import json, sys
for u in json.load(sys.stdin):
    print(u['bundle_id'], u.get('installed_version',''), u.get('latest_version',''))
" | while read -r bid old_ver new_ver; do
                        report_event "$bid" "scheduled" "$old_ver" "$new_ver"
                    done
                else
                    # Allerede utenfor arbeidstid – installer nå
                    show_installing_dialog
                    echo "$promptable_json" | python3 -c "
import json, sys
for u in json.load(sys.stdin):
    print(u['bundle_id'], u.get('intune_app_id',''), u.get('download_url',''), u.get('installed_version',''), u.get('latest_version',''))
" | while read -r bid app_id dl_url old_ver new_ver; do
                        install_update "$bid" "$app_id" "$dl_url"
                        report_event "$bid" "updated" "$old_ver" "$new_ver"
                    done
                fi
                ;;
            2)  # Bruker valgte "Utsett"
                log "INFO" "Bruker utsatte oppdateringer"
                echo "$promptable_json" | python3 -c "
import json, sys
for u in json.load(sys.stdin):
    print(u['bundle_id'], u.get('latest_version',''), u.get('installed_version',''))
" | while read -r bid new_ver old_ver; do
                    increment_deferral "$bid" "$new_ver"
                    report_event "$bid" "deferred" "$old_ver" "$new_ver"
                done
                ;;
            4)  # Timer utløpt (behandles som utsettelse)
                log "INFO" "Dialog-timer utløpt, behandler som utsettelse"
                echo "$promptable_json" | python3 -c "
import json, sys
for u in json.load(sys.stdin):
    print(u['bundle_id'], u.get('latest_version',''), u.get('installed_version',''))
" | while read -r bid new_ver old_ver; do
                    increment_deferral "$bid" "$new_ver"
                    report_event "$bid" "deferred" "$old_ver" "$new_ver"
                done
                ;;
        esac
    fi

    log "INFO" "Update Agent ferdig"
}

main "$@"
```

### 3.3 Intune Platform Script-innstillinger

I Intune-portalen konfigureres scriptet slik:

| Innstilling | Verdi |
|-------------|-------|
| **Script name** | Update Agent |
| **Script** | Last opp `update_agent.sh` |
| **Run script as signed-in user** | **Nei** (kjører som root) |
| **Hide script notifications** | Ja |
| **Script frequency** | Hver 8. time (konfigurerbart) |
| **Max retries** | 3 |
| **Assigned groups** | Alle macOS-enheter |

### 3.4 osascript fra root-kontekst

Nøkkelteknikken for å vise dialog til bruker fra et root-script:

```bash
# Finn innlogget bruker
current_user=$(stat -f "%Su" /dev/console)
uid=$(id -u "$current_user")

# Vis dialog i brukerens kontekst
launchctl asuser "$uid" sudo -u "$current_user" osascript -e '
    display dialog "Oppdateringer tilgjengelig" ¬
        buttons {"Utsett", "Installer i kveld"} ¬
        default button "Installer i kveld" ¬
        with title "Programvareoppdateringer" ¬
        giving up after 300
'

# Vis notifikasjon (diskret)
launchctl asuser "$uid" sudo -u "$current_user" osascript -e '
    display notification "Installeres i kveld" ¬
        with title "Oppdateringer planlagt"
'
```

**Fordeler med osascript:**
- Innebygd i macOS – ingen ekstra avhengigheter
- `display dialog` for valg med knapper
- `display alert` for viktige meldinger (tvungen oppdatering)
- `display notification` for diskrete bekreftelser
- Fungerer fra root via `launchctl asuser`

### 3.5 Direkte installasjon (root-privilegium)

Siden scriptet kjører som root, kan det installere direkte:

```bash
# .pkg-filer
installer -pkg "/path/to/app.pkg" -target /

# .dmg-filer
mount_point=$(hdiutil attach -nobrowse app.dmg | tail -1 | awk '{print $NF}')
cp -R "${mount_point}/"*.app /Applications/
hdiutil detach "$mount_point" -quiet
```

Ingen MDM-roundtrip nødvendig! Mye raskere enn å vente på Intune sync.

#### Vent-på-lukking-logikk

Før installasjon sjekker scriptet om appen kjører (`app_is_running` via `System Events`):

```
┌─ Er appen åpen? ──────────────────────────────────────────────┐
│                                                                │
│  NEI → Installer umiddelbart                                   │
│                                                                │
│  JA  → Vis dialog: "Firefox må lukkes for å oppdatere"         │
│        Knapper: [Lukk Firefox] / [Vent]                        │
│                                                                │
│        "Lukk" → Sender `quit` til appen, venter på avslutning  │
│        "Vent" → Sjekker hvert 5. sekund i opp til 10 minutter  │
│                                                                │
│        Appen lukket → Installer                                │
│        Timeout (10 min) → Hopp over, prøv igjen neste kjøring  │
│        Appen gjenåpnet etter nedlasting → Avbryt installasjon  │
└────────────────────────────────────────────────────────────────┘
```

### 3.6 Utsettelseslogikk

**Datamodell** (`/Library/Application Support/UpdateAgent/deferrals.json`):

```json
{
  "org.mozilla.firefox": {
    "count": 2,
    "version_available": "115.0.1",
    "first_seen": "2026-02-20T09:00:00Z",
    "last_deferred": "2026-02-25T14:00:00Z"
  }
}
```

**Regler:**
- Teller nullstilles når en ny versjon dukker opp fra API
- Etter 3 utsettelser → tvungen oppdatering **umiddelbart** (ingen "Utsett"-knapp, uansett tidspunkt)
- Timer-utløp (5 min) teller som utsettelse
- Vanlige oppdateringer (< 3 utsettelser) installeres **utenfor arbeidstid**
- Lagres i `/Library/Application Support/` (system-nivå, beskyttet mot bruker)

---

## Fase 4: Intune-oppsett og testing

### 4.1 Forutsetninger i Intune

| Komponent | Type i Intune | Merknad |
|-----------|--------------|---------|
| **update_agent.sh** | Platform Script (shell) | Hovedscriptet – ingen andre avhengigheter |
| **setup_update_agent.sh** | Platform Script (shell) | Engangskjøring – oppretter config |

> **Ingen ekstra apper å distribuere** – `osascript` er innebygd i macOS.

### 4.2 Konfigurasjonsskript (kjøres én gang)

Eget platform script for førstegangsoppsett:

```bash
#!/bin/bash
# setup_update_agent.sh – Kjøres én gang for å sette opp konfigurasjon

AGENT_DIR="/Library/Application Support/UpdateAgent"
mkdir -p "${AGENT_DIR}/logs" "${AGENT_DIR}/cache"

cat > "${AGENT_DIR}/config.json" << 'EOF'
{
    "api_base_url": "https://yourfunc.azurewebsites.net/api",
    "api_key": "din-api-nøkkel",
    "max_deferrals": 3,
    "dialog_timeout_seconds": 300,
    "work_hours_start": 8,
    "work_hours_end": 17,
    "scan_paths": ["/Applications", "/Applications/Utilities"]
}
EOF

chmod 600 "${AGENT_DIR}/config.json"
echo "Update Agent konfigurert"
```

### 4.3 Testplan

| Steg | Test | Forventet resultat |
|------|------|-------------------|
| 1 | Kjør setup-script | Config-fil opprettet under `/Library/Application Support/UpdateAgent/` |
| 2 | Kjør `update_agent.sh` manuelt | Skanner apper, kontakter API, viser osascript-dialog |
| 3 | Mock API med oppdateringer | `display dialog` vises med appliste og knapper |
| 4 | Klikk "Installer i kveld" (i arbeidstid) | Lagres i `pending_updates.json`, notifikasjon vises |
| 5 | Kjør scriptet igjen utenfor arbeidstid | Ventende oppdateringer installeres automatisk |
| 6 | Klikk "Utsett" 3 ganger | 4. kjøring viser `display alert` (tvungen, umiddelbar installasjon) |
| 7 | La timer løpe ut (5 min) | Teller som utsettelse |
| 8 | Ny versjon fra API | Utsettelsesteller nullstilles |

---

## Fase 5: App-request (fremtidig utvidelse)

### 5.1 Konsept

Brukere kan be om at nye apper gjøres tilgjengelig:

1. Klientagenten viser en "Be om app"-knapp
2. Bruker søker/velger ønsket app
3. Forespørsel sendes til Azure Function
4. Admin varsles (e-post/Teams-melding via Logic App)
5. Admin godkjenner → AutoPkg-resept opprettes → App pakkes → Tilgjengelig i Intune

### 5.2 API

```
POST /api/request-app
{
  "user_email": "bruker@firma.no",
  "app_name": "Slack",
  "bundle_id": "com.slack.Slack",  // valgfritt
  "reason": "Prosjektsamarbeid",
  "device_id": "hashed-serial"
}
```

---

## Tekniske valg og begrunnelse

| Valg | Alternativ | Begrunnelse |
|------|-----------|-------------|
| **Intune Platform Script (bash)** | Python LaunchAgent / Swift app | Kjører som root (kan installere direkte), innebygd scheduling, én fil å vedlikeholde, ingen runtime-avhengighet å distribuere. |
| **osascript for UI** | swiftDialog / PyObjC | Innebygd i macOS – null ekstra avhengigheter å distribuere. `display dialog` for knapper, `display alert` for tvungne meldinger, `display notification` for diskrete bekreftelser. Fungerer fra root via `launchctl asuser`. |
| **Azure Function** | Direkte Graph API | Sentralisert logikk, caching, enklere klientautentisering, utvidbar. |
| **Azure Table Storage** | CosmosDB / SQL | Billigst, enklest for key-value data. Nok for dette brukstilfellet. |
| **Direkte installasjon (root)** | MDM sync + assignment | Mye raskere enn Intune roundtrip. Root-tilgang fra platform script gjør dette mulig. Intune sync som fallback. |
| **Maks 3 utsettelser** | Konfigurerbar | Default i config.json, kan endres per gruppe via separate konfigurasjonsscript. |
| **System-level lagring** | Per-bruker lagring | `/Library/Application Support/UpdateAgent/` er beskyttet mot brukerendringer. Root-tilgang sikrer integritet. |

---

## Neste steg

Etter godkjenning av denne planen:

1. **Fase 1** – Opprette Azure Function App med kjerneendepunktene
2. **Fase 2** (parallelt) – Lage AutoPkg VersionReporter postprocessor
3. **Fase 3** – Platform Script med osascript-dialoger, off-hours installasjon og utsettelseslogikk
4. **Fase 4** – Intune-oppsett og end-to-end testing

### Åpne spørsmål

- [ ] Hva skal firmaprefikset være? (f.eks. `com.firma.updateagent`)
- [ ] Har dere en eksisterende Azure Function App, eller skal vi opprette ny?
- [ ] Hvilke apper er viktigst å starte med? (Firefox, Chrome, Teams, etc.)
- [ ] Skal tvungne oppdateringer ha en grace period (f.eks. 30 min nedtelling) eller kjøres umiddelbart?
- [ ] Ønsker dere logging/rapportering av oppdateringsstatus per enhet til f.eks. Log Analytics?
- [ ] Skal agenten fungere offline (cached versjonsliste fra siste API-kall)?
- [ ] Skal det være en grace period på tvungne oppdateringer (f.eks. 5 min nedtelling) eller installere rett etter OK-klikk?
