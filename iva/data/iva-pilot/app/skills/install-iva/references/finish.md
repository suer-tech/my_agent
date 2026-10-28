# Step 6 — Finish

Done means the bot answered the person — not that the installer printed "ready".

1. The installer's message "✅ Iva is installed and online" arrived in the person's chat (ask them, or see the
   installer output: "Sent you a confirmation in Telegram").
2. Ask the person to write the bot anything. Watch: `ssh iva 'journalctl --user -u iva-telegram-poll -n 30 --no-pager'`
   (Mac: `iva logs`) — an incoming update and a sent reply within 2 minutes.
3. No reply in 2 minutes → `ssh iva 'export PATH=$HOME/.local/bin:$PATH; iva doctor'`, fix what it names, `iva restart`,
   ask again.
4. Check the short login one last time: `ssh -o BatchMode=yes iva 'export PATH=$HOME/.local/bin:$PATH; iva status' `
   (≤ 5 s, no prompts).
5. Delete temporary secret files (`references/secrets.md`).

Tell the person, in their language, in at most 8 lines:
- Iva is installed and answers in @<bot username>;
- to reach the server: `ssh iva` — macOS: in Terminal; Windows: in PowerShell or Windows Terminal;
- voice and web search: in the bot, `/menu`;
- the server password from the email no longer works; access is by this computer's key (`~/.ssh/iva_ed25519`) —
  keep it, and keep `~/.ssh/iva_sudo_password` (the server's admin password) private;
- running this again later checks and repairs Iva.

If you cannot wait for the person (non-interactive run): stop after step 1 and the doctor check, and tell them to write
the bot and run this again if it stays silent.
