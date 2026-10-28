# Active deployment

Iva runs on the Ubuntu VPS `5.63.152.247` in `/opt/iva`.
The authoritative financial database is `/opt/iva/data/local-finance.sqlite3`
on that server. In the Windows checkout `E:\Projects\main_agent`, the local
`data/local-finance.sqlite3` is the cutover snapshot, not current financial state.
On the VPS the same relative path under `/opt/iva` is live. Do not post live
financial changes to the Windows snapshot.

Read `data/remote-deployment.json` and `docs/VPS-DEPLOYMENT.ru.md` before runtime
changes. Use `deploy/remote.py` for SSH; its pinned host key and private deployment
key are in ignored local data. Never print credential files or VPN private keys.

Services: `iva-vpn`, `iva-telegram-proxy`, `iva@gateway`, `iva@engine`,
`iva@telegram`, `iva@memory`, and `iva-backup.timer`. Codex uses the dedicated
AmneziaWG network namespace via `/usr/local/bin/iva-codex`; Telegram uses the
private HTTP proxy. SSH, Cartesia, and the public web browser use the host route.

The local bot, engine and memory autostart are disabled. Do not reactivate them
while the server owns the Telegram token; rollback requires stopping the server
and reconciling its current data first. Test financial writes in isolated databases.
Deploy code changes to the server when they must affect the running assistant;
local edits alone do not update it. Privileged VPN helpers are installed as
root-owned files under `/usr/local`, separately from the iva-owned application.

Migration completed 2026-10-23. SSH management from this computer uses port 443; standard port 22 remains enabled. The previous VPS 185.182.110.82 is a stopped rollback snapshot. Never start its bot or VPN alongside the active server. Deployment transport selects the active host from data/remote-deployment.json.

Deterministic disk/RAM monitoring runs through iva-resource-monitor.timer every 3 minutes, without models. Root-owned helper: /usr/local/lib/iva/resource_monitor.py; persistent state /var/lib/iva-resource-monitor/state.json and RAM fallback /run/iva-resource-monitor/state.json. Preserve these settings/state during future moves.

The Avtootklik AI backend is installed alongside Iva in /opt/hh-extens (hh-extens.service, separate PostgreSQL hh_extens). HTTPS api.aixmode.ru and management SSH share port 443 through Nginx stream; preserve /etc/nginx/streams-enabled/https-ssh.conf and the loopback iva-management-ssh listener on 22444. Fallback SSH 22443 remains available. Do not replace the multiplexed port with a standalone SSH or HTTPS listener. Runtime configuration is private /etc/hh-extens/backend.env. App sources and detailed documentation: E:\Projects\hh_extens\docs\ARCHITECTURE-DEPLOYMENT.ru.md. The future Threads promotion workflow is recorded in docs/THREADS-AUTOOTKLIK.ru.md; no posting automation is enabled.

Threads browser login preparation exists under /opt/iva/data/threads-browser (private persistent profile, owner iva). Temporary threads-browser/display/wm/vnc/webview units are not enabled on boot; they do not publish. threads.slice has CPU 60%, RAM high 896M/max 1024M, swap 256M, and launch requires 640MB MemAvailable. Only this browser uses the private proxy via THREADS_BROWSER_PROXY. SSH-only UI: deploy/threads_ui_tunnel.py -> localhost:15082; remote webview/VNC stay loopback. Do not copy Codex browser cookies or reuse the MAX profile. See docs/THREADS-AUTOMATION-DESIGN.ru.md for actual status and selected browser transport.

Browser alternation is installed on 2026-10-23: iva-browser-coordinator, 15-minute cooperative turns, shared flock, native owner-bound browser_control tool. MAX and Threads must not run Chromium concurrently; use the queue, never raise limits or bypass guards. MAX checkpoints preserve source/range/completed links and delivery states; pause only at safe boundaries, never during send acknowledgement. Current server Threads profile needs_login; no Threads publisher installed. See docs/BROWSER-TURNS.ru.md. Five MAX sources now have cooperative hooks; preserve them and runtime-guard/browser_turn.py. Coordinator restart must not stop current jobs (Wants, not Requires dependency).

Owner clarified: timed alternation ONLY when both MAX and Threads have real background jobs. Interactive MAX windows and Threads sign-in must never be rotated by elapsed time. With one working service let it continue; explicit owner switch remains available. Current Threads service is interactive, not a publisher; background_work deliberately excludes it until a real worker is implemented and registered.

