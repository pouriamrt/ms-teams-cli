---
name: teams-cli
description: Use this skill when the user asks about their Microsoft Teams chats — reading recent DMs, sending a message, replying to a chat, reacting to a message, marking a chat read or unread, searching across chats, or summarizing a conversation. Wraps the local 'teams' CLI (must be authenticated via 'teams login'; can auto-share credentials with outlook-cli). Triggers on phrases like "check my Teams messages", "DM Alice on Teams", "what did Bob send me on Teams", "react to that message", "mark that chat as read", "mark this unread so I see it again later", "search my Teams for the Q3 thread", "summarize this chat".
---

# teams-cli skill

You are wrapping the local `teams` command-line tool. The CLI talks to Microsoft Graph and the legacy Teams chatsvc/trouter API on behalf of the signed-in user, choosing the right backend per operation: chat **list/read** use chatsvc (the same API Teams web uses for chats), while **send/reply/react/search** use Graph. This split exists because many corporate tenants do not preauthorize the Teams Web client for Graph `Chat.Read*` delegated scopes. The CLI is the **only** way you should access their Teams chats — never import `teams_cli` Python modules, never make raw Graph/chatsvc HTTP calls, never browse the web for their messages.

## Iron rules

1. **Always shell out.** Use `Bash` to run `teams ...`. Never `import teams_cli`.
2. **Always pass `--json`** when reading data (it goes before the subcommand: `teams --json chat list ...`). Parse the JSON to extract what the user asked for. Render a concise summary to the user — do not dump raw JSON unless they ask for it.
3. **Read-only is automatic, writes require confirmation.** See tables below. For ANY state-changing command (`chat send`, `chat reply`, `chat react`), draft the exact command + body, show it to the user, call `AskUserQuestion`, and run only on explicit approval.
4. **Indices are session-scoped and two-tier.** The CLI assigns short integers `1, 2, 3, ...` to the items in the last list/read. **Chat indices** come from `chat list` / `chat search`. **Message indices** come from `chat read`. The two caches are independent — `chat react 3` always means the 3rd message in the chat you just read, never the 3rd chat.
5. **Never run `teams login` yourself.** It requires an interactive browser + bookmarklet click. If `whoami` returns 77, instruct the user to log in and STOP.
6. **Never invent flags or commands.** Stick strictly to what's documented here. If you need a flag you don't see, run `teams <cmd> --help` first.
7. **Prefer Teams for short conversational asks; defer to outlook-cli for anything with subject lines, CCs, or attachments.** When ambiguous, ask which channel.

## Pre-flight (run before any other command)

```bash
teams whoami --json
```

| Exit | What it means | What you do |
|---|---|---|
| `0`  | Logged in. JSON has `username`, `tenant_id`, `client_id`, `user_aad_id`, `shared_from`, `name`. | Proceed. |
| `77` | Session expired or never logged in. | Stop. Tell the user: *"Your Teams session has expired. Please run `teams login` in your terminal and then ask me again. (If you have `outlook-cli` already logged in, `teams login` will detect those credentials and offer to share them — one prompt, no bookmarklet needed.)"* |
| other | Unexpected. | Surface stderr to the user. Do not retry blindly. |

## Read-only commands (use freely, no confirmation)

`--json` flag goes **before** the subcommand. Read commands that produce listings populate the appropriate index cache (`chat list` / `chat search` → chat indices; `chat read` → message indices).

### Chat (read)

| Intent | Command |
|---|---|
| List recent chats (default 25) | `teams --json chat list` |
| Only unread | `teams --json chat list --unread` |
| With exact unread counts (costly: extra Graph call per unread chat) | `teams --json chat list --unread --with-counts` |
| Filter by type | `teams --json chat list --type oneonone\|group\|meeting` |
| Filter by recency | `teams --json chat list --since "2d"` |
| Page size + skip | `teams --json chat list --top N --skip N` |
| Follow all pages | `teams --json chat list --all` |
| Read messages in a chat by index | `teams --json chat read <chat-index>` |
| Read by raw chat-id | `teams --json chat read 19:abc@unq.gbl.spaces` |
| Read by email (1:1 only) | `teams --json chat read alice@example.com` |
| Limit & paginate | `teams --json chat read <ref> --top N --skip N --all` |
| Only messages since | `teams --json chat read <ref> --since "1h"` |
| Don't convert HTML → markdown | `teams --json chat read <ref> --raw` |
| Search across all chats | `teams --json chat search "lgtm"` |
| Search within one chat | `teams --json chat search "lgtm" --in <chat-index>` |
| Search with paging | `teams --json chat search "lgtm" --top N --all` |

