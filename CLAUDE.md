# CLAUDE.md

## Fork Notes (esm7/ccbot)
This is a fork of [six-ddc/ccbot](https://github.com/six-ddc/ccbot). Everything below
this section is upstream's own guidance, unmodified -- read it as authoritative for
the codebase itself. This section covers only what the fork adds and how to maintain it.

### Why this fork exists
The fork drives "Mike", a personal always-on Telegram bridge to Claude Code (a headless
Debian NUC running `ccbot` under `systemd --user`). It started as a few patches for
behaviours upstream lacks, originally hand-edited inside the installed virtualenv
(machine-local and gitignored, so every venv rebuild silently reverted them).

It is now Erez's own customized version of ccbot: changes are made freely to fit how
Mike is used, not limited to small patches or to what upstream would accept. The one
standing constraint is that syncing with upstream must stay possible -- see the
maintenance section below.

### What the fork carries
Notable fork features, kept here so their rationale survives (not an exhaustive or
ordered commit list -- `git log upstream/main..mike` is the source of truth):
1. `feat(transcribe): make model, language and prompt configurable` -- upstream hardcodes
   `gpt-4o-transcribe` and never sends `language` or `prompt`. Adds
   `CCBOT_TRANSCRIBE_{MODEL,LANGUAGE,PROMPT}`. Defaults reproduce upstream behaviour
   exactly; empty language/prompt are omitted from the request rather than sent empty,
   which matters because an empty prompt is not a no-op for the gpt-4o transcribers.
2. `feat(telegram): forward shared locations to Claude Code as lat,lng` -- upstream's
   catch-all rejects Telegram location messages as unsupported. Adds `location_handler`,
   registered before that catch-all. Covers venues too (per the Bot API a venue message
   also populates `location`), prepending title and address since those cannot be
   recovered from coordinates. Live locations are ignored by design: their updates arrive
   as `edited_message`, so the `update.message` guard drops them rather than re-injecting
   a prompt every few seconds.
3. `feat(telegram): mark voice and location messages with a provenance prefix` -- adds
   `VOICE_PREFIX` and `LOCATION_PREFIX`, prepended to the injected text.
4. `feat(telegram): direct chat mode` -- `CCBOT_DIRECT_CHAT=true` lifts upstream's
   topic-only constraint for plain private chats: the private chat with the bot acts as
   one virtual topic with `thread_id` `DIRECT_CHAT_THREAD_ID` (0), bound to one window.
   `/unbind` then a new message switches sessions. Groups and topics keep working
   alongside. Off by default, so upstream behaviour is unchanged. Invariants:
   - `_get_thread_id` is the only switch; everything downstream routes on thread 0 as
     on any topic.
   - 0 must never reach Telegram as `message_thread_id`. Send sites use a truthy check
     (`if thread_id:`) rather than `is not None`; keep it that way in new send code.
   - `resolve_chat_id` must use the same truthy check: `set_group_chat_id` stores a
     group's no-topic chat_id under `"<user>:0"`, which would otherwise hijack the
     private chat's output into that group.
   - The topic-existence probe in `status_polling` skips thread 0.
5. `fix(config): load .env from CCBOT_DIR only` -- upstream loads a `.env` from the cwd
   with priority over `$CCBOT_DIR/.env`. Mike starts ccbot from the vault, which the
   Claude session can write to, so a planted `.env` there would swap the bot token and
   `ALLOWED_USERS` on the next restart. The cwd `.env` is now ignored entirely. Keep it
   that way when merging upstream changes to `config.py`.
6. `feat(security): fail-closed access gate` -- upstream authorizes with a separate
   `if` at the top of each handler, so one missing check exposes the session.
   `handlers/access_gate.py` adds a `TypeHandler` in group `-1` (`ACCESS_GATE_GROUP`)
   that raises `ApplicationHandlerStop` unless the sender is in `ALLOWED_USERS` and the
   chat is the sender's own private chat or a group (channels and anything else are
   rejected). Rejections are silent. Upstream's per-handler checks stay
   as a second layer. Invariants:
   - Every other handler must stay in a group above `-1`. `test_access_gate.py` asserts
     this, and runs every update kind from disallowed senders through the real
     `create_bot()` app with all handler callbacks mocked. Its positive control fails if
     an update kind stops matching any handler, so the negative cases cannot pass
     vacuously. When adding a handler for a new update kind, add that kind to the test.

### Provenance prefixes: what the model actually sees
Easy to get wrong, because the Telegram UI is misleading here. Each handler produces
**two different strings**:
- `send_to_window(...)` -- what Claude Code receives, via tmux keystrokes.
- `safe_reply(...)` -- the 🎤 / 📍 / 📷 confirmation rendered in the Telegram topic.

