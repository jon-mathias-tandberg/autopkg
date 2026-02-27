# Research: macOS Update Agent med AutoPkg + Intune

## Sammendrag

Denne researchen dekker hvordan vi kan bygge et system som automatisk sjekker om installert programvare på macOS-klienter har tilgjengelige oppdateringer, og lar brukeren velge å oppdatere eller utsette.

---

## 1. Eksisterende teknologier og verktøy

### 1.1 AutoPkg Processor-arkitektur

AutoPkg bruker en **processor-pipeline**. Hver processor arver fra `Processor`-baseklassen i `Code/autopkglib/__init__.py`:

```python
class Processor:
    input_variables = {}   # Definerer input med required/description
    output_variables = {}  # Definerer output
    description = ""
    
    def main(self):
        """Hovedlogikk – må implementeres av subklasser."""
        raise ProcessorError("Abstract")
```

**Viktige variabler tilgjengelige etter en typisk reseptkjøring:**
- `bundleid` – App bundle ID (f.eks. `org.mozilla.firefox`)
- `version` – Siste versjon funnet/lastet ned
- `NAME` – Appnavn (fra Input)
- `pathname` / `pkg_path` – Sti til nedlastet/pakket fil
- `download_changed` – Om en ny versjon ble funnet

En **postprocessor** kan kobles til via `--post` flagget i CLI:
```bash
autopkg run Firefox.intune --post com.example.VersionReporter
```

### 1.2 IntuneAppPackager (almenscorner.io)