**`--since` accepts**: `"1h"`, `"30m"`, `"2d"`, `"1w"`, `"yesterday"`, ISO datetime, or natural language.

**`--top` defaults to 25** on both `chat list` and `chat read`. `--all` follows `@odata.nextLink` to the end (use sparingly on busy accounts — can return hundreds of chats / thousands of messages).

**`--with-counts` is opt-in**. Without it, `unread_count` in the JSON is `null` and `has_unread` is a cheap boolean. Only use `--with-counts` when the user explicitly asks for *exact* counts (e.g., "how many unread from each person?"). Otherwise stick with `has_unread`.

**Chat label fallbacks.** In the table output, a chat with no topic and no resolvable member name shows as `(self)`. This happens for two cases: (a) the user's actual chat-with-self, and (b) 1:1 chats where the most recent message was sent by the signed-in user (chatsvc list view returns no members and we can't infer the other party from a from-me message). When you need to identify the other party in case (b), `chat read <index>` will show older messages from them.

**Chat addressing** accepts three forms anywhere a `<chat>` argument is expected:
- **Short index** like `3` — from the most recent `chat list` / `chat search`
- **Raw chat ID** like `19:xxx@unq.gbl.spaces` (1:1), `19:xxx@thread.v2` (group/meeting)
- **Email** like `alice@example.com` — resolves to the 1:1 chat (read-only — for `chat read` only; `chat send` *creates* the chat if missing)

### Meta

| Intent | Command |
|---|---|
| Current version + API target | `teams version` |
| List all schema names | `teams --json-schema --list` |
| JSON Schema for one command | `teams --json-schema chat.list` (also: `chat.read`, `chat.send`, `chat.reply`, `chat.react`, `chat.search`) |

## State-changing commands (CONFIRM with `AskUserQuestion` first)

For each of these, your workflow is:

1. **Draft.** Compose the exact command, including the body if applicable. For multi-line bodies use `--body @-` and pipe via stdin.
2. **Show.** Print the full command and the body to the user in fenced code blocks. Show the recipient label clearly (name + email, or chat topic + member names for group).
3. **Confirm.** Call `AskUserQuestion` with the draft. Recommended options: `"Send it"`, `"Edit body first"`, `"Cancel"`.
4. **Execute.** Only on explicit "Send it" approval, run the command.
5. **Report.** Show the CLI's confirmation (the returned `message_id` / `via` / `ok` from the JSON) to the user.

### Chat (write)

| Intent | Command |
|---|---|
| Send a new message to a chat by index | `teams --json chat send <chat-index> --body @-` (stdin) |
| Send to a person by email (creates 1:1 if missing) | `teams --json chat send alice@example.com --body @-` |
| Send to a raw chat-id | `teams --json chat send 19:abc@unq.gbl.spaces --body @-` |
| Send as HTML | add `--html` (e.g. for `<b>bold</b>` markup) |
| Set importance | `--importance normal\|high\|urgent` (default: `normal`) |
| Send with quote of a prior message | `--reply-to <message-index>` (uses the message-index cache from the last `chat read`) |
| Reply to a message in the last-read chat | `teams --json chat reply <message-index> --body @-` |
| React to a message | `teams --json chat react <message-index> like\|heart\|laugh\|surprised\|sad\|angry` |
| Remove a reaction | `teams --json chat react <message-index> like --unreact` |
| Mark a chat as read | `teams --json chat mark-read <chat-ref>` |
| Mark a chat as unread (whole chat) | `teams --json chat mark-unread <chat-ref>` |
| Mark unread from a cutoff timestamp | `teams --json chat mark-unread <chat-ref> --since "1h"` |

