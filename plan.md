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
│  │  POST /api/trigger-update    ← Klient ber om oppdatering       │    │
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
│  │  Update Agent (Python LaunchAgent)       │                           │
│  │                                          │                           │
│  │  1. Skann /Applications → bundle IDs     │                           │
│  │  2. POST /api/check-updates              │                           │
│  │  3. Motta oppdateringsliste              │                           │
│  │  4. Vis swiftDialog-prompt               │                           │
│  │  5. Bruker: "Oppdater" / "Utsett"       │                           │
│  │  6. POST /api/trigger-update             │                           │
│  │  7. Trigger Company Portal sync          │                           │
│  │                                          │                           │
│  │  ┌────────────────┐  ┌────────────────┐  │                           │
│  │  │ deferrals.json │  │ config.plist   │  │                           │
│  │  │ (utsettelser)  │  │ (innstillinger)│  │                           │
│  │  └────────────────┘  └────────────────┘  │                           │
│  └──────────────────────────────────────────┘                           │
│                                                                         │
│  ┌──────────────────────────────────────────┐                           │
│  │  swiftDialog (UI)                        │                           │
│  │  - Liste over tilgjengelige oppdateringer│                           │
│  │  - App-ikoner + versjonsnummer           │                           │
│  │  - "Oppdater nå" / "Utsett (X igjen)"   │                           │
│  └──────────────────────────────────────────┘                           │
└──────────────────────────────────────────────────────────────────────────┘
```

---

## Faseoversikt

| Fase | Beskrivelse | Estimat | Avhengigheter |
|------|-------------|---------|---------------|
| **1** | Azure Function App (API) | 2-3 dager | Azure-tilgang |
| **2** | AutoPkg VersionReporter postprocessor | 1 dag | Fase 1 |
| **3** | macOS klientagent (kjernefunksjonalitet) | 3-4 dager | Fase 1 |
| **4** | swiftDialog UI-integrasjon | 1-2 dager | Fase 3 |
| **5** | Utsettelseslogikk + tvungen oppdatering | 1 dag | Fase 3-4 |
| **6** | Intune-distribusjon og testing | 1-2 dager | Fase 1-5 |
| **7** | App-request-funksjonalitet (valgfri) | 2-3 dager | Fase 1 |

---

## Fase 1: Azure Function App

### 1.1 Oppsett

- **Runtime**: Python 3.10 (matcher AutoPkg)
- **Hosting plan**: Consumption (serverless)
- **Storage**: Azure Table Storage
- **Autentisering**: 
  - Klient → Function: API-nøkkel (Function Key)
  - Function → Graph API: Managed Identity + App Registration

### 1.2 Tabellstruktur

**Tabell: `ManagedApps`**

| Felt | Type | Beskrivelse |
|------|------|-------------|
| `PartitionKey` | string | `"apps"` (fast verdi) |
| `RowKey` | string | Bundle ID (f.eks. `org.mozilla.firefox`) |
| `app_name` | string | Visningsnavn |
| `latest_version` | string | Siste tilgjengelige versjon |
| `intune_app_id` | string | App-ID i Intune |
| `min_os_version` | string | Minimum macOS-versjon |
| `updated_at` | datetime | Sist oppdatert |
| `updated_by` | string | Hvem/hva som oppdaterte (AutoPkg resept-ID) |

**Tabell: `UpdateEvents`** (logging/audit)

| Felt | Type | Beskrivelse |
|------|------|-------------|
| `PartitionKey` | string | Enhetens serienummer (hashet) |
| `RowKey` | string | Timestamp + bundle ID |
| `action` | string | `"updated"`, `"deferred"`, `"forced"` |
| `from_version` | string | Gammel versjon |
| `to_version` | string | Ny versjon |

### 1.3 API-endepunkter

#### `POST /api/check-updates`

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
      "intune_app_id": "abc123"
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

#### `POST /api/register-version`

**Request (fra AutoPkg):**
```json
{
  "bundle_id": "org.mozilla.firefox",
  "app_name": "Firefox",
  "latest_version": "115.0.1",
  "intune_app_id": "abc123",
  "recipe_id": "com.github.autopkg.intune.Firefox"
}
```

#### `POST /api/trigger-update`

**Request:**
```json
{
  "device_id": "hashed-serial",
  "bundle_ids": ["org.mozilla.firefox"],
  "action": "update"
}
```

**Logikk:**
1. Endre app-assignment til "Required" for denne enheten (via Graph API)
2. Trigger MDM-sync for enheten
3. Etter 24t: tilbakestill til "Available"

#### `POST /api/request-app` (Fase 7, fremtidig)

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
├── requirements.txt
├── check_updates/
│   ├── __init__.py          # Hovedlogikk
│   └── function.json        # HTTP trigger config
├── register_version/
│   ├── __init__.py
│   └── function.json
├── trigger_update/
│   ├── __init__.py
│   └── function.json
└── shared/
    ├── __init__.py
    ├── table_storage.py     # Azure Table Storage helper
    └── graph_client.py      # Microsoft Graph API helper
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

## Fase 3: macOS Klientagent

### 3.1 Arkitektur

```
update-agent/
├── update_agent/
│   ├── __init__.py
│   ├── main.py              # Hovedskript (entry point)
│   ├── scanner.py            # Skann installerte apper
│   ├── api_client.py         # Kommunikasjon med Azure Function
│   ├── deferral_manager.py   # Håndtere utsettelser
│   ├── notifier.py           # swiftDialog-integrasjon
│   └── config.py             # Konfigurasjon
├── config/
│   ├── com.company.updateagent.plist    # LaunchAgent plist
│   └── config.plist                      # App-konfigurasjon
├── requirements.txt
└── build.sh                  # Bygge-/pakkeskript for Intune
```

### 3.2 Hovedflyt

```python
def main():
    # 1. Les konfigurasjon
    config = load_config()
    
    # 2. Skann installerte apper
    installed_apps = scan_applications()
    # → [{"bundle_id": "org.mozilla.firefox", "version": "114.0.2"}, ...]
    
    # 3. Sjekk mot API
    updates = api_client.check_updates(installed_apps)
    
    # 4. Filtrer basert på utsettelser
    actionable = deferral_manager.filter_updates(updates)
    
    # 5. Vis prompt hvis det finnes oppdateringer
    if actionable["promptable"]:
        user_choice = notifier.show_update_dialog(actionable)
        
        if user_choice == "update":
            api_client.trigger_update(actionable["selected"])
            trigger_company_portal_sync()
        elif user_choice == "defer":
            deferral_manager.record_deferral(actionable["deferred"])
    
    # 6. Tving oppdatering for apper med 3+ utsettelser
    if actionable["forced"]:
        api_client.trigger_update(actionable["forced"])
        notifier.show_forced_update_notice(actionable["forced"])
        trigger_company_portal_sync()