Threads one-shot publication pilot now exists (2026-10-23), superseding older notes that no comment has been published: threads-promotion.service posted one authorized comment https://www.threads.com/@suer_stuff/post/De1t1hvCAjX . Persistent server login survived restart. Private browser RPC and SQLite prevent replay. This is NOT the continuous discovery/publishing worker or its Iva control integration; background-work handoff remains unimplemented for this test runner. See docs/THREADS-LIVE-TEST.ru.md before launching anything; the completed one-comment test must never be reset or blindly rerun.

MAX automation is installed separately at /opt/max (compatibility symlink /root/max130), exact production Python sources from 151.248.126.26. Its private account profile is /opt/max/state/browser. All MAX workers and Chromium belong to max.slice: CPUQuota=60%, MemoryHigh=750M, MemoryMax=950M, MemorySwapMax=256M. Iva has higher CPU/IO priority and OOM protection. Preserve runtime-guard/sitecustomize.py and PYTHONPATH: the OS lock permits only one browser controller and refuses launch below 384MB MemAvailable. Do not bypass it or start multiple queues/accounts. MAX panel listens only on loopback; deploy/max_ui_tunnel.py forwards localhost:15080 over pinned SSH. Never copy account sessions, campaigns or API keys from production 151, or send MAX messages as a diagnostic without owner authorization.

MAX text generation and existing photo OCR now use max-codex.service through /run/max-codex/completions.sock (User=iva, max.slice), via deploy/max_codex_service.py and /opt/max/max_llm.py. Existing Codex authorization/VPN shim and orchestrator model profile are reused, with isolated ephemeral context and tools disabled. OpenCode primary routing is unchanged. Six MAX sources are now patched; preimages in /opt/max-deploy/before-codex and original SHA manifest. RouterAI connector/key storage remain for future image generation; no automatic paid fallback. Source runtime installer must not overwrite these adapters. See deployment docs and deploy/install_max_codex.py for installation/rollback.

MAX opening deferral installed 2026-10-24 in six broadcast modules, retaining Codex and browser_turn hooks. Persistent deferred/open_failures/open_retry_at/open_last_error state must survive updates. Exact preimages and installation report: /opt/max-deploy/deferral-20261024; deployment helper deploy/install_max_deferral.py. Do not overwrite these modules with old Codex/browser installers. See VPS deployment docs.

MAX announcement layout installed 2026-10-25: preserve broadcast_formatting.py, broadcast_format_repair.py and format_body integration in broadcast.rewrite, alongside Codex/deferral hooks. Repair affects only text of current never-sent ready/deferred/waiting_join rows, preserves digest/facts/state/schedule, and rejects archive/current-campaign races. Backup and verification: /opt/max-deploy/format-20261025; installer deploy/install_max_format.py. At install this instance had zero campaigns/messages.


Unmet-demand research installed 2026-10-25: preserve hourly job 2f9611a667cc493a,
kind=demand_research, and its owner task/notification policy. Native public web +
bounded HTTP/PDF collection uses no Chromium. Separate persistent evidence DB:
/opt/iva/data/demand-research.sqlite3; cards/data and native-vault idea exports
under demand-research. Preserve checkpoints, evidence, revisions, export indexes
and manual-card conflict protection. demand_research is the native read tool;
scheduled_tasks manages scheduling. Never regress this to generic Bing-only agent
or enable shell/MCP in research. deploy/backup.py includes the new DB/cards.
Deployment/tests/rollback: docs/DEMAND-RESEARCH.ru.md and server
/opt/iva/data/demand-release-20261025-r2. Financial Codex/base ledger unchanged.

Research breadth/adaptive priority upgraded 2026-10-25: four search batches,
up to 48 candidate URLs, three-page fetch/analysis checkpoints, persistent
source-grounded question branches and stricter evidence gates. Preserve
demand_batches, demand_questions/events, notice signatures and scheduler cycle
anchors. Current release: /opt/iva/data/demand-release-20261025-v3-r2 plus its
monitor-followup. Do not install the old v2 worker over the migrated schema.
Research retains a PID/boot/start-bound coordinator lease and owner.lock through
the complete cycle; cron only starts cycles, the 15-minute browser rotation
does not interrupt research. Release after terminal Store.finish/outbox commit,
explicit pause/cancel, or a recorded technical suspension. MAX/Threads queue and
send-ack checkpoints remain intact. Threads subtracts research-pauses.json time
from its active budget; planned waits are not incidents and new repair-model
jobs defer to research. Preserve the root-owned coordinator installed separately
under /usr/local/lib/iva-browser. Upgrade helper: deploy/upgrade_demand_research.py.