**`--body @-` reads the body from stdin** — that's how you pipe a multi-line draft. If you omit `--body` entirely on `chat send` / `chat reply`, the CLI opens `$EDITOR`, which will hang in a non-interactive session — **always pass `--body @-` with stdin**, or pass `--body "text"` for one-liners.

**Reactions are fixed-vocabulary.** Only these 6 are supported by Graph: `like`, `heart`, `laugh`, `surprised`, `sad`, `angry`. Anything else (e.g. `rocket`, custom emoji) returns exit 2 with the valid list. Don't try clever fallbacks — surface the constraint to the user.

**`chat mark-read` / `chat mark-unread`** mutate the per-user read cursor. They affect ONLY the signed-in user — other chat members see nothing. `<chat-ref>` accepts the same three forms as `chat send`: chat index, raw chat-id, or email (email creates a 1:1 if missing). `mark-unread --since <when>` pushes the cursor back to that instant so anything more recent re-appears as unread; omit `--since` to mark the whole chat unread. `--since` accepts the same vocabulary as `chat read --since` (`"1h"`, `"yesterday"`, ISO 8601, etc.). These are state-changing — follow the confirm-before-execute workflow above.

**Backend selection is automatic** — the CLI tries Graph's `markChatRead*ForUser` first and falls back on 403 to the chatsvc `consumptionHorizonBookmark` property (the unread-marker Teams web itself uses: a nonzero cursor marks the chat unread, `0` clears it / marks read). The returned JSON's `via` field reports which path succeeded. On the chatsvc path the CLI reads the marker back to confirm the change actually took effect; if the tenant silently drops the write, `verified` is false, `ok` is false, and the command exits non-zero. Don't surface `via` to the user unless they're debugging.

**`chat reply` vs `chat send --reply-to`:**
- `chat reply <message-index>` is the convenience form. It uses the *last-read chat* as context. Use this when you're already in a "read → reply" flow.
- `chat send <chat-ref> --reply-to <message-index>` is the explicit form. The `<chat-ref>` can be any chat (different from the one you last read). The `<message-index>` still comes from the last `chat read`.
- Both produce the same wire result: a new message in the target chat containing a `<blockquote>` of the referenced message.

**There is no separate "thread" — Teams DMs/group chats are flat.** Reply quoting is purely cosmetic. Don't try to address "the third reply in thread N" — that's channel-post semantics, which v1 does not support.

## Indices in depth

The CLI keeps **two** short-index caches:

- **Chat indices** at `~/.cache/teams-cli/last_chat_listing.json` — populated by `chat list` and `chat search`. Used by `chat read N`, `chat send N`, `chat search --in N`.
- **Message indices** at `~/.cache/teams-cli/last_message_listing.json` — populated by `chat read`. Used by `chat react N`, `chat reply N`, `chat send --reply-to N`.

**The two caches are independent.** `chat react 3` always means message index 3 in the chat you most recently *read*, never chat index 3. If the user says "react to chat 3", you must `chat read 3` first to populate the message cache, then ask which message in that chat to react to.

**Cache freshness.** Each list/read fully replaces the prior cache for that family. If you've been chatting with the user for a while and you're not sure the cache is fresh, just re-list — it's cheap.

**The long Graph ID always works** as a substitute for any index — the JSON output of any list/read includes the `id` (chat ID) and `message_id`. Pass those directly for an unambiguous reference when you don't trust the cache.

**`chat read` displays messages oldest-first**, but Graph returns newest-first internally. The displayed indices reflect oldest-first order: index 1 is the oldest visible message, index N is the newest. So `chat react N` reacts to the most recent message in the chat.

## JSON contract

The `--json` output has a stable shape across versions. Top-level keys are frozen; new fields may be added but never renamed or removed.

