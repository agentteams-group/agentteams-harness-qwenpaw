# Natural Task collaboration

The native middleware binds direct Leader Human sources as well as Manager sources, ends covered Leader intake before planning, and saves compact terminal tool facts plus verified actual-send receipts. Only the natural acceptance/final business report is visible; ordinary submit/plan control facts remain in native history. Existing tool approvals are preserved.

Use the exact Core revision in `versions.env` (AgentTeams PR #1725). Task rules remain in Core: original-conversation intake creates the Task room before planning; normal successful nodes directly notify ready downstream Workers; exceptions and final acceptance notify the Leader. Do not add receiver-specific wakeups, visible ACKs, self-mentions or a second model request for a receipt.

Prepared model context uses `task_context.model_context` / the public `model_context` operation. Terminal model output uses `task_native.model_result` / `model_result`: preserve Task/submission/plan/completion identities, primary receipt and verified extra receipts while omitting repeated canonical snapshots. A queued notification is not an actual send; only the authenticated Core event with `native_id=matrix:$event` supplies the sent fact. No sent fact proves downstream execution.

Native tests and local model/API fixtures are separate from real Matrix multi-Worker end-to-end acceptance. Record source/image versions, tool-call and native-session receipts, model-call counts, exact artifact hashes, remote persistence, Trace visibility and any interventions. Do not describe synthetic fixture success as full Harness admission certification.

Recovered and live inputs both bind the authenticated Matrix actor. A same-owner `completed` Task may settle an existing tool return and persist its actual native history, but never reopen inference or admit new tools. Silent final callbacks still require an authoritative durable result. Successful planning emits no control-receipt chat.

A Leader's direct intake must pass the role gate and use its own registered Team Admin; it cannot read the Manager-only global Human registry. Native Task intake removes legacy ProjectFlow/TaskFlow escape paths. Internal task-room handoff notices enter the normal verified coordination ingress, including a same-identity actor/recipient; plain self messages remain suppressed.

The QwenPaw channel converter consumes AgentScope text-block events. A terminal native callback emits those events and a final Msg without a ModelCall event or provider request; returning Msg alone passes Agent.reply fixtures but leaves real streaming channels without a final result. Thinking/tool/placeholder events carry `org.agentteams.progress_only` at the producer so other Harnesses never ingest them as business input.

A handoff session is `coordination-handoff:<event-id>` even when it carries a main Task. Only assignment/review/main-ready sessions append the main-task attempt suffix. Check actual Controller session identities rather than duplicating a suffix across all kinds.

Internal coordination turns do not advertise private-conversation stop/steer controls, even while a newly created Task room temporarily has two members. Only ordinary Human DM turns publish direct-execution lifecycle events; native planning, tool progress and coordination receipts continue normally.

Project native QwenPaw envelope tool calls and outputs into the shared Matrix run blocks. Preserve the actual call ID, tool name, redacted arguments, result and failure status; edit the corresponding call rather than counting its output as another tool. Correlate reasoning and the final reply with that same run. Display-only messages carry no mentions and never become model input or a new inference request.

Scheduled assistant reminders enter the native scheduled lifecycle before the SDK cron/bootstrap hooks. They must not consume Human onboarding, create its completion flag, run its package setup or request setup-file deletion. Ordinary Human messages retain native bootstrap behavior; reminder-looking text alone does not select the scheduled lifecycle. Native lifecycle regression coverage exercises the installed CronContextHook and BootstrapHook after verified channel admission.
