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

### 3. Environment Variables

Koden bruker variablene som **allerede finnes** i `autopkg-api-func`:

| Eksisterende variabel | Brukes til |
|----------------------|------------|
| `AZURE_STORAGE_ACCOUNT` | Storage Account-navn (for SAS-token) |
| `AZURE_STORAGE_CONNECTION_STRING` | Table Storage-tilkobling |
| `AZURE_STORAGE_CONTAINER` | Blob container (brukes for pakker) |

Du trenger kun å legge til **én ny variabel**:

| Ny variabel | Verdi | Beskrivelse |
|-------------|-------|-------------|
| `STORAGE_ACCOUNT_KEY` | Din Storage Account access key | Kreves for å generere SAS-tokens |
| `SAS_TOKEN_EXPIRY_MINUTES` | `15` | (Valgfri) SAS-token levetid, default 15 min |

> **Merk**: Hvis Function App-en har Managed Identity med "Storage Blob Data Owner"
> (som den allerede har på `autopkgapi`), kan SAS-generering i fremtiden byttes til
> User Delegation SAS for å unngå account key.

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