```jsonc
// teams --json chat list
{
  "items": [
    {
      "id": "19:3e0f...._ab06770f....@unq.gbl.spaces",
      "index": 1,
      "topic": null,                       // null for 1:1; chat name for group
      "chat_type": "oneOnOne",             // "oneOnOne" | "group" | "meeting"
      "members": [],                       // empty in `chat list` — chatsvc list view doesn't include members. Use `last_message.from` to identify the other party in 1:1s.
      "last_message": {
        "from": { "name": "Alice Smith", "email": "alice@example.com", "user_id": "ab06...", "from_me": false },
        "preview": "lgtm, merging now",
        "created_at": "2026-05-22T14:03:00Z",
        "message_id": "1716393780123"
      },
      "has_unread": true,
      "unread_count": null,                // null unless --with-counts was passed
      "is_muted": false,
      "last_updated": "2026-05-22T14:03:00Z"
    }
  ],
  "next_skip": null                        // integer if more pages available
}

// teams --json chat read <chat>
{
  "chat_id": "19:abc@unq.gbl.spaces",
  "items": [
    {
      "id": "1716393720000",
      "index": 1,
      "chat_id": "19:abc@unq.gbl.spaces",
      "from": { "name": "...", "email": "...", "user_id": "...", "from_me": false },
      "created_at": "2026-05-22T14:02:00Z",
      "body": "ok if I merge?",
      "body_format": "text",               // "text" | "html"
      "importance": "normal",              // "normal" | "high" | "urgent"
      "reactions": [
        {
          "reaction_type": "like",
          "user": { "name": "...", "email": "...", "from_me": true },
          "created_at": "2026-05-22T14:03:00Z"
        }
      ],
      "is_deleted": false,
      "reply_to_id": null
    }
  ],
  "next_skip": null
}

// teams --json chat send / reply
{
  "chat_id": "19:abc@unq.gbl.spaces",
  "message_id": "1716394000000",
  "reply_to_id": "1716393720000",          // only on reply / send --reply-to
  "created_at": "2026-05-22T14:10:00Z"
}

// teams --json chat react
{
  "ok": true,
  "reaction": "like",
  "via": "graph",                          // "graph" or "chatsvc" — both equally valid
  "unreact": false
}

// teams --json chat mark-read
{
  "ok": true,                              // == verified
  "chat_id": "19:abc@unq.gbl.spaces",
  "state": "read",
  "via": "graph",                          // "graph" or "chatsvc" — both equally valid
  "verified": true,                        // chatsvc path reads the cursor back to confirm
  "error": null                            // string describing the failure when verified=false
}

// teams --json chat mark-unread
{
  "ok": true,
  "chat_id": "19:abc@unq.gbl.spaces",
  "state": "unread",
  "since": "2026-05-20T14:00:00Z",         // null when --since was omitted
  "via": "graph",                          // "graph" or "chatsvc"
  "verified": true,
  "error": null
}
// When via=="chatsvc" and the tenant silently drops the write, `verified` and
// `ok` are false, `error` carries the reason, and the command exits non-zero.

// teams --json chat search
{
  "items": [
    {
      "message_id": "1716393780123",
      "chat_id": "19:abc@unq.gbl.spaces",
      "from": { "user_id": "...", "name": "Alice Smith", "email": "", "from_me": false },
      "preview": "lgtm, merging now",
      "created_at": "2026-05-22T14:03:00+00:00",
      "score": 5.6
    }
  ],
  "total_estimated": 2                     // approximate; from Microsoft Search
}
```

For any other contract details: `teams --json-schema chat.<command>` returns the canonical JSON Schema.

## Exit codes

| Code | Meaning | Your reaction |
|---|---|---|
| `0` | Success | Proceed. |
| `1` | Generic error (stderr has detail) | Surface the stderr line to the user. |
| `2` | Usage error (bad flags, unsupported reaction emoji) | You made a flag mistake — re-check `teams <cmd> --help` and the supported values in this skill. |
| `64` | Not found (bad index, bad email, missing chat-id) | The index/email is stale or wrong; re-run the appropriate list / re-read, then retry. |
| `77` | Session expired | Stop. Tell user to run `teams login`. |