Research team orchestration deployed 2026-10-25: new plan.version=3 cycles use
an actual planner, buyer/alternatives/foreign/critic researchers in separate
contexts, coordinator reviews, bounded child tasks, and final synthesis.
Preserve demand_agent_* tables, task/batch mappings, source observations and
research-cycle-<id>.md reports alongside hypothesis cards. Up to four root and
four child tasks, depth two, 72 candidate URLs; execution is sequential under
the existing research lease, never concurrent Chromium/model workers. Legacy
cycles keep their saved format. Failed/deferred checks and unresolved synthesis
cannot qualify demand or masquerade as completed validation. Research-specific
minimum_reasoning_effort must be applied AFTER Codex profile reload; do not
modify the shared Iva model profile to implement it. Current releases:
/opt/iva/data/demand-release-20261025-agents and -agents-quality. Full live run
proved four roles and memory export but requested no children; do not describe
recursive live execution as verified. See docs/DEMAND-MULTIAGENT.ru.md for exact
test evidence, limits and the separate reasoning-floor probe.

Research review/retry repair deployed 2026-10-25 at 13:42 UTC, superseding the
earlier live-child caveat: production cycles already completed four depth-one
children. Release /opt/iva/data/demand-release-20261025-review-retry passed 318
server tests and four real medium model calls on isolated historical copies.
The probe admitted a depth-two child after explicit approval and verified its
quote/checkpoint/dedup, but did not execute that child's search. Preserve dynamic
review-key/source-id schemas, exact quote gates with one decorative wrapper
normalization, branch_reason, report excerpt metadata, and international source
lineage. All specialist reports use at least medium without changing shared Iva
model settings. Research incident state survives checkpoints and same-kind
schedule updates; terminal technical limitations remain in last_error, ordinary
source gaps are not service failures. Preserve demand_notice_state and cycle,
pending-result and lease cursor fields. No per-step retry/recovery spam. Current
verification: release/verification/review-report.json; docs/DEMAND-MULTIAGENT.ru.md.

OpenCode provider recovery deployed 2026-10-25: global health can remain true
while /provider returns 503 and inference never starts. Preserve provider
preflight, native-tool rejection, bounded diagnostics, and shared invocation
flock at data/opencode-runtime/inference.lock (iva:iva 0660) through cleanup.
Root-owned /usr/local/lib/iva/opencode_health.py runs via iva-opencode-health.timer
every minute; private persistent state /var/lib/iva-opencode-health/state.json.
It restarts only iva-opencode after three confirmed provider503 observations,
with deployment+inference exclusive locks, owner/research/session idle guards,
15-minute cooldown and max two attempts/hour. No models, notifications, ledger
writes or shared-profile changes. Preserve the root helper and both units on
future moves. OpenCode remains 1.19.3 / deepseek-v4.1-flash-free; no blind major
upgrade or old install_opencode.py overwrite. Release data/opencode-repair-20261025
passed 38 server tests and two real PrimaryBridge probes on an isolated ledger;
workflow routing used an inert stub. See docs/OPENCODE-RECOVERY-20261025.ru.md.

Research storage retention deployed 2026-10-25 at the completed-cycle boundary.
Preserve demand_retention archive readers, demand_schedule_retention protections,
and demand_storage admission/runtime. Full research intermediates live 14 days;
older terminal cycles become semantic archives, only unpinned source bodies and
duplicate scheduler result JSON are compacted. Hypotheses/evidence/questions,
active/pending-export checkpoints, dedup, manual cards and vault files stay.
Root-owned iva-demand-retention.timer checks hourly at :25 UTC, compacts at most
daily unless continuing a backlog, and uses deployment/maintenance/backup locks
plus the normal research lease. It never interrupts research or interactive
browsers. New cycles require 1 GiB free; existing cycles continue. One replaceable
private before-retention.tar.gz lives in data/demand-storage outside daily backup
includes. Daily backup retains 14 days with capacity/atomic-file guards. Never
VACUUM the live financial DB or restore its old snapshot over newer operations.
Release /opt/iva/data/demand-release-20261025-retention passed 386 server tests;
aged copies proved compaction and unchanged knowledge/checkpoints/finances.
Initial production run no_old_material (no records older than 14 days). See
docs/DEMAND-STORAGE.ru.md and release/final-verification.json. Old readers cannot
be blindly restored once real archive compaction has occurred.

