# Step 1 — Target

Find where Iva goes. Propose the first match and ask the person to confirm; change nothing before the answer.

1. Environment: `IVA_HOST`, `IVA_SSH`, `VPS_IP`, `VPS_HOST`, `SERVER_IP` (`env | grep -E '^(IVA_HOST|IVA_SSH|VPS_IP|VPS_HOST|SERVER_IP)='`;
   Windows: `Get-ChildItem Env: | Where-Object Name -Match 'IVA_HOST|IVA_SSH|VPS_IP|VPS_HOST|SERVER_IP'`).
2. `~/.ssh/config`: a `Host iva` block, or a host whose name contains `iva`.
3. Mail, if you have a mail tool: messages from the last 30 days with "server is ready", "root password", "IP address",
   "VPS", "сервер готов", "данные для доступа". Show the person the sender, date and IP — never the password.
4. Otherwise ask: "Paste the email from your hosting provider, or say 'this computer' to install on this Mac."

From a hosting email take: IPv4, user (usually `root`), password. Save the password to a file with mode 600 at once
(`references/secrets.md`) and refer to the file from then on.

"This computer" is valid only on macOS (Iva 0.5.4+): skip steps 2–3, run the installer locally (`install.sh` detects
macOS and sets up launchd), and tell the person Iva works while the Mac is on, awake and logged in.