```

### 3.3 App-skanning

```python
def scan_applications(paths=None):
    """Skann installerte apper og returner bundle IDs + versjoner."""
    if paths is None:
        paths = ["/Applications", "/Applications/Utilities"]
    
    apps = []
    for base_path in paths:
        for app_dir in glob.glob(os.path.join(base_path, "*.app")):
            plist_path = os.path.join(app_dir, "Contents", "Info.plist")
            if os.path.exists(plist_path):
                with open(plist_path, "rb") as f:
                    info = plistlib.load(f)
                apps.append({
                    "bundle_id": info.get("CFBundleIdentifier", ""),
                    "version": info.get("CFBundleShortVersionString", ""),
                    "name": info.get("CFBundleName", os.path.basename(app_dir)),
                    "path": app_dir
                })
    return apps
```

### 3.4 LaunchAgent

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" 
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.company.updateagent</string>
    <key>ProgramArguments</key>
    <array>
        <string>/usr/local/bin/python3</string>
        <string>/Library/Application Support/UpdateAgent/main.py</string>
    </array>
    <key>StartCalendarInterval</key>
    <array>
        <!-- Kjør kl 09:00 og 14:00 på hverdager -->
        <dict>
            <key>Hour</key><integer>9</integer>
            <key>Minute</key><integer>0</integer>
        </dict>
        <dict>
            <key>Hour</key><integer>14</integer>
            <key>Minute</key><integer>0</integer>
        </dict>
    </array>
    <key>StandardOutPath</key>
    <string>/var/log/updateagent/agent.log</string>
    <key>StandardErrorPath</key>
    <string>/var/log/updateagent/agent_error.log</string>
</dict>
</plist>
```