Research direction/citation/network repair deployed 2026-10-25; active release
/opt/iva/data/demand-release-20261025-scope-network-r2. New plan.scope v1 binds a
cycle to software demand OR commerce for MAX; connected pivots remain allowed.
Independent tool-free audits gate plans, children and proposed hypotheses. Keep
the broad owner task out of stage prompts and do not rewrite historical cycles.
Citation repair has two persisted attempts, exact source/word gates and concrete
retry feedback; corrected synthesis citations require a separate semantic review.
Unverified child requests are explicit limitations, not failure of a completed
parent report. Do not add unsupported JSON Schema uniqueItems to native output
schemas: uniqueness is enforced locally. Preserve repair/audit checkpoints.

iva-research-fetch.service is enabled: root-owned helper
/usr/local/lib/iva-research-fetch/server.py runs as iva inside the EXISTING
iva-codex namespace, private /run/iva-research-fetch/fetch.sock. It does not restart
or edit the VPN. Source reads retry only explicit recognized geographic denials;
successful exact-host routes live in private data/research-network/routes.json
for 7 days, failed probes cool down 1 hour (max 256 hosts). Generic 403, CAPTCHA,
login and 429 are not geographic proof. No session cookies or browser profiles.
470 server tests and native scope/real translated-quote repair probes passed.
Real public VPN reads passed; the geo-denial trigger was simulated in an isolated
cache. Habr read succeeded; Reddit returned generic 403. See
docs/DEMAND-DEPLOYMENT-20261025-SCOPE.ru.md and docs/DEMAND-SCOPE-AND-NETWORK.ru.md.


Verified automatic recovery notices deployed 2026-10-25 in
/opt/iva/data/recovery-notices-release-20261025-r3 (plus recorded SQLite-sidecar
unit amendment). Owner now wants a brief Telegram explanation AFTER a real
repair/recovery check: failure, action and passed check. This supersedes the old
blanket silence on technical recoveries; per-attempt failure/recovery spam remains
suppressed. Keep existing repair authorization scopes. The deterministic
iva-recovery-notices.timer polls each minute, no models. Own durable database
data/recovery-notices/notices.sqlite3 is included in backup; baseline never
replays history, sending after crash becomes uncertain, no automatic retry of
uncertain Telegram sends. status.recovery_notices exposes fresh delivery/proofs.
Read docs/RECOVERY-NOTICES.ru.md before changing notification evidence gates.

OpenCode root helper exports sanitized bounded confirmed restart history to
/var/lib/iva-opencode-health-events/events.json, root:iva0640 in root:iva0750
directory; private watchdog state remains private. Threads repair DB stays
mode=ro and mount-read-only in notifier; only its directory permits SQLite WAL/SHM
sidecar creation. Never use immutable=1 against this live WAL database.

Gateway restart propagates to iva@telegram through Requires/After. Gracefully
pause Telegram at an idle admission boundary BEFORE restarting gateway; release
the short SQLite admission barrier before waiting for Telegram long-poll exit.
Never hold a writer lock throughout that wait. Restore previous Telegram activity
and do not interrupt active owner/research work. Final r3 checks confirmed all
24 code/unit hashes, live status, protected state, services and a real setup
Telegram acknowledgement; no synthetic repaired incident was sent.

Owner paused unmet-demand research on 2026-10-25, asking to continue tomorrow.
Schedule 2f9611a667cc493a is paused; no automatic resume is scheduled.
Manual cycle b209a41de445479a is checkpoint-suspended under
data/demand-supervised-20261025-attempt3 (same job/attempt must be reused).
All supervised processes stopped and the research lease was released. Do not
resume either schedule or manual run without a new owner instruction. The SQL
cycle can still say running while its operator wrapper is explicitly suspended.
Read docs/DEMAND-SUPERVISED-20261025.ru.md before continuing. Latest deployed code:
/opt/iva/data/demand-content-quality-release-20261025-r2, 251 server tests plus
successful native replay of the historical duplicate-request failure. The third
full corrected cycle is NOT yet completed. Only iva@scheduler imports and runs
these scheduled demand stages; iva@research is the separate tender worker.

