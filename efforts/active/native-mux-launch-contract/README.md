# Native Mux Launch Contract

_This proposal was prepared with GitHub Copilot._

- **Slug:** `native-mux-launch-contract`
- **Repo:** copilot-extensions
- **Branch(es):** sequential managed proposal and implementation PRs against `dev`
- **Created:** 2026-10-22
- **Status:** Active
- **Vision:** closes [session-hosting](../../../visions/session-hosting/README.md) host-owned presentation and exact initial context; preserves [architecture invariants](../../../docs/patterns/README.md)
- **Umbrella issue:** #7962

## Guiding Intent

An interactive `agent-worktrees copilot` invocation must keep terminal input
with its attached mux until that client exits. Reattaching by the mux's native
command must not lose worktree status. Resuming a conversation must execute in
the selected worktree rather than restore an unrelated anchor directory.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Fix driver | Own the bounded attach, connected-session status and directory repairs through review and source acceptance | Managed source worktree `soldered-bearing` |
| Connected-status implementer | Own bounded connected-mux discovery/routing and focused regressions; no launch, lifecycle, Git publication or effort writes | Fix driver's in-session coding delegate |
| Existing native recovery owner | Retain restart-recovery implementation and activation | #4564; `steady-resistor` |
| Existing session-host owners | Retain provider selection, remote identity and graceful cutover | #352, #5961 and #4496 |

## Coordination

- **Topology:** reviewed proposal first, then one cohesive source repair.
- **Host (owns PRs):** Fix driver.
- **Delegates:** Connected-status implementer owns status-specific
  Manager/engine helpers and tests; Fix driver owns terminal/directory launch,
  shared-loop integration, documentation, effort and publication.
- **Scope:** existing native CLI and Manager mux boundaries, not reboot
  recovery, arbitrary foreign mux adoption, provider replacement or rollout.
- **Handoff:** preserve this complete objective, source workspace, pending
  review and remaining validation; a merged slice is not parent completion.

## Context

- The Windows `copilot` terminal handoff currently calls `os.execvp`, while
  the Manager's Windows foreground launcher waits for its child. A Windows
  overlay is not the POSIX process-replacement/terminal-custody contract.
- Direct embodiment creates its mux without going through the Manager's
  normal launch-script mapping registration. Existing attachment observation
  sees registered mappings, not every native attachment.
- Copilot advertises `-C <directory>` as an explicit working-directory
  selection. A mux's pane starting directory and a resumed conversation's
  recorded workspace are different evidence.
- The reporter identified the wrong directory through Copilot session
  metadata or its displayed workspace, not an independently observed shell
  `pwd`. The repair must test actual execution and distinguish historical
  metadata from current process context.
- Existing native restart, provider attachment identity and daemon cutover
  owners remain unchanged. No installed payload, live session or global
  terminal/feed configuration is patched by this effort.

## Request

The operator reported (verbatim, with no private identifiers):

> Bugs in `agent-worktrees copilot`:
> 1. Although it seemingly rendered the mux session in the terminal, I could not interact with it; key presses fell through to the backing console. I had to use `psmux attach ____` to get back to the session manually
> 2. Manually re-attaching to the mux session failed to ensure the Worktree Manager mux daemon pushed sttaus to it. We should ensure that the Mux daemon pushes status to all muxes with active connections
> 3. The CWD of copilot in that session was somehow set to the anchor repo root, and not the worktree folder.

The operator confirmed `native-mux-launch-contract` and clarified that the
directory observation came from "Copilot session metadata or displayed
workspace." The implementation and validation decomposition below is
**agent-recommended**, not additional operator scope.

## Plan

### Phase 1 - Reviewed contract and bounded reproduction

- [x] Review and merge this proposal before implementation.
- [x] Establish fail-first terminal-custody, manual-attachment status and
  resumed-directory regressions at their existing owning boundaries.

### Phase 2 - Existing-owner repairs

- [x] Keep the Windows interactive attach caller waiting with inherited
  terminal streams; preserve POSIX replacement, detach and exit-code behavior.
- [x] Ensure every connected, attributable worktree mux receives status through
  existing Manager/engine ownership, including native manual reattachment.
  Preserve incarnation fences and standalone operation when Manager is absent.
- [x] Pin the selected execution directory through native Copilot launch/resume
  without rewriting session history, dropping the conversation, or accepting a
  foreign worktree identity.
- [x] Update authoritative launch/status documentation and changefiles for all
  affected shipping owners.

### Phase 3 - Source acceptance and conclusion

- [x] Exercise focused tests and native Windows consumer boundaries; record
  exact evidence and any unavailable platform or interactive tier.
- [ ] Land the repair through genuine current-head review and required CI.
- [ ] Resolve every Plan and Validation Plan item, journal the result and
  archive this effort. Release and productive rollout remain explicitly
  separate, and no updater is authorized by this source-fix request.

## Validation Plan

- [x] Terminal tests prove the Windows parent cannot return before its attach
  client finishes, inherits rather than pipes input/output, propagates client
  failure, and preserves POSIX `exec` behavior.
- [x] Connected-session controls include multiple sessions, manual reattach,
  daemon restart, stale mapping/incarnation refusal and disconnected sessions.
  Unknown attachment probes must not become confirmed zero or claim ownership.
- [x] Deferred to `#7962`: Resume tests use an anchor-origin conversation and a distinct selected
  worktree; prove the actual child's directory, not only launch JSON or a title.
