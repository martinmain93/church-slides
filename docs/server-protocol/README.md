# Church Slides server protocol

**Version 0, draft.** How the desktop app talks to a Church Slides server:
signing in, the account, and (in later versions) sync, the live set and AI
assistants.

The app is free and open source and never needs a server. A server adds
hosted features for an organization's team. The official one runs at
`https://api.churchslides.app`, but nothing here is specific to it: any server
that implements this document works with the app. That's deliberate. The
protocol builds on published standards wherever one fits, and this document
covers the rest.

| Section | Status |
| --- | --- |
| [Discovery](#discovery) | v0 |
| [Signing in](#signing-in) | v0 |
| [Errors](#errors) | v0 |
| [Account](#account) | v0 |
| [Organizations](#organizations) | v0 |
| [Band devices](#band-devices) | v0 |
| [Sync of organization libraries](#sync) | v0 draft |
| [Live set](#live-set) | v0 |
| AI assistants (MCP) | Not yet specified |

## Conventions

- HTTPS only, except `http://localhost` for development.
- JSON request and response bodies (`application/json`), UTF-8.
- Times are Unix milliseconds. Ids are opaque strings.
- Names are generic: *organization*, *member*, *library*, *setlist*,
  *service*.
- **Versioning.** Discovery reports the protocol version. A server may add
  fields, endpoints, capabilities and error codes in any version, so clients
  ignore what they don't recognise. Removing or changing the meaning of
  anything needs a new major version, and a server keeps serving the old one
  while released apps still use it.

## Discovery

The app is given a server address (the official one by default) and starts
by reading:

```
GET {server}/.well-known/church-slides
```

```json
{
  "protocol": "0",
  "api": "/v1",
  "capabilities": ["account", "organizations", "live", "sync", "ai", "mcp"],
  "auth": {
    "issuer": "https://api.churchslides.app",
    "resource": "https://api.churchslides.app/v1",
    "scopes": ["account", "offline_access"]
  }
}
```

| Field | Meaning |
| --- | --- |
| `protocol` | Protocol version this server speaks. |
| `api` | Path of the protected API, relative to the server. |
| `capabilities` | What this server offers. A server may offer any subset. Values so far: `account`, `organizations`, `live`, `sync`, `ai`, `mcp`. The app shows only features whose capability is listed. |
| `auth.issuer` | The OAuth authorization server. Its metadata is at `{issuer}/.well-known/oauth-authorization-server` ([RFC 8414]). |
| `auth.resource` | The resource indicator ([RFC 8707]) to request tokens for. |
| `auth.scopes` | Scopes the app should request. |

## Signing in

Signing in is **OAuth 2.1** (authorization code with PKCE). How a server
signs people in (Google, Apple, email, a pilot passcode) is its own
business; the app only sees the standard flow.

1. **Find the endpoints** from the authorization server metadata at
   `{auth.issuer}/.well-known/oauth-authorization-server`.
2. **Identify the app.** The desktop app's `client_id` is the URL of its
   [Client ID Metadata Document]:

   ```
   https://churchslides.app/oauth/desktop-client.json
   ```

   It's a public client (`token_endpoint_auth_method: none`), and its
   redirect URI is the loopback address `http://127.0.0.1/callback`. Per
   [RFC 8252] §7.3, the server accepts any port on it. A server that
   doesn't support these documents (its metadata lacks
   `client_id_metadata_document_supported: true`) can offer Dynamic Client
   Registration ([RFC 7591]) at `registration_endpoint`. The app then
   registers once per server with the same metadata and keeps the
   `client_id` it gets back.
3. **Authorize in the system browser**, never in an embedded view
   ([RFC 8252]). The app listens on a random free loopback port and opens:

   ```
   {authorization_endpoint}?response_type=code
     &client_id=…&redirect_uri=http://127.0.0.1:{port}/callback
     &code_challenge=…&code_challenge_method=S256
     &state=…&scope=account%20offline_access
     &resource={auth.resource}
   ```

   To sign in through an invite (see [Joining](#joining)), the app adds
   `&invite={link or code}`. A server may then let a new person in without
   whatever else it asks of newcomers, and adds them to the organization as
   they sign in. Servers that don't support invites ignore it.

   The server redirects back to the loopback address with `code`, `state`
   and `iss` ([RFC 9207]). The app checks that `state` matches and that
   `iss` equals `auth.issuer`.
4. **Exchange the code** at `token_endpoint`, with `code_verifier` and
   `resource`. The response has a short-lived `access_token` (minutes) and a
   `refresh_token`.
5. **Call the API** with `Authorization: Bearer {access_token}`. A `401`
   means the access token expired: refresh and retry once.
6. **Refresh** with `grant_type=refresh_token`. Refresh tokens rotate:
   always store the new one. If refreshing fails with `invalid_grant`, the
   device was signed out (or signed out everywhere). Show the person as
   signed out, and keep everything local working.
7. **Sign out** this device by revoking its refresh token at the
   `revocation_endpoint` ([RFC 7009]), then forget the tokens.

**Storage.** The app keeps the refresh token in the operating system's
credential store (macOS Keychain, Windows Credential Manager), never in its
database or settings files. Access tokens stay in memory.

**Offline.** None of this ever blocks presenting. Signing in needs the
network; everything else carries on without it.

## Errors

Every error from the API (not the OAuth endpoints, which use OAuth's own
error format) has one shape:

```json
{ "error": { "code": "not_on_plan", "message": "A sentence a person can read." } }
```

| Code | HTTP | Meaning |
| --- | --- | --- |
| `bad_request` | 400 | The request is malformed. |
| `unauthorized` | 401 | No valid access token. Also sent with `WWW-Authenticate` ([RFC 6750], [RFC 9728]). |
| `forbidden` | 403 | Signed in, but this member's role doesn't allow it. |
| `not_found` | 404 | No such route or object. |
| `not_on_plan` | 402 | The organization's plan doesn't include this. The app says so plainly; it never hides or fakes the feature. |
| `rate_limited` | 429 | Too many requests; try again later. |
| `internal` | 500 | The server failed. An `id` field may be included for support. |

Codes are stable. New ones may be added; clients treat an unknown code by
its HTTP status.

## Account

All under `{server}{api}`, with a bearer token that has the `account` scope.

### `GET /me`

The signed-in person and their organizations.

```json
{
  "user": { "id": "…", "email": "ada@example.org", "displayName": "Ada" },
  "memberships": [{ "orgId": "…", "orgName": "Grace Chapel", "role": "operator" }]
}
```

Roles: `owner`, `admin`, `set_leader`, `operator`, `member`.

### `GET /me/sessions`

The devices and apps signed in as this person.

```json
{ "sessions": [{ "id": "…", "app": "Church Slides", "createdAt": 1700000000000, "expiresAt": 1705000000000 }] }
```

### `DELETE /me/sessions/{id}`

Signs one of them out. `204`.

### `POST /me/sign-out-everywhere`

Signs every device and app out, this one included. Their refresh tokens
stop working, and access tokens stop being accepted.

```json
{ "signedOut": 3 }
```

## Organizations

A team (a church, a school, a band) is an **organization**. Its content
belongs to it and is visible only to its members. Everything below is under
`{server}{api}` with an `account` token.

### Roles

| Role | What it adds |
| --- | --- |
| `member` | Sees the organization, its members and their roles |
| `set_leader` | (Later: edits setlists and leads the live set) |
| `operator` | Adds and removes band devices. (Later: edits the library and runs the live set) |
| `admin` | Renames the organization; invites people; changes roles below owner; removes members; sees members' emails |
| `owner` | Exactly one per organization. Hands ownership to another member; deletes the organization |

A server checks the role on every request. Someone who isn't a member gets
`404 not_found` for anything under an organization, so an organization's
existence isn't revealed. A member whose role doesn't allow it gets
`403 forbidden`.

### `POST /orgs`

Creates an organization; the caller is its owner. `201`.

```json
{ "name": "Grace Chapel", "timeZone": "America/Halifax" }
```

```json
{ "org": { "id": "…", "name": "Grace Chapel", "timeZone": "America/Halifax", "createdAt": 1700000000000 }, "role": "owner" }
```

`timeZone` is an IANA name and is optional (`UTC`). A server may limit how
many organizations one person owns (`403 forbidden`).

### `GET /orgs/{orgId}` · `PATCH /orgs/{orgId}` · `DELETE /orgs/{orgId}`

Read it (any member), change `name` or `timeZone` (admin), or delete it
with everything in it (owner, `204`).

### `POST /orgs/{orgId}/transfer`

`{ "userId": "…" }`: the owner hands the organization to another member,
and becomes an admin. `204`.

### Members

- `GET /orgs/{orgId}/members` (any member):
  ```json
  { "members": [{ "userId": "…", "displayName": "Ada", "email": "ada@example.org", "role": "operator", "joinedAt": 1700000000000 }] }
  ```
  `email` is included only for owners and admins, and for the caller's own entry.
- `PATCH /orgs/{orgId}/members/{userId}` with `{ "role": "operator" }`
  (admin): any role but `owner`; the owner's own role can't be changed. `204`.
- `DELETE /orgs/{orgId}/members/{userId}` (admin), or your own `userId` to
  leave (anyone but the owner). `204`.

### Invites

An admin makes an invite link; anyone who has it can join with its role
until it expires, runs out or is withdrawn.

- `POST /orgs/{orgId}/invites` (admin):
  ```json
  { "role": "member", "expiresInDays": 14, "maxUses": null }
  ```
  All optional (these are the defaults; `expiresInDays` 1–30, `maxUses` 1–1000
  or `null` for no limit). `role` can't be `owner`. `201`:
  ```json
  {
    "invite": { "id": "…", "role": "member", "createdAt": 1700000000000, "expiresAt": 1701209600000, "maxUses": null, "uses": 0 },
    "code": "csi_…",
    "url": "https://api.churchslides.app/join/csi_…"
  }
  ```
  The code is shown only here; servers store a hash of it.
- `GET /orgs/{orgId}/invites` (admin): `{ "invites": [...] }`, the ones that still work.
- `DELETE /orgs/{orgId}/invites/{inviteId}` (admin): withdraw it. `204`.

#### Joining

The `url` is a page a person can open in a browser: it names the
organization and says how to join from the app. The app accepts the link
or the bare code:

- Signed out, it [signs in](#signing-in) with `invite=` on the authorize URL.
- Signed in, it calls `POST /invites/accept` with `{ "invite": "{link or code}" }`:
  ```json
  { "membership": { "orgId": "…", "orgName": "Grace Chapel", "role": "operator" }, "joined": true }
  ```
  Someone already in the organization keeps their role (`joined: false`);
  an invite never changes it. An invite that can't be used is
  `404 not_found`.
- `POST /invites/preview` with the same body says what an invite is for,
  without joining: `{ "orgName": "…", "role": "operator", "currentRole": null }`.

## Band devices

A band device is a phone or tablet on a band stand that follows the live
set. It isn't a person: it has a **device token** instead of a sign-in,
it can see only its own organization's live set, and it never sees the
library.

- `POST /orgs/{orgId}/band-devices` with `{ "name": "Keys iPad", "canLead": false }`
  (operator): `201` with
  `{ "device": { "id", "name", "canLead", "createdAt", "lastUsedAt" }, "token": "csb_…", "url": "…" }`.
  The token is shown only here. `canLead` makes a **leader link**: the
  worship leader's controls as well as the band screen. `url` is a band
  page that opens the [live set](#live-set) with this token.
- `GET /orgs/{orgId}/band-devices` (operator): `{ "devices": [...] }`.
- `DELETE /orgs/{orgId}/band-devices/{deviceId}` (operator): it stops
  working at once. `204`.

The device calls the band API at `{server}/band/v1` with
`Authorization: Bearer {token}`. That API doesn't accept people's access
tokens, and `{api}` doesn't accept device tokens.

### `GET /band/v1/me`

```json
{ "device": { "id": "…", "name": "Keys iPad", "canLead": false }, "org": { "id": "…", "name": "Grace Chapel" } }
```

## Sync

An organization's library (songs, services, readings, its display look,
service history and learned song timing) is kept on every member's computer
and synced through the server. Each computer works offline and sends its
changes when it can, and the server puts every change in one order.

### Records

Everything syncs as **records**: `{ type, id, fields }`. `fields` is an
object whose contents are the app's own (a song's words, its chart, a
service's items). A server stores them as given, except for the few rules
below. Ids are made by the client: 1–100 characters from
`A-Z a-z 0-9 _ -`. Field names are 1–40 characters from `a-z 0-9 _`.

| `type` | What | Who may change it |
| --- | --- | --- |
| `song` | A song with its chart, keys, scripture links and lead-sheet data | `edit_library`: owner, admin, operator |
| `reading` | A prepared reading | `edit_library` |
| `look` | The library's display look (id `default`) | `edit_library` |
| `setlist` | A service and its items | `edit_services`: owner, admin, operator, set leader |
| `service` | One service that took place: when, which songs (history) | `run_live`: owner, admin, operator. Can't be deleted |
| `performance` | Timing the AI learned from one performance of a song | `run_live`. Can't be deleted |

Every member can read every record.

**Rules a server applies:**

- **The last change applied wins, field by field.** A key changed on one
  computer and words changed on another both survive.
- **Song words are never lost.** If a change to a song's `body_text` was made
  on top of an older version than the server holds (its `base` is older than
  the last change to `body_text`), the server keeps the words it replaces as
  a **revision** and reports `conflict: true`. Revisions can be listed and
  brought back. Words edited on a song that was deleted meanwhile are kept as
  a revision too.
- **Deletes are tombstones.** A deleted record keeps its number in the change
  order, so an offline computer learns of it. Its fields are kept, so it can
  come back.
- **References keep records alive.** A record may list what it uses in a
  `refs` field: `["song:abc", …]`. Deleting a record that a live record
  refers to is refused (`in_use`). A change whose `refs` names a deleted
  record brings that record back, whoever makes it: a set leader who adds a
  song to a service while an operator deletes it gets the song back, not a
  service with a hole in it. History (`service`, `performance`) can refer to
  songs but never keeps one from being deleted.
- **Limits:** 50,000 records per library, 20 kept versions per song's words
  (older ones go), and about two sync calls a second per person
  (`rate_limited` beyond that).

### `POST {api}/orgs/{orgId}/sync/push`

Send local changes, oldest first. Members, who may change nothing, are
refused (`forbidden`).

```json
{
  "device": "mac-01J…",
  "changes": [
    { "op": "op_01J…", "type": "song", "id": "s1", "base": 41,
      "set": { "title": "…", "body_text": "…" } },
    { "op": "op_01J…", "type": "setlist", "id": "l7", "base": 0, "delete": true }
  ]
}
```

- `device` is a stable id for this computer's copy of the library. A
  computer's own back-to-back edits are never a conflict with each other.
- `op` is a unique id for the change, made by the client. Sending the same
  `op` from the same person and `device` again (a retry after a lost reply)
  returns the first result and changes nothing. Ops are remembered for 90
  days.
- `base` is the change number of the record the client last had (`0` for a
  record it made).
- `set` names only the fields that changed. A field set to `null` is
  removed. A change has `set` or `"delete": true`, not both.
- At most 500 changes, 20,000 `refs` and 4 MB in one push. A record is at
  most 512 KB.

Response:

```json
{
  "results": [
    { "op": "op_01J…", "status": "applied", "seq": 42, "conflict": true },
    { "op": "op_01J…", "status": "refused", "code": "in_use", "message": "…" }
  ],
  "latest": 42
}
```

`status` is `applied` (with the record's new `seq`) or `refused`, with a
`code` of `forbidden`, `in_use`, `deleted`, `too_large` or `invalid`. A
refused change is final: the client drops it and keeps the server's version
(the next pull brings it). The push as a whole is refused (`bad_request`)
only when it's malformed. The whole push is applied in one transaction.

### `GET {api}/orgs/{orgId}/sync/pull?after={seq}`

Every record changed after `seq`, in change order, each as it is now:

```json
{
  "records": [
    { "type": "song", "id": "s1", "seq": 42, "deleted": false, "fields": { … } }
  ],
  "seq": 42,
  "more": false
}
```

A page holds up to 500 records or about 4 MB. When `more` is `true`, pull
again after `seq`. When it's `false`, `seq` is the server's latest change
number. A `seq` lower than the `after` you sent means the library was reset
(or this copy came from elsewhere): pull again from `0`. A new computer pulls from `0`, which downloads the whole
library. The client replaces its copy of each record with the server's, then
applies its own unsent changes on top.

### `GET {api}/orgs/{orgId}/sync/revisions?type=song&id={id}`

The kept versions of a record's words, newest first, without the words:
`{ "revisions": [{ "id": 3, "field": "body_text", "replacedAt": 1731900000000, "replacedBy": "Sam" }] }`.
Add `&revision=3` for one version with its words:
`{ "revision": { "id": 3, "field": "body_text", "value": "…", … } }`.
Bringing one back is an ordinary push that sets the field.

### Change notices

`POST {api}/orgs/{orgId}/sync/ticket` (any member) returns
`{ "url": "wss://…/sync?org=…&ticket=css_…" }`, a one-use ticket like the
live set's. Over the socket the server sends `{ "type": "changed", "seq": 42 }`
whenever a push changes something, and the client pulls. The first message,
on connecting, carries the latest number. The socket is only a nudge: a
client that missed one pulls when it reconnects. Either side may
send `ping`; the server answers `pong`. Removing a member or changing their
role closes it (`4001`).

## AI gateway

Capability `ai`. An OpenAI-compatible endpoint, so a computer can use AI
features (spoken requests, finding Bible verses, chord suggestions) without
an AI account of its own. The server forwards each call to a provider and
charges it to the organization's daily allowance.

### `POST {api}/orgs/{orgId}/ai/key`

For roles that run services (`run_live`). Returns a key for the gateway
only, not for the API:

```json
{ "key": "csa_…", "expiresAt": 1731990000000, "baseUrl": "https://…/ai/v1",
  "models": { "chat": ["openai/gpt-oss-20b", "openai/gpt-oss-120b"],
              "transcription": ["whisper-large-v3-turbo", "whisper-large-v3"] },
  "features": ["ai", "speech"],
  "speech": { "url": "wss://…", "token": "…", "expiresAt": 1731990000000 } }
```

`features` lists the hosted features the organization's plan has on now:
`ai` (this gateway), `speech` (live speech, below), and later `agent`. A
feature that's off is absent, with no reason given; the app falls back
quietly and shows no prompt. `speech` is present only when the plan has it
and today's allowance isn't used up.

A key lasts 15 minutes; ask for a new one every few. The server checks the
person's membership and role when it issues a key, not on each call, so a
call is never slowed by a database lookup; someone who leaves the
organization or loses the role loses AI within 15 minutes.

### Calls

`POST {baseUrl}/chat/completions` and `POST {baseUrl}/audio/transcriptions`,
as OpenAI's API, with `Authorization: Bearer csa_…`. Only the listed models;
no streaming; output tokens and uploads are capped. A call over the day's
allowance gets `rate_limited` and is not forwarded. The count is kept after
answering and may lag by up to a minute, so a busy day can run slightly over.
Nothing sent is stored; the server keeps a record of each call (who, which
model, tokens or seconds, and its estimated cost), never its content. Each answer has a
`Server-Timing: upstream;dur=…` header: how long the provider took, so the
gateway's own cost is the rest.

A call says which feature it's for in `X-Church-Slides-Feature` (`ai` when
absent; `agent` for the in-app assistant, later). A feature the plan
doesn't have gets `not_on_plan` (402).

### Live speech

The app streams the service's audio to `speech.url` + `/stream?token=…` and
gets words back; the protocol is the speech server's
(`speech-server/README.md`). The token is checked only when a stream
connects, so the app renews it with the key. The speech server reports each
stream's usage to the Church Slides server, which can stop a stream whose
organization is no longer on a plan with speech or is over today's
allowance (close code 4402); the app's own engine takes over.

### `GET {api}/orgs/{orgId}/usage?from=YYYY-MM-DD&to=YYYY-MM-DD`

For owners and admins (`edit_settings`). The organization's use of hosted
features per UTC day, feature and meter, with our estimated cost
(`cost_micro_usd`, millionths of a US dollar), and its plan:

```json
{ "from": "2026-11-01", "to": "2026-11-27",
  "plan": { "id": "pilot", "features": ["ai", "speech"],
            "daily": { "ai.requests": 500, "speech.audio_seconds": 28800 } },
  "days": [ { "day": "2026-11-23", "feature": "speech", "meter": "audio_seconds", "quantity": 5400 },
            { "day": "2026-11-23", "feature": "speech", "meter": "cost_micro_usd", "quantity": 210000 } ] }
```

Meters: `ai`: `requests`, `input_tokens`, `output_tokens`, `audio_seconds`;
`speech`: `streams`, `audio_seconds`, `wall_seconds`, `gpu_seconds`. Up to
a year at a time.

### `GET {api}/orgs/{orgId}/ai/usage`

`{ "day": "2026-11-18", "used": { "requests": 12, "tokens": 9000, "audioSeconds": 40 },
"allowance": { "requests": 500, "tokens": 1000000, "audioSeconds": 10800 } }`.
Days are UTC.

## AI assistants (MCP)

Capability `mcp`; discovery's `mcp` field gives the path (`/mcp`). A remote
[MCP](https://modelcontextprotocol.io) server over an organization's library,
for Claude, ChatGPT or any MCP client.

- **Transport:** Streamable HTTP. `POST /mcp` with one JSON-RPC request,
  answered with one JSON reply (no server-sent streams). Notifications get
  `202`.
- **Sign-in:** MCP's authorization spec. Without a token the server answers
  `401` with `WWW-Authenticate` pointing at its protected-resource metadata
  (`/.well-known/oauth-protected-resource/mcp`, RFC 9728), whose resource is
  `{server}/mcp`. The client registers (dynamic registration or a Client ID
  Metadata Document), signs in with PKCE, and asks for `resource={server}/mcp`.
  A token for `/mcp` isn't accepted by `/v1`, and the reverse. A sign-in that
  names no resource is for `/v1`.
- **Tools:** the same names, arguments and results as the app's local MCP
  tools, plus an `organization` argument (its id or name; optional when the
  person is in one) and `list_organizations`. Tools that read a computer's
  files stay local. Every change is an ordinary sync push, so roles, the
  record rules above and change notices apply.

## Live set

Each organization has one **live room**. The computer running the service
(the *host*) publishes the setlist and the current slide into it, and band
devices follow from anywhere. Signed in, the app opens it by itself for
every service.

### The host

1. `POST {api}/orgs/{orgId}/live/ticket` (operator) returns
   `{ "url": "wss://…/live/host?org=…&ticket=cst_…" }`. The ticket works
   once, within two minutes, so the app asks for a new one for every
   (re)connection.
2. Open a WebSocket to `url`. One host at a time: a newer connection
   replaces the older (closed with code `4000`).

`GET {api}/orgs/{orgId}/live` (any member) says whether a service is live:
`{ "hostConnected": true, "devices": 3 }`.

### Band devices

A band device opens a WebSocket to `{server}/band/v1/live` with its token,
either as `Authorization: Bearer {token}` or, from a browser (which can't set
headers on a WebSocket), as the subprotocols `["csb", "{token}"]`; the server
accepts `csb`.

A band link's `url` is a band page:
`https://churchslides.app/live/#s={server}&t={token}`. Both go in the fragment,
which browsers never send to a server.

### Signed-in members

A member can follow without a band link by signing in on the band page
(`https://churchslides.app/live/`, a public OAuth client identified by
`https://churchslides.app/oauth/band-page-client.json`, or registered at
`registration_endpoint` where client metadata documents aren't supported).

1. `POST {api}/orgs/{orgId}/live/member-ticket` (any member) returns
   `{ "url": "wss://…/live/member?org=…&ticket=cst_…", "canLead": true }`.
   Like the host's, the ticket works once, within two minutes.
2. Open a WebSocket to `url` and talk to the room as a band device. The
   person's role decides leader controls (`canLead`): owners, admins,
   operators and set leaders get them. A role change or removal closes the
   socket (`4001`); the next ticket reflects the new role.

### Messages

JSON text frames. Either side may send the text `ping`; the server answers
`pong` (without waking anything).

**Host → room**

| Message | Meaning |
| --- | --- |
| `{ "type": "setlist", "data": … }` | The whole set: songs, their slides, charts, keys. Kept and sent to every device. |
| `{ "type": "position", "data": … }` | The current slide. Kept and sent to every device. |
| `{ "type": "lan", "data": { "band": "http://…/band", "leader": "http://…/leader?pin=…" } }` | Where devices on the building's network can reach the host directly. Band links get `band`, leader links `leader`. |
| `{ "type": "idle" }` | The service ended; devices wait for the next. |
| `{ "type": "close" }` | Forget everything, including `lan`. |
| `{ "type": "library", "to": "…", "data": [...], "part": 0, "parts": 1 }` | A reply to a leader's `library_request`. |

**Room → host**: `{ "type": "clients", "count": 3 }` (devices connected),
`{ "type": "command", "command": … }` and
`{ "type": "library_request", "from": "…" }` from a leader link.

**Room → device**: on connecting, the current `setlist`, `position` and
`lan` (or `{ "type": "idle" }` if no service is live), then each as it
changes. `{ "type": "welcome", "role": "band" | "leader" }` answers `hello`.
`{ "type": "error", "message": "…" }` refuses a command.

**Device → room**: `{ "type": "hello", "role": "leader" }` (a leader link
gets the leader's controls; any other link stays a band screen),
`{ "type": "command", "command": … }` and `{ "type": "library_request" }`
(leader links only).

The setlist, position and command shapes are the app's band protocol
([`src/lib/band/protocol.ts`](../../src/lib/band/protocol.ts)), the same on the
local network and the self-hosted relay.

### When the room can't be reached

The host keeps its own band server running on the building's network. A
band page that loses the room offers to connect there directly, using the
`lan` address. That page goes back to the room as soon as the host reaches
it again.

A member removed from the organization, or whose role changes, loses their
host connection at once (code `4001`). So does a band device whose link is
removed.

[RFC 6750]: https://www.rfc-editor.org/rfc/rfc6750
[RFC 7009]: https://www.rfc-editor.org/rfc/rfc7009
[RFC 7591]: https://www.rfc-editor.org/rfc/rfc7591
[RFC 8252]: https://www.rfc-editor.org/rfc/rfc8252
[RFC 8414]: https://www.rfc-editor.org/rfc/rfc8414
[RFC 8707]: https://www.rfc-editor.org/rfc/rfc8707
[RFC 9207]: https://www.rfc-editor.org/rfc/rfc9207
[RFC 9728]: https://www.rfc-editor.org/rfc/rfc9728
[Client ID Metadata Document]: https://datatracker.ietf.org/doc/html/draft-ietf-oauth-client-id-metadata-document-00