Owner resumed the supervised check on 2026-10-26. The SAME attempt 3/cycle
b209a41de445479a is now terminal completed_with_gaps: 42 pages analyzed, 8 tasks
done, 0 failed, 7 deferred, three signal hypotheses, report plus three cards
verified in native vault. Pre-pause completed tasks were not replayed; lease
released. The periodic schedule 2f9611a667cc493a remains PAUSED, no automatic
resume. Do not reset the completed manual attempt.

Current research release /opt/iva/data/demand-discovery-rescue-release-20261026
passed 218 server tests. Preserve one persisted alternative-source search per
role with zero readable sources, same-task 12/6 and cycle72 URL budgets, failed
URL/closed-host exclusions, and failed second-batch propagation. No VPN/access
guard relaxation. Preserve role-isolated hypothesis identity/test contracts;
without a real previous_test the result must remain not_tested. Accumulated
notes are history, not a verified current list; new card exports label this.
Historical cards/cycle were not rewritten. A real isolated foreign-role replay
proved discovery/fetch/analyze/report (one readable pricing source of six), not
a new complete cycle or a complete foreign-market comparison. Shared locks,
production data, notifications and regular schedule stayed protected. See
docs/DEMAND-SUPERVISED-20261026.ru.md and release verification-rescue report.

Evidence-adequacy research upgrade installed 2026-10-26:
/opt/iva/data/demand-coverage-release-20261026. New cycles opt in with
plan.evidence_coverage_version=1; preserve old cycle contracts and the completed
manual attempt. agent_coverage is an independent tool-free medium assessment
of the SAME assignment, including literal quotes and role-specific requirements.
Pricing/vendor statements never substitute for direct user experience. Preserve
same-task evidence_coverage_assessed/search/closed checkpoints and task.result
evidence_coverage, including semantic archive readers. Initial 8/4 URL searches
reserve 4/2 inside unchanged 12/6 task and 72 cycle limits. At most two followups,
counting any prior discovery_rescue; no invented child citations, closed-host
bypass, automatic schedules or unlimited retries. Unresolved questions remain
deferred; evaluator technical exhaustion is unavailable/agent_coverage_failed,
not a false successful recovery. Incomplete coverage blocks confident promotion,
weakened priority notices and conclusive branch verdicts. Same-scope/job planning
can reuse archived gaps; unrelated scopes cannot.

This release passed 321 server tests/19 suites and 15 native probe gates on a
private pricing-only foreign-role copy: actual assessment/search/assessment/report,
one remaining URL, new forum HTTP 403, honest deferred gap, zero newly read reviews.
Do not describe this as complete foreign validation or a new full live cycle.
All 35 combined code hashes and protected production state verified; lease idle,
services active, gateway read API already updated. Regular schedule 2f9611a667cc493a
still PAUSED. Older code cannot resume an active agent_coverage checkpoint; use
a safe boundary or compatible reader for rollback. See
docs/DEMAND-EVIDENCE-COVERAGE.ru.md and release/final-verification.json.

Owner explicitly changed the research retry policy on 2026-10-26: an ordinary
HTTP 403 / access_denied must get one retry through the EXISTING research VPN
reader even without explicit geography wording. This supersedes the older
geo-only trigger above. Keep recognized CAPTCHA/authentication/429 exclusions,
exact-host scope, seven-day successful cache, one-hour failed-probe cooldown,
public-address/redirect guards and all research/browser locks. Do not relabel
generic 403 as proven geography: reason=http_403 / cached_http_403. Persist
direct_http_status and vpn_http_status in network_route and route-cache evidence.
Never alter VPN keys/global routes or copy sessions to implement this fallback.
See docs/DEMAND-HTTP403-VPN.ru.md and the follow-up release
/opt/iva/data/demand-http403-release-20261026. The periodic research schedule
remains paused; completed evidence/cycles must not be reset for this change.

HTTP403 follow-up installed and verified: 235 server tests / 12 suites,
10 live gates, actual forum direct403 -> VPN307 -> disallowed_url at the redirect
guard. No forum text was recovered; do not claim successful VPN access there.
All 40 combined code hashes, research tables, protected finance/schedule state,
host/VPN routes and root reader/unit preserved. Gateway needed a safe idle reload
with graceful Telegram pause/restoration; its actual API now reports the new
policy. Services active, research lease released, schedule still paused.