## Worked examples

### 1. "Summarize my Teams DMs"

```bash
teams --json chat list --unread --top 20
```

For each item where `has_unread == true`:
```bash
teams --json chat read <index> --top 20
```

Then produce a per-chat summary (group by sender or by chat topic). Mention message content, time, and who said what. **Do not** call `react`, `reply`, or `send` as a side effect.

If the user then asks "how many unread from each person?", re-run with counts:
```bash
teams --json chat list --unread --with-counts
```

### 2. "Did Bob ping me?"

```bash
teams --json chat list --unread --top 25
```

Filter client-side for items where `last_message.from.name` contains "Bob" AND `last_message.from.from_me == false` AND `has_unread == true`. Show the matching chat's last message preview. Offer to read the full chat.

(Note: `members[]` is empty in `chat list` output because the chatsvc list view doesn't return it. Match on `last_message.from.name` instead of `members[].name`. If "Bob" sent the latest message in a 1:1 with you, the `topic` will also be Bob's name as rendered in the table.)

### 3. "DM Alice that I'll have the PR ready by Friday"

Draft body:
```
I'll have the PR ready by Friday.
```

Show the user:
```bash
teams --json chat send alice@example.com --body @-
# (with the body above piped via stdin)
```

`AskUserQuestion` with options `"Send it"`, `"Edit body first"`, `"Cancel"`.

On "Send it":
```bash
printf '%s\n' "I'll have the PR ready by Friday." | teams --json chat send alice@example.com --body @-
```

Parse the response: `{chat_id, message_id, created_at}`. Tell the user the message was sent and (optionally) that the 1:1 chat was created if it didn't already exist.

### 4. "React 👍 to Bob's last message"

```bash
teams --json chat list --top 25
```

Find the chat where `last_message.from.name` contains "Bob" and `last_message.from.from_me == false`. Let that chat's `index` be `i`.

```bash
teams --json chat read i --top 1
```

