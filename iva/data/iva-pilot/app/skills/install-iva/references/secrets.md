# Secrets: passwords, bot token, API keys

A secret lives in a file with mode 600 and moves through stdin or a copied file. It never appears in a command line,
in output you print, or in your messages. Why: command lines are visible in `ps` on both machines and stay in shell
history and in the session transcript.

| Need | Safe | Unsafe |
|---|---|---|
| Keep a secret the person gave you | `umask 077; cat > "$TMPDIR/iva-secrets/host-pass"` (paste via stdin, or write with your file tool) | `PASS=...` in a command |
| Password login (OpenSSH ≥ 8.4) | `SSH_ASKPASS=<script that cats the file> SSH_ASKPASS_REQUIRE=force DISPLAY=:0 ssh -o PubkeyAuthentication=no root@IP …` | `sshpass -p "$PASS"` |
| sudo on the server | `ssh iva 'sudo -S -p "" <cmd>' < ~/.ssh/iva_sudo_password` | `ssh iva "echo '$PASS' \| sudo -S <cmd>"` |
| Env file to the server | `scp` a 600 file, or `ssh iva 'umask 077; cat > ~/iva.env' < localfile` | heredoc with values inside the remote command |
| Check a bot token | `curl -s "https://api.telegram.org/bot$(cat tokenfile)/getMe"` from a script file, not inline in a long chain you print | printing the URL |

Windows PowerShell: read secrets with `Get-Content -Raw <file>` piped to `ssh … 'cat > …'`; restrict files with
`icacls <file> /inheritance:r /grant:r "$($env:USERNAME):F"`.

After the run: delete every temporary secret file. Keep only what the person needs later, next to their key and with the
same protection: `~/.ssh/iva_sudo_password` (mode 600), the sudo password of user `iva`. The hosting root password is
useless after step 3 — do not keep it.
