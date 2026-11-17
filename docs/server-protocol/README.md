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
| Sync of organization libraries | Not yet specified |
| Live set | Not yet specified (today's [relay messages](../../relay/src/room.ts) are the starting point) |
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
  "capabilities": ["account"],
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
| `capabilities` | What this server offers. A server may offer any subset. Values so far: `account`. Planned: `sync`, `live`, `ai`, `mcp`. The app shows only features whose capability is listed. |
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

[RFC 6750]: https://www.rfc-editor.org/rfc/rfc6750
[RFC 7009]: https://www.rfc-editor.org/rfc/rfc7009
[RFC 7591]: https://www.rfc-editor.org/rfc/rfc7591
[RFC 8252]: https://www.rfc-editor.org/rfc/rfc8252
[RFC 8414]: https://www.rfc-editor.org/rfc/rfc8414
[RFC 8707]: https://www.rfc-editor.org/rfc/rfc8707
[RFC 9207]: https://www.rfc-editor.org/rfc/rfc9207
[RFC 9728]: https://www.rfc-editor.org/rfc/rfc9728
[Client ID Metadata Document]: https://datatracker.ietf.org/doc/html/draft-ietf-oauth-client-id-metadata-document-00
