# Update Agent for macOS

Automatisk oppdateringssystem for macOS-apper, integrert med AutoPkg + Intune.

## Arkitektur

```
AutoPkg → Blob Storage + API → Platform Script → Notifikasjon → Installasjon
```

## Komponenter

### Azure Function (`azure-function/`)
Tre endepunkter i eksisterende `autopkg-api-func`:
- `POST /api/check-updates` – Returnerer tilgjengelige oppdateringer med SAS-token
- `POST /api/register-version` – Registrerer nye versjoner fra AutoPkg
- `POST /api/log-event` – Audit-logging

### Platform Scripts (`scripts/`)
- **`setup_update_agent.sh`** – Engangskjøring via Intune. Oppretter config og lagrer API-nøkkel i System Keychain.
- **`update_agent.sh`** – Kjører på intervall via Intune. Scanner apper, viser notifikasjoner, installerer oppdateringer.

### AutoPkg Pipeline (`autopkg_tools/register_packages.py`)
Post-steg i GitHub Actions som laster opp pakker til Blob Storage og registrerer versjoner via API.

## Intune-oppsett

### 1. Forutsetninger
- `terminal-notifier` installert på Mac-ene (via Homebrew eller Intune LOB-app)
- Azure Function App deployet med `check_updates`, `register_version`, `log_event`
- Blob Storage container `packages` i `autopkgapi` storage account

### 2. Deploy setup-script (én gang)
Rediger `scripts/setup_update_agent.sh` med riktige verdier og last opp:
- Intune → Devices → macOS → Shell scripts
- Run as: root
- Frequency: Not configured (kjøres kun én gang)

### 3. Deploy update-agent (på intervall)
Last opp `scripts/update_agent.sh`:
- Intune → Devices → macOS → Shell scripts
- Run as: root
- Frequency: Every 8 hours

### 4. GitHub Secrets
| Secret | Beskrivelse |
|--------|-------------|
| `UPDATE_AGENT_API_URL` | Function App API base URL |
| `UPDATE_AGENT_API_KEY` | Function App key |

## Brukeropplevelse

| Kjøring | Hva brukeren ser |
|---------|-----------------|
| Oppdatering tilgjengelig | Notifikasjon med app-ikon: *"Versjon X installeres neste gang du lukker appen"* |
| App lukkes | Oppdatering installeres automatisk (stille) |
| 3+ kjøringer uten lukking | Tvungen dialog: *"Lukk appene for å fullføre oppdateringen"* |

## Sikkerhet
- API-nøkkel lagres i macOS System Keychain (kryptert, kun root-tilgang)
- SAS-tokens utløper etter 15 minutter
- OIDC for blob-upload i GitHub Actions
- Ingen sensitiv info i config-filer