The emoji live only in the echo and **never reach the model**. Because injection is
plain keystrokes, dictated text and dropped pins would otherwise be indistinguishable
from typed input. Hence the prefixes, which are applied to the injected string only:

| Input | Injected into the session | Shown in Telegram |
|---|---|---|
| Voice | `User's transcripted voice message: <text>` | `🎤 "<text>"` |
| Location | `User shared his location: <lat>,<lng>` | `📍 <lat>,<lng>` |
| Venue | `User shared his location: <title>, <address>\n<lat>,<lng>` | `📍 <title>, <address>\n<lat>,<lng>` |
| Photo | `(image attached: <path>)` | `📷 Image sent to Claude Code.` |

Photos are self-identifying via the path, so they get no prefix. If you change a prefix
string, update any consuming CLAUDE.md rules that match on it -- they are matched as
literal text by the downstream session, and a silent rename breaks them without error.

### Branch layout and maintenance
- `main` tracks upstream and carries no local changes. Keep it that way.
- `mike` carries all fork work and is the branch actually installed.
- To take upstream changes: update `main` from upstream, then **merge** it into `mike`.
  `mike` is a long-lived, divergent, deployed branch, so it is not rebased -- rewriting
  it would mean force-pushing the installed branch and replaying every fork commit on
  each sync.
- Keep upstream sync cheap: make the changes a feature needs, but avoid gratuitous
  churn in upstream code -- no broad refactors, renames, reformatting or file moves
  unless the feature actually requires them. Prefer adding new code (new modules,
  handlers, config options) over rewriting upstream's, when both are reasonable.
- Upstreaming is optional. When a change is generally useful, it can be offered as a PR
  from a branch cut off `main`. If upstream merges an equivalent, prefer upstream's
  version on the next sync and drop the fork's copy.

### Before committing here
Upstream's bar applies (see Common Commands below): `ruff format` clean and `pyright`
at 0 errors. Note that `ruff check` reports a large number of **pre-existing** findings
on untouched upstream code, so it does not pass cleanly and cannot be used as a gate --
compare findings before and after a change instead of reading the absolute count.

ccmux — Telegram bot that bridges Telegram Forum topics to Claude Code sessions via tmux windows. Each topic is bound to one tmux window running one Claude Code instance.

Tech stack: Python, python-telegram-bot, tmux, uv.

## Common Commands

```bash
uv run ruff check src/ tests/         # Lint — MUST pass before committing
uv run ruff format src/ tests/        # Format — auto-fix, then verify with --check
uv run pyright src/ccbot/             # Type check — MUST be 0 errors before committing
./scripts/restart.sh                  # Restart the ccbot service after code changes
ccbot hook --install                  # Auto-install Claude Code SessionStart hook
```

## Core Design Constraints

- **1 Topic = 1 Window = 1 Session** — all internal routing keyed by tmux window ID (`@0`, `@12`), not window name. Window names kept as display names. Same directory can have multiple windows.
- **Topic-only** — no backward-compat for non-topic mode. No `active_sessions`, no `/list`, no General topic routing.
- **No message truncation** at parse layer — splitting only at send layer (`split_message`, 4096 char limit).
- **MarkdownV2 only** — use `safe_reply`/`safe_edit`/`safe_send` helpers (auto fallback to plain text). Internal queue/UI code calls bot API directly with its own fallback.
- **Hook-based session tracking** — `SessionStart` hook writes `session_map.json`; monitor polls it to detect session changes.
- **Message queue per user** — FIFO ordering, message merging (3800 char limit), tool_use/tool_result pairing.
- **Rate limiting** — `AIORateLimiter(max_retries=5)` on the Application (30/s global). On restart, the global bucket is pre-filled to avoid burst against Telegram's server-side counter.

## Code Conventions

- Every `.py` file starts with a module-level docstring: purpose clear within 10 lines, one-sentence summary first line, then core responsibilities and key components.
- Telegram interaction: prefer inline keyboards over reply keyboards; use `edit_message_text` for in-place updates; keep callback data under 64 bytes; use `answer_callback_query` for instant feedback.

## Configuration

- Config directory: `~/.ccbot/` by default, override with `CCBOT_DIR` env var.
- `.env` loading priority: local `.env` > config dir `.env`.
- State files: `state.json` (thread bindings), `session_map.json` (hook-generated), `monitor_state.json` (byte offsets).

## Hook Configuration

Auto-install: `ccbot hook --install`

Or manually in `~/.claude/settings.json`:
```json
{
  "hooks": {
    "SessionStart": [
      {
        "hooks": [{ "type": "command", "command": "ccbot hook", "timeout": 5 }]
      }
    ]
  }
}
```

## Architecture Details

See @.claude/rules/architecture.md for full system diagram and module inventory.
See @.claude/rules/topic-architecture.md for topic→window→session mapping details.
See @.claude/rules/message-handling.md for message queue, merging, and rate limiting.
