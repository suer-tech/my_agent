# Step 0 — Pre-flight

Run both checks before changing anything. Show the person one table: what is there, what is missing, what you will do.
Stop if something is missing that you cannot fix yourself, and give one instruction to fix it.

## A. The person's computer

| Check | macOS / Linux | Windows (PowerShell) | If missing |
|---|---|---|---|
| OS and shell | `uname -s; echo $SHELL` | `$PSVersionTable.PSVersion; [Environment]::OSVersion` | — |
| OpenSSH client | `ssh -V` | `Get-WindowsCapability -Online -Name OpenSSH.Client*` (State: Installed); `ssh -V` | Windows: `Add-WindowsCapability -Online -Name OpenSSH.Client~~~~0.0.1.0` in an admin PowerShell, or Settings → Apps → Optional features → OpenSSH Client. Stop until done |
| OpenSSH ≥ 8.4 | from `ssh -V` | from `ssh -V` | password login without a terminal is unavailable → use the "person logs in once" path in `access.md` |
| `ssh-keygen` | `command -v ssh-keygen` | `Get-Command ssh-keygen` | comes with the client |
| `~/.ssh` and other keys | `ls -la ~/.ssh` | `Get-ChildItem $env:USERPROFILE\.ssh` | create the folder; leave every existing key alone |
| Existing `Host iva` | `grep -n "^Host iva$" ~/.ssh/config` | `Select-String -Path $env:USERPROFILE\.ssh\config -Pattern '^Host iva$'` | it may already point to the person's server → step 1 |
| Network to the server | `nc -z -w 5 <IP> 22` | `Test-NetConnection <IP> -Port 22` | server still booting → retry every 15 s for 5 min; else ask the person to check the server in the hosting panel |
| Mail tool | your own tool list | your own tool list | none → ask for the hosting email text in step 1 |

## B. The server (first login, before any change)

```bash
. /etc/os-release; echo "$PRETTY_NAME"; uname -m          # Ubuntu 22.04/24.04 or Debian 12; x86_64 or aarch64
free -m | awk '/Mem:/{print $2}'                           # ≥ 1024 MB (the installer adds swap below 1.5 GB)
df -Pm / | awk 'NR==2{print $4}'                           # ≥ 5000 MB free
ss -ltnp 2>/dev/null | awk 'NR>1{print $4}'                # what already listens
getent passwd | awk -F: '$3>=1000 && $3<65000{print $1}'   # existing human users
test -e /var/run/reboot-required && echo REBOOT_NEEDED
fuser /var/lib/dpkg/lock-frontend 2>/dev/null && echo APT_BUSY
```

| Finding | Action |
|---|---|
| Not Ubuntu/Debian | stop: the installer supports Ubuntu/Debian (apt) servers |
| < 1 GB RAM or < 5 GB disk | stop: name the minimum and suggest the next plan at the host |
| Something listens on 80/443 or other services run | ask before installing next to them |
| A user `iva` already exists | step 4 decides (existing install or a stranger's account) |
| `APT_BUSY` | the fresh server is installing updates — wait, re-check every 30 s |
| `REBOOT_NEEDED` | fine before bootstrap; bootstrap and step 3 handle the reboot |