The newest message (Bob's) will be at message-index `1`. Show the user the message preview and ask:

> `AskUserQuestion`: "React with 👍 to: '<preview>'?" — options: ["React", "Cancel"]

On "React":
```bash
teams --json chat react 1 like
```

Confirm via the returned `{ok: true, via: "graph"}`.

### 5. "What did the team say about Q3 planning?"

```bash
teams --json chat search "Q3 planning" --top 25
```

For each hit, the JSON contains `chat_id`, `message_id`, `preview`, `created_at`, `from.name`. Group hits by `chat_id` and present a chronological synthesis to the user. If they want deeper context on one chat:

```bash
teams --json chat list                              # to assign chat indices
teams --json chat read <matched-chat-index> --since "<matched.created_at minus 1h>"
```

If the search returns empty and the user expected results: surface that "Microsoft Search index can lag chat writes by 5–30 seconds — try again in a moment." (This is in the CLI's error path; surface it verbatim if exit code is 0 with empty items.)

### 6. "Reply to that last message"

If you've just done a `chat read` (cache is fresh):
```bash
teams --json chat reply <last-message-index> --body @-
```

If the cache is stale (you don't know when the last `chat read` ran): re-read the chat first to refresh the message-index cache. Then draft + confirm + reply.

Always show the user the message you're replying to, in a fenced block, so they can verify you've selected the right one before approving.

### 7. "Find every chat where someone mentioned the deploy and react 👍"

This is a **multi-step state change** — confirm the batch plan first, not each individual reaction.

```bash
teams --json chat search "deploy" --all
```

Show the user: "Found N hits across M chats. I'd like to react 👍 to each. Confirm?" Use `AskUserQuestion`. On approval, for each hit you'll need to load that chat's message indices, find the matching message, and react:

```bash
teams --json chat list                              # to map chat_id → chat-index
# For each matched hit:
teams --json chat read <chat-index> --top 50        # populate message-index cache
# Identify the message-index matching hit.message_id, then:
teams --json chat react <message-index> like
```

This is genuinely loop-y — only do it when the user has authorized the batch. **Never react silently as a side effect of summarizing.**

### 8. "I read those in another client — clear the badges"

```bash
teams --json chat list --unread --top 25
```

For each unread chat the user actually wants cleared, draft the mark-read commands and confirm as a batch (don't fire them silently):

> `AskUserQuestion`: "Mark these N chats as read: [list]?" — options: ["Mark all read", "Pick one by one", "Cancel"]

On "Mark all read":
```bash
teams --json chat mark-read <chat-index-1>
teams --json chat mark-read <chat-index-2>
# ...
```

The inverse ("I want to come back to that chat later") uses `mark-unread`:
```bash
teams --json chat mark-unread <chat-index>                            # whole chat reappears as unread
teams --json chat mark-unread <chat-index> --since "1h"               # only the last hour reappears
```

Both commands only affect the signed-in user's read cursor — other chat members see nothing.

### 9. "What's the JSON shape of chat list?"

```bash
teams --json-schema chat.list
```

Show the schema. Don't fetch real chats just to demo.

## Notify me when I get a new Teams message (polling watcher)

The CLI has **no native push or watch** — there is no server callback. "Notify me on new messages" is implemented as a **Claude-Code-orchestrated polling loop** on top of `chat list --unread`: poll on an interval, diff each result against a persisted set of already-seen `message_id`s, and fire a notification only on genuinely new arrivals. This is the *only* supported way to get notifications — never promise real-time delivery.

### Delivery mechanism

Use the **`PushNotification` tool** to alert the user — it raises a desktop notification in their terminal and, if Remote Control is connected, pushes to their phone. Keep the body to one line, under 200 chars, leading with sender + gist (e.g. `Teams: Alice in "Deploy" — "can you review?"`). Stay silent on routine "nothing new" ticks; a notification the user didn't need is costly.

### Two ways to run the poll

- **`/loop` (session-bound, simplest).** `/loop 5m <watcher prompt>` schedules a recurring in-session job (CronCreate `*/5 * * * *`). Runs only while this Claude Code session is open; auto-expires after 7 days; costs tokens per tick. Best for ad-hoc "watch while I work."
- **OS scheduler (always-on).** A Windows Task Scheduler task (or cron on macOS/Linux) running a standalone script that does the same poll → diff → toast. Survives reboots, no token cost — but Claude only *authors* it, it isn't "Claude live." Best when it must outlive the session.

Ask the user which lifetime they want before building.

### State file

Persist seen IDs **on disk, not in conversation context** — the loop must survive context compaction and reuse state across ticks:

```
~/.cache/teams-notify/seen.json   →  { "seen_message_ids": ["1779883569530", ...] }
```

Before the first tick, **snapshot the current unread set into `seen.json`** so tick 1 doesn't fire on the backlog the user has already seen.

### Tick algorithm

1. Run `teams --json chat list --unread --top 50`, writing the JSON to a path **both Git-Bash and Windows-Python agree on** — use the cache dir, *not* `/tmp` (Git-Bash `/tmp` ≠ Windows-Python `/tmp`, a silent FileNotFoundError trap). If exit code is `77`: send `PushNotification` "Teams session expired — run `teams login`" and **stop the loop** (do not reschedule).
2. Load `seen_message_ids` from `seen.json`.
3. New = unread items where `last_message.message_id` ∉ seen **and** `last_message.from.from_me == false`. Skip pure `URIObject` / `CallRecording` meeting auto-posts unless that's the only new thing.
4. If any new: send **one** `PushNotification` summarizing sender name(s) + cleaned preview (strip HTML tags), and print a one-line summary to chat. Else stay silent.
5. Rewrite `seen.json` as the union of prior seen ∪ all current unread `message_id`s.

**Read-only guarantee:** the watcher must never `send`, `reply`, `react`, `mark-read`, or `mark-unread`. Marking read as a side effect would move the user's unread cursor and destroy the very signal it polls.

### Reference diff script

```python
import json, os, re

base = os.path.join(os.path.expanduser("~"), ".cache/teams-notify")
cur = json.load(open(os.path.join(base, "unread.json")))["items"]  # CLI output
seenp = os.path.join(base, "seen.json")
seen = set(json.load(open(seenp))["seen_message_ids"])

new, all_ids = [], []
for it in cur:
    lm = it.get("last_message") or {}
    mid = str(lm.get("message_id") or "")
    if mid:
        all_ids.append(mid)
    frm = lm.get("from") or {}
    if mid and mid not in seen and not frm.get("from_me"):
        prev = lm.get("preview") or ""
        is_rec = "URIObject" in prev and "CallRecording" in prev
        new.append(
            {
                "topic": it.get("topic"),
                "from": frm.get("name"),
                "preview": re.sub(r"<[^>]+>", " ", prev).strip()[:120],
                "is_recording": is_rec,
                "mid": mid,
            }
        )

json.dump({"seen_message_ids": sorted(seen.union(all_ids))}, open(seenp, "w"), indent=2)
real = [n for n in new if not n["is_recording"]]
print("NEW_TOTAL", len(new), "NEW_REAL", len(real))
print(json.dumps(new, indent=2))
```

### Caveats to surface to the user

- **Session-bound** (`/loop` variant): closing Claude Code stops it. Always-on needs the OS-scheduler variant.
- **Polling lag**: alerts arrive up to one interval late (cron fires only while the REPL is idle).
- **Auth expiry**: when the captured token dies, polls return exit `77` with no messages — surface that explicitly, else silence reads as "no messages" when it's really "watcher dead."
- **Cost**: every tick spends tokens (`/loop` variant) plus one chatsvc list call.
- Stop via `CronDelete <job-id>` or by telling the agent to stop the loop.

## Error recovery

| Symptom | Fix |
|---|---|
| Exit `77` | Stop. *"Your Teams session has expired. Please run `teams login` in your terminal."* If outlook-cli is logged in, mention the auto-share offer. |
| Exit `64` after `chat read 3` | Re-run `chat list`, then re-resolve `3`. |
| Exit `64` after `chat react 3` | Re-run `chat read <chat>`, then re-resolve `3`. The message-index cache is per-chat — opening a different chat invalidates it. |
| Exit `64` with "User alice@xyz not found in directory" | The email doesn't resolve in your tenant's directory. Tell the user verbatim; ask for the correct address. |
| Exit `1` with "Cannot start chat with X — tenant federation policy" | Tenant blocks external/B2B chat. Surface verbatim. Do NOT retry with chatsvc fallback (that's intentionally not wired for chat creation in v1). |
| Exit `1` with "Chat-write blocked by tenant policy" | Conditional Access blocks the write. Surface verbatim. |
| Exit `1` with "Reaction failed via both paths" | Both Graph and chatsvc rejected the reaction. Surface the inner status codes verbatim. |
| Exit `2` on `chat react` with an emoji | Only `like|heart|laugh|surprised|sad|angry` are supported by Graph's reactionType. Suggest one of those. |
| Exit `1` with TLS / "certificate verify failed" | The user's machine doesn't trust the corporate proxy CA. Suggest `TEAMS_CLI_CA_BUNDLE=/path/to/bundle.pem teams chat list` as the diagnostic step. |
| Exit `1` with "AADSTS9002327" | The captured RT was minted at a different Teams origin. Suggest `TEAMS_CLI_ORIGIN=https://teams.cloud.microsoft teams chat list`. |
| `chat send` hangs | You forgot `--body @-` and the CLI is waiting on `$EDITOR`. Cancel, retry with `--body @-` and stdin. |
| Empty results from `chat search` for a very recent message | Microsoft Search index lag (5–30s typical). Wait and retry. |

## What I will NOT do

- **Send messages** without explicit user approval via `AskUserQuestion`.
- **React or unreact** without explicit user approval. Even a 👍 changes server state and is visible to other chat participants.
- **Reply** without explicit user approval, even when the original message is from the user themselves.
- **Mark anything read or unread** as a side effect of summarizing or browsing. `chat mark-read` and `chat mark-unread` are real commands now, but they mutate the user's read cursor and must go through the confirm-before-execute workflow like any other state change.
- **Send to multiple recipients at once** without showing the full recipient list and getting explicit batch approval.
- **Import `teams_cli` Python modules** — always shell out via `Bash`.
- **Run `teams login`** myself — requires interactive browser + bookmarklet click.
- **Invent flags** — if a flag isn't in this skill or in `--help`, it doesn't exist.
- **Fall back to chatsvc** manually — the CLI's `chat react` does this internally when Graph 4xx's; the choice is opaque and reported in the `via` field. Don't surface "via: chatsvc" to the user unless they're debugging.
- **Bypass the two-tier index convention.** If the user says "react to message 3", confirm which chat they were just looking at; if the message-index cache is stale, re-read first.
- **Promise behavior I can't verify** — if uncertain, run `--help` or check `--json-schema`.

## Cross-skill coordination with outlook-cli

The user has both `outlook-cli` and `teams-cli` installed. When the channel is ambiguous:

- **Prefer Teams** for: short conversational asks ("ping Alice", "DM Bob", "message the team", "quick word with Carla").
- **Prefer Outlook** for: anything with subject lines, CCs, attachments, formal tone, "send an email", "draft a note to".
- **If neither cue applies**: ask the user which channel.

If the user explicitly names a person and asks to "message them" with no further qualifier, default to Teams (it's the faster channel for short messages) but mention you can switch to email if preferred.

## Configuration & environment variables

The user has a config file at `~/.config/teams-cli/config.toml` (created on first run). Inspect it (read-only) via `teams config list` if it exists. Don't write to it without explicit instruction — it changes CLI defaults across all sessions.

If the user reports unexpected behavior, these env vars exist:

| Var | Effect |
|---|---|
| `TEAMS_CLI_HOME` | Override the home dir for credentials/cache lookups (default `$HOME`). Useful for multi-profile testing. |
| `TEAMS_CLI_ORIGIN` | Override the `Origin` header sent to the AAD token endpoint (default `https://teams.microsoft.com`). Switch to `https://teams.cloud.microsoft` for some tenants. |
| `TEAMS_CLI_CA_BUNDLE` | Path to corporate root CA bundle (for MITM proxies). |
| `TEAMS_CLI_INSECURE=1` | Disable TLS verification entirely. **Diagnostic only — never suggest as a fix.** |
| `OUTLOOK_CLI_HOME` | The CLI checks this location for outlook-cli's credentials.json when looking for a FOCI share candidate at login time. |

Do not set these for the user — only mention them if they ask about TLS, login, or endpoint issues.

## Things this skill does NOT cover (v1)

The CLI deliberately does not yet implement these — if the user asks, say so and offer the closest covered alternative:

- **Activity feed / mentions across all chats and channels** → for now, `chat search "@<your-name>"` is the closest workaround.
- **Presence get/set** (Available/Busy/DND/Away) → not supported in v1; user must use the Teams app for this.
- **Channels & teams operations** (post to channel, list teams, threaded replies) → not in v1 — Teams DMs/group chats only.
- **Meeting helpers** (today's meetings, join links) → use `outlook cal today` instead (Teams meetings appear in your calendar).
- **Native real-time watch / server push** → not supported (no callback API). To "notify me on new messages", use the client-side polling watcher in [Notify me when I get a new Teams message](#notify-me-when-i-get-a-new-teams-message-polling-watcher) — poll `chat list --unread` on an interval and diff against seen `message_id`s.
- **File operations** (upload/download files in chat) → most files in Teams chats are OneDrive/SharePoint links. The CLI doesn't fetch them.
- **Calls / video / screen share** → not meaningfully expressible from a CLI; use the Teams app.
- **Custom reactions / non-default emoji** → only the 6 Graph reactionType values work (`like|heart|laugh|surprised|sad|angry`).

If the user wants any of these, tell them it's deferred to a future version and suggest the closest covered alternative or the Teams web/desktop app.
