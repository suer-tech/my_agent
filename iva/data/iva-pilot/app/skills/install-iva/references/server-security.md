# Step 3 — Server security

Done means: login only by the person's key, only as `iva`, root SSH login closed, only SSH open, fail2ban on sshd,
automatic security updates on, Iva runs as `iva`. `bootstrap.sh` does all of it and is safe to re-run.

## 3.1 Run bootstrap (as root, by key)

```bash
umask 077; head -c 24 /dev/urandom | base64 | tr -d '/+=' > ~/.ssh/iva_sudo_password   # only if the file is missing
scp -q bootstrap.sh root@<IP>:/root/   # from https://raw.githubusercontent.com/smixs/iva-agent/<branch>/bootstrap.sh (branch: iva-state.md)
ssh root@<IP> 'umask 077; cat > /root/iva-pass' < ~/.ssh/iva_sudo_password
ssh root@<IP> 'IVA_USER=iva IVA_PASS="$(cat /root/iva-pass)" IVA_PUBKEY="<public key line>" IVA_TZ=<tz> \
  IVA_DISABLE_PASSWORD_AUTH=1 bash /root/bootstrap.sh --non-interactive; rm -f /root/iva-pass'
```

The password is read from a file on the server, so it never appears in a local command line. `<tz>` comes from the person's
computer (`readlink /etc/localtime` on macOS; `tzutil /g` on Windows, mapped to IANA) — confirm it in the pre-flight
table. Re-run on an already prepared server: bootstrap skips what is done (`facts.md`: second run changes nothing).

## 3.2 Never lock out — order matters

Bootstrap closes root and password login and reloads sshd without dropping the open session. Before you close your
root session, prove `ssh -o BatchMode=yes iva true` works. If it does not: keep the root session, fix `authorized_keys`
of `iva`, retry. Do not log out of root while `iva` cannot log in.

## 3.3 Reboot

`/var/run/reboot-required` after bootstrap → `ssh iva 'sudo -S -p "" systemctl reboot' < ~/.ssh/iva_sudo_password`,
wait for port 22 (every 10 s, up to 5 min), `ssh iva true`. The installer refuses to start while a kernel reboot is
pending.

## 3.4 Verify and show the person

```bash
ssh iva 'sudo -S -p "" sh -c "sshd -T | grep -E \"^(passwordauthentication|permitrootlogin|pubkeyauthentication|kbdinteractiveauthentication) \"; ufw status; fail2ban-client status sshd | head -3; systemctl is-active unattended-upgrades"' < ~/.ssh/iva_sudo_password
ssh -o PreferredAuthentications=password -o PubkeyAuthentication=no -o BatchMode=yes root@<IP> true   # must be refused
```

Expected: `passwordauthentication no`, `permitrootlogin no`, `pubkeyauthentication yes`, `kbdinteractiveauthentication no`,
ufw active with only 22/tcp, fail2ban jail `sshd` present, unattended-upgrades active, the password probe refused. Tell
the person in two lines: "Only your computer's key can enter the server now. The password from the email no longer
works."
