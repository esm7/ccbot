# CLAUDE.md

## Fork Notes (esm7/ccbot)
This is a fork of [six-ddc/ccbot](https://github.com/six-ddc/ccbot). Everything below
this section is upstream's own guidance, unmodified -- read it as authoritative for
the codebase itself. This section covers only what the fork adds and how to maintain it.

### Why this fork exists
The fork drives "Mike", a personal always-on Telegram bridge to Claude Code (a headless
Debian NUC running `ccbot` under `systemd --user`). Two behaviours were needed that
upstream does not provide. They were originally applied as hand edits directly inside
the installed virtualenv, which is machine-local and gitignored -- so every venv rebuild
silently reverted them and the bridge quietly lost features. The fork exists to make
those patches durable and reviewable instead of invisible.

### What the fork carries
Two additive commits on top of upstream, each self-contained and cherry-pickable:
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
- `mike` carries the two commits above and is the branch actually installed.
- To take upstream changes: update `main` from upstream, then **rebase** `mike` onto it.
  Do not merge -- rebasing keeps each patch a clean, PR-ready commit.
- Patches are deliberately **additive**. `location_handler` duplicates the
  session-resolution preamble of `photo_handler`/`voice_handler` instead of factoring it
  out, because a shared-helper refactor would touch untouched upstream handlers and
  enlarge the rebase conflict surface. Keep new patches equally additive.
- Both patches are candidates for upstreaming. If either is merged upstream, drop it from
  `mike` on the next rebase rather than carrying a duplicate.

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