MAX pacing upgraded 2026-10-26: policy yekaterinburg_09_22_batches_v4, ordinary batch gaps 2–5 minutes with distinct neighbors, up to 8 recipients, unchanged short intra-batch delay range. Budget only uses 30s/chat in three build_plan expressions; operational retime overhead is 15s. Preserve safe v2/v3 migration of unopened ready batches, begun batch identity, Codex/layout/deferral hooks. Opening retries remain 5/30 minutes and autorun retry300 unchanged. Backup/reports /opt/max-deploy/pacing-20261026; helper deploy/install_max_pacing.py. Installation had no campaigns; no messages sent.

OCR lead research installed 2026-10-26 at /opt/lead-flow, separate database
/opt/lead-flow/data/research.sqlite3 and native Iva lead_research tool. Preserve
the existing research lease, public readers, exact quotes, bounded followups,
writer/reviewer checkpoints and same-company/outreach dedup. Configuration
/etc/lead-flow/research.json is currently enabled=true, mode=ingest,
crm_transport=iva_vpn; the research
timer is enabled/active. The quarter-hour
admission timer permits one new cycle/day or saved continuation, not a new study
each tick. Scheduled service returned daily_cycle_already_completed with zero
models after activation: 26 October's slot is terminal, no pending research loop;
the next new search is 2026-10-27 09:00 Yekaterinburg. Generic demand schedule
2f9611a667cc493a remains paused.
CRM PR2 merged as e263639 after initial PR1 f8eb62a; Vercel Production
CRssM64M6ThGvaLNcofSArG3xLML is Ready. Owner-approved CRM commissioning passed
12 live gates: two authenticated HTTP 200 ingests returned the same lead_id 279,
first created=true, replay created=false/replayed=true. UI has 122 active cards
(previously 121), exactly one MYFORCE, needs_review/fit50/intent0, three sources,
zero CRM letters. No model, draft API or SMTP call in commissioning. Preserve
production research tables and isolated evidence. Mail ai.studio.ufa@mail.ru has no supplied
app password: SMTP/IMAP and live delivery remain disabled/unverified. First live
preview read 5 of 7 URLs and rejected 3 candidates; second cycle
1d108003540a48708b8458474074d4d7 completed with 17 model calls, 17 of 20 URLs read,
4 rejected candidates and no letters. Attempted URLs are not readable pages;
oneshot inactive after completion is normal. No qualified-buyer or live-letter
success is proven by these runs. Preserve CRM original_created/current eligibility,
manual fields, cancelled/uncertain first-message history and suppression.
The v2 audit found quote-format and dropped footer-contact defects. The separate
contact-reader release /opt/iva/data/lead-contact-release-20261026 is installed:
102 server tests passed; ordinary, opt-in and actual VPN reads of the ArbitrKIT
PAU page proved unchanged body plus one opt-in public footer contact. Preserve
default-off contact fragments and official-domain gates; this does not prove
buyer intent or cross-domain identity. Pipeline4 installed 47 runtime files plus
three static assets for complete CRM HTTP tests; 70 server worker/HTTP/probe tests
passed after quote feedback. First cached native quote probe failed its long
identity quote after one call. Second passed literal quote repair and independent
research review in six calls: 30->6 / 29->12 words, non-quote fields/source data
unchanged, no search/fetch/CRM. Its two letter attempts failed review. Subsequent
writer/renderer and pinned provenance changes produced one ready local draft in
isolated native letter-only run b09e37e9f2da4d099bfd79eb3dadc954: three calls
(two author, one reviewer), all eight review flags true. MYFORCE remains
needs_review/intent0; source/assessment/critic unchanged, no CRM/SMTP. Production
v2 still has four rejected candidates and zero drafts. After that probe a second
fixed criteria sentence was removed from the renderer; no separate model probe
of that cosmetic deletion. Current full local pytest passed 149 tests/49 subtests;
87 server worker/transport/doctor tests passed in 1.211s,14 Linux helper tests,
7 Linux commissioning tests,43 native/control/installer/audit tests in 0.194s.
Do not claim a new full search, qualified buyer or mail delivery from those probes.
Host DNS resolves Vercel, but direct HTTPS failed on both returned A-addresses
(one TCP timeout, the other TCP success followed by TLS timeout);
existing iva-codex VPN public GET returned 200 with verified TLS. Upstream cause
is unproven. Authenticated ingestion now works through the separate installed
bridge: lead-flow-crm.socket enabled/active, root-owned helper
/usr/local/lib/lead-flow-crm/request.py, Useriva in existing iva-codex namespace,
fixed origin and only POST /api/agent/leads or /drafts, no redirects/retries.
Token /etc/lead-flow/crm-agent.token is root:iva0640; never print it or move it
into the public source reader. Global routes/VPN keys unchanged. Mail claim,
ack and inbox routes require separate future commissioning.
Native gateway status was actually verified after mode=ingest activation:
proof_status=verified, proofscope=scoped_ingest, last_verified_at
2026-10-26T11:15:14.661207+00:00, current_api_health=not_checked. Proof is fixed
/var/lib/lead-flow-crm/commissioning.json, root:iva0640 in root:iva0750 directory;
strict schema/origin/transport/owner/mode, UTC timestamp not future and <=7 days.
Status never reads credentials or calls CRM. last_confirmed_export remains null:
the main research DB still has no ACK; isolated commissioning is separate.
Preserve /opt/iva/data/lead-crm-status-release-20261026 preimages and verification.
Final post-commissioning runtime audit passed 107/107 gates with 80 reviewed
file hashes, enabled ingest/VPN transport and scoped_ingest proof. This is
separate from the real two-HTTP-200 card/dedup check, not a mail delivery test.
Final public UI/documentation commit 82ee987 is production Ready in Vercel
deployment DevwsoiGAuFEcLPE41JLaUkMBZzm. Preserve the private CRM token/proof,
existing namespace helper and root-owned units; never route public source reads
or mail through this two-path research CRM socket.
Read docs/LEAD-FLOW-DEPLOYMENT.ru.md before changing this deployment. Release:
/opt/iva/data/lead-flow-release-20261026; native root helper
/usr/local/bin/iva-lead-research-control and /var/lib/iva-lead-research state.
Lead maintenance ran successfully at 15:10:14 Yekaterinburg on 2026-10-26:
/opt/iva/data/lead-flow/backups/lead-flow-20261026-101014.tar.gz, one database,
zero compacted bodies/pruned backups/model calls. Maintenance timer is enabled
and active, next due 2026-10-27 03:30 Yekaterinburg. Preserve separate backup/retention
locks and evidence/ACK protection. A future scheduled backup is not yet verified.

