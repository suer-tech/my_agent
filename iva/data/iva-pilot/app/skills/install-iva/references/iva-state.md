# Step 4 — Iva: install, repair, update, or leave alone

Read the state first (as `iva`, with `export PATH=$HOME/.local/bin:$PATH`):

```bash
ssh iva 'export PATH=$HOME/.local/bin:$PATH; command -v iva && iva version; iva status 2>&1 | tail -20; test -f ~/iva/.env && echo ENV_PRESENT; if test -f ~/iva/.iva-installed; then cat ~/iva/.iva-installed; fi'
curl -fsSL https://api.github.com/repos/smixs/iva-agent/releases/latest | grep -m1 '"tag_name"'
```

| State | Action |
|---|---|
| No `iva` command, no `~/iva` | First install → 4.1 |
| Iva checkout has `.iva-installed` containing `autostart=false` | Resume the first install → 4.1, even if services are running; repeat installation configures autostart and sends confirmation |
| Iva checkout has `.iva-installing` and no `.iva-installed` | Resume the interrupted first install → 4.1; the installer reuses finished stages |
| Installed, services active, version = latest release | Leave alone. Tell the person "Iva is already installed (version X) and running" and go to step 6 |
| Installed, services stopped or failing | `iva doctor` (it repairs what it can), then `iva restart`; read `iva logs` if still failing |
| Installed, older than the latest release | `iva update`, then step 6 |
| Iva under another user or path | ask the person which one is theirs; do not touch the other |

A Mac target: same table, run locally (no `ssh iva`).

## Branch

Install the release from `main` by default. If the person explicitly asks for beta, fetch the beta installer and set
`IVA_BETA=1` for the installer process. These are the two supported installation paths.

## 4.1 First install

Write the minimal env file from step 5 (`references/keys.md`), copy it to the server with mode 600, then:

```bash
ssh iva 'umask 077; cat > ~/iva-setup.env' < <local env file>
ssh iva 'curl -fsSL https://raw.githubusercontent.com/smixs/iva-agent/main/install.sh | bash -s -- --env-file ~/iva-setup.env; rc=$?; rm -f ~/iva-setup.env; exit $rc'
```

For an explicitly requested beta installation, use this installer command instead:

```bash
ssh iva 'curl -fsSL https://raw.githubusercontent.com/smixs/iva-agent/beta/install.sh | IVA_BETA=1 bash -s -- --env-file ~/iva-setup.env; rc=$?; rm -f ~/iva-setup.env; exit $rc'
```

`--env-file` runs without questions, including when a terminal is available. It is accepted for a first install or an
interrupted first install; a completed installation uses `iva config` instead.

The installer validates the keys, fills everything it derives (bearer, webhook secret, bot username, port, vault),
installs autostart and messages the person from the bot. It takes 3–10 minutes; the Chromium download is the longest
part. A dropped SSH connection stops setup and releases the installer lock. Recreate the temporary env file and run the
same command again: finished stages are skipped. An interactive repeat walks the setup with saved values as defaults.

Failure handling: read the last 40 lines of the installer output, fix the named cause (a wrong key or token → back to
step 5), re-run. Do not continue past a failed installer with manual steps.