Fra [almenscorner.io](https://almenscorner.io/posts/simplifying-macos-app-management-with-intune-autopkg-tools/):

- Bruker AutoPkg-resepter med `.intune`-suffix
- Custom processor `IntuneAppUploader` som:
  - Pakker appen som `.intunemac`
  - Laster opp til Intune via Microsoft Graph API
  - Setter metadata (versjon, bundle ID, beskrivelse)
- Bruker Azure AD/Entra ID for autentisering
- Appen blir tilgjengelig i Intune Company Portal

### 1.3 Microsoft Graph API for Intune

**Relevante endepunkter:**
- `GET /deviceAppManagement/mobileApps` – List alle apper i Intune
- `GET /deviceAppManagement/mobileApps/{id}` – Hent app-detaljer (inkl. versjon)
- `POST /deviceManagement/managedDevices/{id}/syncDevice` – Trigger MDM-sync
- `PATCH /deviceAppManagement/mobileApps/{id}/assignments` – Endre apptilordning

**macOS-apper i Intune har:**
- `bundleId` – Bundle identifier
- `buildNumber` / `versionNumber` – Versjonsinfo
- `displayName` – Appnavn

### 1.4 swiftDialog

[swiftDialog](https://github.com/swiftDialog/swiftDialog) er et open-source verktøy for å vise native macOS-dialoger fra kommandolinjen:

```bash
dialog \
  --title "Oppdateringer tilgjengelig" \
  --message "Følgende apper har oppdateringer:" \
  --infotext "3 oppdateringer tilgjengelig" \
  --button1text "Oppdater nå" \
  --button2text "Utsett" \
  --listitem "Firefox,icon=/Applications/Firefox.app" \
  --listitem "Google Chrome,icon=/Applications/Google Chrome.app"
```

**Fordeler:**
- Native macOS-utseende (SwiftUI)
- Støtter lister, ikoner, fremdriftsindikatorer
- Aktivt vedlikeholdt
- Enkelt å pakke og distribuere via Intune
- Ingen avhengigheter utover macOS

### 1.5 Intune Platform Scripts

Intune støtter **platform scripts** (shell scripts) for macOS:

- Kjører som **root** (full tilgang til system)
- Konfigurerbart **kjøreintervall** (f.eks. hver 1, 8 eller 24 timer)
- Krever **ikke** at brukeren har admin-rettigheter
- Intune håndterer distribusjon og oppdatering av scriptet
- Logger kjøreresultat tilbake til Intune for compliance

**Viktig**: Selv om scriptet kjører som root (ingen GUI), kan man vise dialoger til innlogget bruker via `launchctl asuser`:

```bash
current_user=$(stat -f "%Su" /dev/console)
uid=$(id -u "$current_user")
launchctl asuser "$uid" sudo -u "$current_user" /usr/local/bin/dialog --title "Test"
```

Dette er en velkjent teknikk i macOS-administrasjon (brukt av Nudge, DEPNotify, etc.).

### 1.6 UI-verktøy for dialoger

| Verktøy | Fordeler | Ulemper |
|---------|----------|---------|
| **osascript/AppleScript** ✅ | Innebygd i macOS, null avhengigheter, `display dialog`/`display alert`/`display notification` | Enklere UI enn dedikerte verktøy |
| **swiftDialog** | Native SwiftUI, feature-rikt, lister med ikoner | Ekstra binær å distribuere og vedlikeholde |
| **Python + PyObjC** | Fullt tilpassbar | Tung avhengighet, kompleks |
| **Notification Center** | Diskret, native | Begrenset interaksjon, kan ignoreres |

**Valg: osascript** – Null avhengigheter, innebygd, fungerer fra root via `launchctl asuser`. Tilstrekkelig for oppdateringsdialog med to knapper.

**osascript-eksempler brukt i løsningen:**

```applescript
-- Dialog med knapper (oppdatering tilgjengelig)
display dialog "Firefox: 114.0 → 115.0" ¬
    buttons {"Utsett (2 igjen)", "Installer i kveld"} ¬
    default button "Installer i kveld" ¬
    with title "Oppdateringer" ¬
    giving up after 300

-- Kritisk alert (tvungen oppdatering)
display alert "Obligatorisk oppdatering" ¬
    message "Oppdateringene installeres nå." ¬
    as critical buttons {"OK"}

-- Diskret notifikasjon (bekreftelse)
display notification "Installeres i kveld" ¬
    with title "Oppdateringer planlagt"
```

### 1.7 Klientagent-tilnærminger

| Tilnærming | Fordeler | Ulemper |
|------------|----------|---------|
| **Intune Platform Script (bash)** ✅ | Root-tilgang, innebygd scheduling, én fil, ingen runtime-avhengighet | Begrenset feilhåndtering i bash |
| **Python LaunchAgent** | Bedre kodestruktur, lettere testing | Krever Python-runtime distribusjon, ingen root, mer kompleks deploy |
| **Swift-app** | Native, best ytelse | Kompileringskompleksitet, krever Xcode, vanskelig å vedlikeholde for ikke-Swift-team |

**Valg: Intune Platform Script** – Enklest å vedlikeholde, root-tilgang løser installasjonsproblematikk, osascript er innebygd.

---

## 2. macOS Bundle ID-innsamling

### 2.1 Metoder for å finne installerte apper

**Metode A: `system_profiler` (anbefalt)**
```bash
system_profiler SPApplicationsDataType -json
```
Returnerer JSON med alle installerte apper inkl. bundle ID, versjon, sti.

**Metode B: Skanne `/Applications`**
```python
import plistlib, os, glob

for app in glob.glob("/Applications/*.app"):
    plist_path = os.path.join(app, "Contents", "Info.plist")
    if os.path.exists(plist_path):
        with open(plist_path, "rb") as f:
            info = plistlib.load(f)
        bundle_id = info.get("CFBundleIdentifier")
        version = info.get("CFBundleShortVersionString")
```

**Metode C: `mdfind` (Spotlight)**
```bash
mdfind "kMDItemContentType == 'com.apple.application-bundle'"
```

**Anbefaling**: Kombiner Metode B (direkte plist-lesing) for `/Applications` og `/Applications/Utilities` – raskest og mest pålitelig.

### 2.2 Versjonssammenligning

AutoPkg bruker allerede `LooseVersion` fra `distutils.version`. For robust versjonssammenligning anbefales `packaging.version`:

```python
from packaging.version import Version
Version("115.0.1") > Version("114.0.2")  # True
```

---

## 3. Azure Function App – Design

### 3.1 Alternativ A: Azure Function med Table Storage (anbefalt)

**Fordeler:**
- Serverless, minimale kostnader
- Enkel å vedlikeholde
- Skalerer automatisk
- Allerede i Azure-miljøet

**Tabell-struktur:**
| PartitionKey | RowKey | Felt |
|---|---|---|
| `managed_apps` | `org.mozilla.firefox` | `latest_version`, `app_name`, `intune_app_id`, `download_url`, `updated_at` |

### 3.2 Alternativ B: Direkte Graph API-kall fra klient

Klienten kan sjekke direkte mot Intune via Graph API uten mellomlag.

**Fordeler:** Ingen ekstra infrastruktur
**Ulemper:** Krever token-håndtering på klienten, mer komplekst, høyere API-belastning

### 3.3 Anbefaling

**Alternativ A (Azure Function)** er best fordi:
- Sentralisert logikk for versjonssjekk
- Cacher Intune-data (reduserer Graph API-kall)
- Enklere autentisering (klient → Function med API-nøkkel)
- Kan utvides med app-request-funksjonalitet

---

## 4. Oppdateringsmekanisme via Intune

### 4.1 Trigger oppdatering uten admin-rettigheter

Siden brukerne ikke har admin-rettigheter, er det to hovedmetoder:

**Metode A: MDM-sync trigger**
```bash
# Trigger Company Portal sync
open "companyportal://refresh"
# Eller via profiles-kommando (krever MDM enrollment)
sudo profiles -C  # Sjekk enrollment
```

**Metode B: Intune Company Portal CLI**
Company Portal kan trigges til å sjekke/installere apper.

**Metode C: Endre app-assignment via Graph API**
Azure Function kan endre en app fra "Available" til "Required" for enheten, som trigger automatisk installasjon.

**Anbefaling**: Kombinasjon av Metode A (MDM-sync) og Metode C (server-side assignment-endring) for pålitelig oppdatering.

### 4.2 Utsettelseslogikk

Lagre utsettelsesteller lokalt:
```
~/Library/Application Support/UpdateAgent/deferrals.json
{
  "org.mozilla.firefox": {"count": 2, "last_deferred": "2026-02-25T10:00:00Z"},
  "com.google.Chrome": {"count": 0, "last_deferred": null}
}
```

Maks 3 utsettelser → etter 3. utsettelse tvinges oppdatering.

---

## 5. Sikkerhet

### 5.1 Autentisering

| Komponent | Autentisering |
|-----------|--------------|
| Klient → Azure Function | API-nøkkel (lagret i macOS Keychain) eller mTLS |
| Azure Function → Graph API | Managed Identity + App Registration |
| AutoPkg → Azure Function | Service Principal / API-nøkkel |

### 5.2 Datahåndtering

- Klienten sender kun bundle IDs og versjoner (ikke sensitiv info)
- API-nøkkel kryptert i macOS Keychain
- Azure Function bruker Managed Identity (ingen hardkodede secrets)

---

## 6. Liknende prosjekter / inspirasjon

| Prosjekt | Beskrivelse | Relevans |
|----------|-------------|----------|
| [Nudge](https://github.com/macadmins/nudge) | macOS OS-oppdateringsprompt | UI-mønster for utsettelser |
| [InstallOMator](https://github.com/Installomator/Installomator) | Direkte app-installasjon | Alternativ oppdateringsmetode |
| [SOFA](https://sofa.macadmins.io/) | macOS/iOS oppdateringsfeed | Datakildeinspirasjon |
| [swiftDialog](https://github.com/swiftDialog/swiftDialog) | Native macOS-dialoger | UI-komponent |

---

## 7. Tekniske begrensninger

1. **macOS-spesifikt**: Alt klientside-kode er macOS-only
2. **Python 3.10**: AutoPkg bruker `imp`-modulen som ble fjernet i Python 3.12
3. **Admin-rettigheter**: Selve installasjonen må gå via MDM/Intune da brukere mangler rettigheter
4. **Nettverkstilgang**: Klientene må nå Azure Function-endepunktet
5. **swiftDialog**: Må distribueres separat (via Intune) til klientene