MAX generation resilience installed 2026-10-26: preserve TextGenerationError, rejected-text/retry-instruction/variation feedback, pending generation_failures/retry_at/last_error and campaign generation cooldown. Local failed passes retry10/30min then manual-only, provider cooldown5min does not consume per-recipient attempts. Ready queue always takes priority; generation chunks <=8 only with no ready; partial send route/UI remain actionable. Preserve Codex/layout/pacing v4/open retry/ACK/browser_turn and manual stop. Release/backups/tests /opt/max-deploy/generation-20261026; installer deploy/install_max_generation.py. No campaign/send created during installation.


## OCR acquisition throughout the day — 2026-10-26

Owner now authorizes broader acquisition and one first discovery email per
company. This supersedes daily-only/qualified-only OCR admission. Campaign
ocr-russia-v3: 120-minute local slots 09:00–21:00, up to12companies/72URL attempts/
100model calls per slot, eight diversified queries, max84candidates in seven daily
slots. Preserve checkpoints and the shared research lease; missed slots are not
replayed. General demand schedule2f9611a667cc493a stays PAUSED.

New contact_ready means independently verified supported document process,
company attribution and official business contact, with demand still unknown.
It can create an independently checked discovery letter without inventing buyer
intent. CRM owner policy permits queueing these first letters; one initial letter
per company/recipient across campaigns, suppression/refusals/uncertain outcomes
remain durable. Claim budget counts all started attempts: max100companies/local
day, at least300seconds apart, window09–22Yekaterinburg.

PR3 merged776355b; Vercel Production Cc9uz91CyFnqs97dzKbFoV39J2zg Ready.
Runtime deployed in /opt/lead-flow, root-owned private CRM helper extended with
fixed claim/result/replies/heartbeat/maintenance routes, no arbitrary paths or
credentials in models. lead-flow-mailer.timer is enabled every5minutes09–21;
service ConditionPathExists=/etc/lead-flow/mail-app-password. Credentials remain
ABSENT; mail.json enabled/send_enabled=false, CRM_OUTREACH_ENABLED=0. Do not
claim emails are being sent. Mail activation needs the actual owner-supplied app
password and separate SMTP/IMAP/receipt checks. Password must never be printed.

