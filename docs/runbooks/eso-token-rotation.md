# ESO Token Rotation

Use when the ESO access token needs to be rotated (compromise, periodic rotation).

## Procedure

1. Sign in: `op signin`

2. List current tokens to find the token ID:
   ```bash
   op connect token list --server "vollminlab-k8s"
   ```

3. Create a new token:
   ```bash
   op connect token create "eso-access-token" --server "vollminlab-k8s" --vaults Homelab
   # Copy the new token value
   ```

4. Update the vault item:
   ```bash
   op item edit "Connect Server Credentials" --vault Homelab "eso_token[concealed]=<new-token>"
   ```

5. Update the cluster secret:
   ```bash
   kubectl create secret generic onepassword-connect -n 1password \
     --from-literal=token="<new-token>" \
     --dry-run=client -o yaml | kubectl apply -f -
   ```

6. Verify ESO reconnects:
   ```bash
   kubectl get clustersecretstore onepassword-cluster-store
   # Status should return to Ready within 30s
   ```

7. Revoke the old token:
   ```bash
   op connect token revoke --server "vollminlab-k8s" --token <old-token-id>
   ```