- [x] Native Windows checks exercise the real process boundary in contained,
  disposable state. A synthetic mux client does not prove full human keyboard
  interaction; any unavailable real terminal tier is stated explicitly.
- [x] Deferred to `#7962`: POSIX behavior is exercised where available, or remains an explicit
  native-CI obligation without representing Windows compatibility as POSIX proof.
- [x] Run touched-code lint, install/resource contracts and the relevant
  focused regression portfolios without adding a broad promotion matrix.
- [ ] Review final documentation and graceful-cutover impact; verify actual
  source merge before reporting completion. No running-state claim follows
  from source tests or a merge.

## Proposal

Repair the existing foreground attachment and status-routing contracts through
their current owners. Preserve the session-host process boundary and reuse
native directory selection instead of editing Copilot's stored transcript.
Qualify actual native execution separately from metadata and wrapper output.

Documentation impact: the CLI reference describes foreground attachment,
directory selection and historical metadata; the Manager README describes
discovery, incarnation fencing, optional-provider behavior and on-demand
startup. This effort and its index retain progress and unearned acceptance.
The changefile requests patch releases for both shipping owners; no version
is hand-edited.

Graceful cutover impact: the existing mux daemon gains a discovery request,
not another service or presentation owner. Its route, drain, retirement and
installer-driven cutover remain unchanged. New status applies carry the
mapping incarnation; startup uses the selected owner's existing ensure
operation with bounded headless custody. A reachable older peer is not
restarted or updated to force acceptance. No productive rollout is performed.

## Journal

### 2026-10-22 - Report and scope confirmation

- Confirmed the effort slug and the reporter's directory-observation source.
- Checked current open mux/provider PRs and adjacent attachment/restart issues.
  Existing recovery and provider owners retain their distinct work.
- Traced the Windows overlay call, direct embodiment and Manager mapping seams.
  No executable source or installed runtime has been changed.

### 2026-10-22 - Reviewed plan and native launch regressions

- Proposal #7965 merged after current-head Copilot approval and successful
  required CI. Implementation began only after pulling that reviewed plan
  forward.
- The terminal/directory regressions failed first: nine failures distinguish
  the Windows overlay and missing/foreign native-directory selection.
- The first native PowerShell forwarding check exposed a second real defect:
  an attached short `-C` value containing a drive colon was split at the
  dot-sourced setup boundary. Normalized setup now receives a named directory
  parameter and constructs native arguments after its own parameter parsing.
- The complete focused launch portfolio passes 178 cases, including the real
  Windows child lifetime/input control and normalized PowerShell execution
  with a spaced selected directory. The executable stand-ins do not constitute
  human terminal interaction or native Copilot conversation-resume acceptance.
- A bounded status implementer owns only connected-mux discovery/routing and
  its focused regressions; the fix driver retains launch integration, review
  and completion. No running agent, mux, service or installed payload is patched.

### 2026-10-22 - Integrated connected-session repair

- Connected-session discovery now joins unambiguously attributable native
  worktree muxes to the existing status pipeline. Healthy mappings avoid
  repeated roster/probe work; unresolved incarnations have a completion-based
  retry cadence and bounded fair selection.
- Review corrected permanent refusal of legitimate fresh incarnations:
  native confirmation and an atomic registry identity/revision comparison
  preserve tombstones and concurrent replacements without trapping a new
  incarnation behind an old teardown.
- Positive attributable demand can start an already-selected dormant Manager
  through its declared command and existing ensure lifecycle. Absent Manager
  remains standalone; unsupported peers defer visibly without restart or a
  direct-writer bypass. An explicit legacy sink-root override is preserved;
  conflicting automatic startup is refused rather than silently retargeted.
- Independent final engine/status validation passes 222 cases under the
  ordinary runner limits. The complete Manager discovery/daemon/attachment
  group passes 134 cases under its unchanged 120-second containment, including
  the native startup case that had previously failed in a larger worker run.
  The launch portfolio separately passes 178 cases.
- A read-only real PSMux enumeration confirmed the required native identity,
  attachment and original-directory format fields on every observed session.
  It did not create, attach, stop or modify any user session.
- Full Docker clean-room execution is unavailable because the installed Docker
  client has no running engine endpoint. Disposable Windows process/IPC/setup
  controls were exercised instead. Human keyboard/real Copilot resume
  acceptance and native POSIX execution remain with #7962; this source-only
  task does not authorize productive rollout or claim those outcomes.

### 2026-10-22 - Current-base reconciliation and final source checks

- Reconciled the current contribution base while preserving unrelated effort
  rows and the new `get git-remote` documentation. Range comparison showed
  only documentation-context differences in the replayed repair.
- The combined native portfolio exposed a wake-test lifecycle race: startup
  could already set the event, the loop could consume it before the final
  assertion, and an exhausted two-value supersession iterator could prevent
  bounded retirement. The fixture now isolates the real request's wake,
  captures its actual reply-time transition and retains superseded state
  through drain. No production wake behavior, deadline or test selection was
  weakened. An intermediate attempt hit the unchanged 300-second bound;
  the corrected complete portfolio passes 403 cases in 82.98 seconds.
- Checked the startup helper's POSIX custody contract rather than assuming a
  Windows Job covers other platforms. POSIX startup has its own process group;
  timeout cleanup retires that group while its leader is still owned, before
  reaping. The focused client portfolio passes 18 cases, including the
  platform-parameter cleanup control. This is not native POSIX qualification.
- All changed Python passes focused lint. Installer closure, source-size and
  headless-launch checks pass; no ceiling or deadline was raised.