Research/source history backed up before cutover. Release:
/opt/iva/data/lead-discovery-release-20261026. Compiled native control release:
/opt/iva/data/lead-discovery-control-20261026; idle engine rebuild and graceful
Telegram restore passed with protected state unchanged. Previous compiled backup
was preserved by using a fresh control release. First fresh cycle793f49b3637c4da2b83f1b4639fd9474 completed12:10:39UTC:12companies,62URLattempts/56readable,69model calls,4CRM cards(ids280–283),3ready first letters,zeroSMTP. It used pipeline5. Pipeline6 installed afterward broadens independent fit review to supported practice/product profiles, saves fit-without-contact cards, rejects unsuitable HR/press/privacy/no-reply mailboxes and avoids followups solely to prove demand. Explicit worker.reconsider performs one new bounded re-review of saved prospects without resetting its terminal source or repeating discovery. A source-budget interruption may carry unread URLs forward. Do not pass this reconsideration off as a fresh search. Its transient service is lead-flow-reconsider-20261026.
Server155tests/49subtests and14root-helper tests passed; private claim got actual
HTTP409 while global outbound is disabled, with zero SMTP calls.

Final acquisition verification26Oct: source cycle793f49b3637c4da2b83f1b4639fd9474 remained completed/69calls/12candidates/62pages and IDs280–283. Reconsideration76d65a82f3e947d1a288c0d2798974e6 completed12:17:04UTC,9calls:adlawyers.ru284,delfarvater.ru285,lgsl.ru286,each with ready first draft. Across those same12distinct discovered companies:7CRM cards,6ready letters,0SMTP. This is live proof of the broader profile gate, not a new discovery of3companies. The original source run was preserved. Research timer restored enabled/active; next ordinary slot17:00 admitted real run3e9ebb91c0c94c95b45af63f06c6846f,still running at final gateway check. Do not reset it. The general demand schedule remains paused. Mailer timer enabled/active but password absent, mail flags false, CRM_OUTREACH_ENABLED0. Final reviewed audit114/114 (85source hashes), actual gateway reported120-minute interval/max12/discovery true and live CRM acknowledgements. Reports:/opt/iva/data/lead-flow-release-20261026/discovery-final-verification.json and /opt/iva/data/lead-discovery-release-20261026/gateway-final.json. PR4 mergedce4c4d1 after PR3. Full local158tests/49subtests; server137targeted/40subtests, earlier full155/49, realPostgreSQL27/27, helper14/14.

UI follow-up de0bbd0 clarifies that acquisition_runs currently records mailer passes, not the separate research DB. The empty mail journal is not evidence of an idle researcher. Integration status words now fit their cards. Only static JS/CSS were atomically updated under application-deploy.lock while the independent research worker continued; worker/model inputs and credentials were untouched. Fixed source preimages/manifest are in lead-discovery-release-20261026/ui-manifest.json.

Mailbox activation26Oct supersedes earlier absent/disabled OCR mail notes. Owner supplied the app password in a private local .env; only the selected value was transferred, never printed, and is stored /etc/lead-flow/mail-app-password iva:iva0600. Production mail.json enabled/send_enabled=true; CRM_OUTREACH_ENABLED=1 applied in Vercel deployment8ZGUTohJYzZeqiBh6vgpXJPcmAcZ Ready. MFA verification was completed by the owner through Vercel. SMTP authentication/NOOP and read-only IMAP passed. Initial baseline skipped113old messages,0ingested/0sent. Mailer timer enabled/active09–22Yekaterinburg every5minutes,max1send/run andCRM100companies/day/300seconds spacing. Three actual SMTP DATA250 messages had distinct IDs,3CRM ACKs,minimum318seconds between attempts at final check. Provider acceptance is not recipient inbox delivery; Sent-folder mutation is intentionally not implemented by this transport. The mailing queue and local journal, not the mail provider Sent folder, are authoritative for attempts and receipts. No automatic retry of uncertain sends.

Native lead_research.status.mailer now reads the separate journal mode=ro and exposes transport outcomes/last_run/configured switches without credentials, recipients, bodies or claim tokens. Top-level email_sent=false is still researcher-only. Read-only Python metadata update used deploy/install_lead_mail_status.py and safe gateway reload; enginePID/finances/profiles/schedules were preserved. New native privacy/readonly tests passed40/43subtests combined with audit tests. Mailer pacing/off-hours reasons no longer masquerade as queue_empty;19local and19server tests/7subtests passed. Activation/proofs/preimages:/opt/iva/data/lead-mail-activation-20261026; final-verification.json and native-status/verification.json. Source researchschedule and browser/MAXlocks unchanged; old general research stayspaused.