---

## Fase 4: swiftDialog UI

### 4.1 Oppdateringsdialog

```bash
/usr/local/bin/dialog \
  --title "Programvareoppdateringer tilgjengelig" \
  --titlefont "size=20" \
  --message "Følgende apper har nye versjoner tilgjengelig.\n\nVelg appene du vil oppdatere:" \
  --icon "/System/Library/CoreServices/Software Update.app/Contents/Resources/SoftwareUpdate.icns" \
  --button1text "Oppdater valgte" \
  --button2text "Utsett (3 gjenværende)" \
  --infobuttontext "Mer info" \
  --listitem "Firefox|Installert: 114.0.2 → Ny: 115.0.1|icon=/Applications/Firefox.app/Contents/Resources/firefox.icns|statustext=Oppdatering klar" \
  --listitem "Google Chrome|Installert: 120.0 → Ny: 121.0|icon=/Applications/Google Chrome.app/Contents/Resources/app.icns|statustext=Oppdatering klar" \
  --timer 300 \
  --height 500 \
  --width 700 \
  --json
```

### 4.2 Tvungen oppdateringsdialog (etter 3 utsettelser)

```bash
/usr/local/bin/dialog \
  --title "Obligatorisk oppdatering" \
  --message "Du har utsatt disse oppdateringene maksimalt antall ganger.\n\nOppdateringene vil nå installeres." \
  --icon "caution" \
  --button1text "OK" \
  --button2disabled \
  --timer 60
```

### 4.3 Bekreftelsesdialog

```bash
/usr/local/bin/dialog \
  --title "Oppdateringer startet" \
  --message "Oppdateringene installeres via Intune.\n\nDu kan fortsette å jobbe." \
  --icon "/System/Library/CoreServices/CoreTypes.bundle/Contents/Resources/ToolbarInfo.icns" \
  --button1text "OK" \
  --timer 10
```

---

## Fase 5: Utsettelseslogikk

### 5.1 Datamodell

```json
// ~/Library/Application Support/UpdateAgent/deferrals.json
{
  "org.mozilla.firefox": {
    "count": 2,
    "max_deferrals": 3,
    "first_seen": "2026-02-20T09:00:00Z",
    "last_deferred": "2026-02-25T14:00:00Z",
    "version_available": "115.0.1"
  },
  "com.google.Chrome": {
    "count": 0,
    "max_deferrals": 3,
    "first_seen": "2026-02-26T09:00:00Z",
    "last_deferred": null,
    "version_available": "121.0"
  }
}
```

### 5.2 Logikk

```python
def filter_updates(self, updates):
    """Kategoriser oppdateringer basert på utsettelsestatus."""
    result = {"promptable": [], "forced": [], "deferred_counts": {}}
    
    for update in updates:
        bid = update["bundle_id"]
        deferral = self.deferrals.get(bid, {"count": 0})
        
        # Reset teller hvis versjon endret seg
        if deferral.get("version_available") != update["latest_version"]:
            deferral = {"count": 0, "version_available": update["latest_version"]}
        
        if deferral["count"] >= self.MAX_DEFERRALS:  # 3
            result["forced"].append(update)
        else:
            update["remaining_deferrals"] = self.MAX_DEFERRALS - deferral["count"]
            result["promptable"].append(update)
    
    return result
```

