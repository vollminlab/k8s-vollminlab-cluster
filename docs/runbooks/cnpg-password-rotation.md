# CNPG Password Rotation

CNPG ExternalSecrets use `refreshInterval: "0"` (create-once). Rotation is coordinated.

## Procedure

1. Update the password in 1Password vault item (field: `password`).

2. Force one ESO sync:
   ```bash
   kubectl patch externalsecret <name> -n <namespace> \
     --type=merge -p '{"spec":{"refreshInterval":"1s"}}'
   ```

3. Confirm sync completed (check Ready=True, last sync timestamp updated):
   ```bash
   kubectl get externalsecret <name> -n <namespace> -o wide
   ```

4. IMMEDIATELY reset to prevent continuous re-sync:
   ```bash
   kubectl patch externalsecret <name> -n <namespace> \
     --type=merge -p '{"spec":{"refreshInterval":"0"}}'
   ```

5. Rotate the password in the database:
   ```bash
   kubectl cnpg psql <cluster-name> -n <namespace>
   # In psql: ALTER ROLE <username> WITH PASSWORD '<new-password>';
   # Verify: \du
   # Exit: \q
   ```

6. Verify app connectivity (check app logs for auth errors).
