#!/usr/bin/env bash
# Creates the three 1Password items the triage ExternalSecrets read, in the Homelab vault.
# Secrets are read with hidden prompts or from files and piped to `op` as a JSON template on
# stdin, so no value lands on a command line, in shell history, or on screen.
#
# Usage: bash scripts/triage-1p-setup.sh ~/Downloads/vollminlab-triage.<date>.private-key.pem
set -euo pipefail
umask 077
VAULT=Homelab

exists() { op item get "$1" --vault "$VAULT" --format json >/dev/null 2>&1; }

labels() {
  op item get "$1" --vault "$VAULT" --format json | python3 -c '
import json,sys; d=json.load(sys.stdin)
print("   fields:", sorted(f["label"] for f in d["fields"] if f.get("value") and f["label"]!="notesPlain"))'
}

# create TITLE NOTE  (reads "label<TAB>value" lines on stdin; values may be multi-line only via PEM_FILE)
create() {
  local TITLE="$1" NOTE="$2"
  TITLE="$TITLE" NOTE="$NOTE" python3 -I -c '
import json, os, sys
fields = [{"id": "notesPlain", "label": "notesPlain", "type": "STRING", "purpose": "NOTES",
           "value": os.environ["NOTE"]}]
for line in sys.stdin.read().splitlines():
    label, _, value = line.partition("\t")
    if label == "password" and value.startswith("@file:"):
        value = open(value[6:]).read().strip() + "\n"
    kind = "STRING" if label.endswith("_id") else "CONCEALED"
    fields.append({"id": label, "label": label, "type": kind, "value": value})
print(json.dumps({"title": os.environ["TITLE"], "category": "SECURE_NOTE",
                  "tags": ["Homelab"], "fields": fields}))
' | op item create --vault "$VAULT" - >/dev/null
  echo "   created \"$TITLE\""
  labels "$TITLE"
}

# --- 1. GitHub App ------------------------------------------------------------------------
T="Triage GitHub App"
echo "1) $T"
if exists "$T"; then echo "   already exists, skipping"; labels "$T"; else
  PEM="${1:-}"
  [[ -f "$PEM" ]] || { echo "   pass the downloaded .pem path as the first argument"; exit 1; }
  openssl pkey -in "$PEM" -noout 2>/dev/null || { echo "   $PEM is not a valid private key"; exit 1; }
  read -rp "   App ID (number on the App's General page): " APP_ID
  read -rp "   Installation ID (number at the end of the install URL): " INST_ID
  [[ "$APP_ID" =~ ^[0-9]+$ && "$INST_ID" =~ ^[0-9]+$ ]] || { echo "   both IDs must be numbers"; exit 1; }
  printf 'github_app_id\t%s\ngithub_app_installation_id\t%s\npassword\t@file:%s\n' \
    "$APP_ID" "$INST_ID" "$PEM" \
    | create "$T" "Referenced by ExternalSecret triage/triage-github-app — do not rename fields"
  # Read the key back and prove the newlines survived (the known 1Password PEM failure).
  op item get "$T" --vault "$VAULT" --format json | python3 -I -c '
import json,subprocess,sys
v = next(f["value"] for f in json.load(sys.stdin)["fields"] if f["label"]=="password")
ok = v.startswith("-----BEGIN") and v.count("\n") > 5 and subprocess.run(
    ["openssl","pkey","-noout"], input=v.encode(), capture_output=True).returncode == 0
print("   PEM read back:", "OK (multi-line, valid key)" if ok else "BROKEN - newlines lost")
sys.exit(0 if ok else 1)'
  echo "   now delete the download:  shred -u '$PEM'"
fi

# --- 2. Anthropic ----------------------------------------------------------------------
T="Triage Anthropic API Key"
echo "2) $T"
if exists "$T"; then echo "   already exists, skipping"; labels "$T"; else
  read -rsp "   Paste the triage-agent API key (hidden): " KEY; echo
  [[ "$KEY" == sk-ant-* ]] || { echo "   that does not look like an Anthropic key (sk-ant-...)"; exit 1; }
  printf 'api_key\t%s\n' "$KEY" \
    | create "$T" "Referenced by ExternalSecret triage/triage-anthropic-apikey — do not rename fields"
  unset KEY
fi

# --- 3. Pushover -----------------------------------------------------------------------
T="Triage Pushover"
echo "3) $T"
if exists "$T"; then echo "   already exists, skipping"; labels "$T"; else
  read -rsp "   Paste the new Triage app's API token (hidden): " TOKEN; echo
  [[ "$TOKEN" =~ ^[A-Za-z0-9]{30}$ ]] || { echo "   Pushover tokens are 30 letters/digits"; exit 1; }
  USER_KEY=$(op read "op://$VAULT/Kubeadm Cert Pushover/user-key")
  printf 'token\t%s\nuser-key\t%s\n' "$TOKEN" "$USER_KEY" \
    | create "$T" "Referenced by ExternalSecret triage/triage-pushover — do not rename fields"
  unset TOKEN USER_KEY
fi

echo "Done. Tell Claude, and it will verify the items and mint a test GitHub token."
