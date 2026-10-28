# Workspace with a server snapshot

Read LOCAL-SERVER-COPY.ru.md before modifying or running the imported project.
The active Iva deployment is 5.63.152.247:/opt/iva. Local iva/ and lead-flow/
are verified snapshots downloaded on 2026-10-28, not live financial state.
Do not start a second Telegram bot or write live financial changes into the
local snapshot. Do not deploy or run imported code merely to inspect this copy.
Keep private configuration, credentials, data and archives out of Git.
Previous local files are preserved in .local-before-server-sync-20261028/.

The Git repository is this main_agent directory, with origin
https://github.com/suer-tech/my_agent.git and default branch main. Commit the
public sources recorded in SOURCE-MANIFEST.json; keep the private snapshot,
runtime data and one-time personal finance scripts excluded by .gitignore.