---

## Fase 6: Distribusjon via Intune

### 6.1 Komponenter å distribuere

| Komponent | Type | Metode |
|-----------|------|--------|
| Python 3.10 runtime | Framework | Shell-script + pkg |
| Update Agent scripts | Shell script | Intune shell script |
| swiftDialog | App | Intune DMG/PKG |
| LaunchAgent plist | Config | Intune configuration profile |
| API-nøkkel | Credential | macOS Keychain via script |

### 6.2 Pakkering

```bash
# Bygg intune-pakke
#!/bin/bash
# build.sh

AGENT_DIR="/Library/Application Support/UpdateAgent"
LAUNCH_AGENT="com.company.updateagent.plist"

# Kopier filer
mkdir -p "$AGENT_DIR"
cp -r update_agent/* "$AGENT_DIR/"
cp config/config.plist "$AGENT_DIR/"

# Installer LaunchAgent
cp "config/$LAUNCH_AGENT" /Library/LaunchAgents/
chmod 644 "/Library/LaunchAgents/$LAUNCH_AGENT"

# Last inn LaunchAgent for alle innloggede brukere
logged_in_user=$(stat -f "%Su" /dev/console)
launchctl bootstrap "gui/$(id -u $logged_in_user)" "/Library/LaunchAgents/$LAUNCH_AGENT"
```

---

## Fase 7: App-request (fremtidig utvidelse)

### 7.1 Konsept

Brukere kan be om at nye apper gjøres tilgjengelig:

1. Klientagenten viser en "Be om app"-knapp
2. Bruker søker/velger ønsket app
3. Forespørsel sendes til Azure Function
4. Admin varsles (e-post/Teams-melding via Logic App)
5. Admin godkjenner → AutoPkg-resept opprettes → App pakkes → Tilgjengelig i Intune

### 7.2 API

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
| **Python klientagent** | Swift app | Lettere å vedlikeholde for team som allerede bruker AutoPkg (Python). Mindre kompileringskompleksitet. |
| **swiftDialog for UI** | osascript / PyObjC | Native utseende, aktivt vedlikeholdt, enkel å bruke fra kommandolinje. |
| **Azure Function** | Direkte Graph API | Sentralisert logikk, caching, enklere klientautentisering, utvidbar. |
| **Azure Table Storage** | CosmosDB / SQL | Billigst, enklest for key-value data. Nok for dette brukstilfellet. |
| **LaunchAgent** | LaunchDaemon | Kjører i brukerens kontekst (kan vise dialoger). LaunchDaemon kan ikke vise GUI. |
| **MDM sync + assignment** | Direkte download | Respekterer Intune-policyer, ingen admin-rettigheter nødvendig. |
| **Maks 3 utsettelser** | Konfigurerbar | Hardkodet default, kan overstyres via config.plist eller MDM-profil. |

---

## Neste steg

Etter godkjenning av denne planen:

1. **Start med Fase 1** – Opprette Azure Function App med de tre kjerneendepunktene
2. **Parallelt Fase 2** – Lage AutoPkg postprocessor
3. **Deretter Fase 3-4** – Klientagent + UI
4. **Testing Fase 5-6** – Utsettelseslogikk og distribusjon

### Åpne spørsmål

- [ ] Hva skal firmaprefikset være? (f.eks. `com.firma.updateagent`)
- [ ] Har dere en eksisterende Azure Function App, eller skal vi opprette ny?
- [ ] Hvilke apper er viktigst å starte med? (Firefox, Chrome, Teams, etc.)
- [ ] Skal tvungne oppdateringer tillate en grace period (f.eks. 30 min) eller kjøre umiddelbart?
- [ ] Ønsker dere logging/rapportering av oppdateringsstatus per enhet?
- [ ] Skal agenten fungere offline (cached versjonsliste)?
