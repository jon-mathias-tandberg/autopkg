# Update Agent – Azure Function tillegg for autopkg-api-func

Disse funksjonene legges til i den eksisterende `autopkg-api-func` Function App.

## Nye funksjoner

| Funksjon | Route | Beskrivelse |
|----------|-------|-------------|
| `check_updates` | POST /api/check-updates | Klient sender bundle IDs, får tilbake oppdateringer med SAS-token |
| `register_version` | POST /api/register-version | AutoPkg registrerer nye versjoner |
| `log_event` | POST /api/log-event | Audit-logging fra klienter |

## Deploy til eksisterende Function App

### 1. Legg til avhengigheter i `requirements.txt`

Legg til disse linjene i den **eksisterende** `requirements.txt`:

```
azure-data-tables
azure-storage-blob
packaging
```

### 2. Kopier funksjonskataloger

Kopier følgende kataloger inn i roten av Function App-prosjektet:

```
check_updates/
register_version/
log_event/
shared/
```

### 3. Legg til Environment Variables

I Azure Portal → `autopkg-api-func` → Settings → Environment variables:

| Variabel | Verdi | Beskrivelse |
|----------|-------|-------------|
| `STORAGE_CONNECTION_STRING` | `DefaultEndpointsProtocol=https;AccountName=...` | Connection string til Storage Account |
| `STORAGE_ACCOUNT_NAME` | `dittlagringsnavn` | Storage Account navn (for SAS) |
| `STORAGE_ACCOUNT_KEY` | `din-nøkkel` | Storage Account key (for SAS) |
| `BLOB_CONTAINER_NAME` | `packages` | Blob container for .pkg/.dmg filer |
| `SAS_TOKEN_EXPIRY_MINUTES` | `15` | SAS-token levetid (minutter) |

> `TABLE_MANAGED_APPS` og `TABLE_UPDATE_EVENTS` er valgfrie
> (default: `ManagedApps` og `UpdateEvents`).

### 4. Opprett Blob container

I Storage Account → Containers → `+ Container`:
- Navn: `packages`
- Access level: Private

### 5. Deploy

```bash
func azure functionapp publish autopkg-api-func
```

Table Storage-tabellene opprettes automatisk ved første API-kall.
