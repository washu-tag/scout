# Send Notices to the Scout Launchpad

The launchpad rail has a bell. Your app can put a notice under it for the people who
need to act on something, such as a new request waiting for review or a long job that
has finished. A notice names one or more users, or everyone holding one of your app's
own Keycloak roles. It shows in their open Scout tabs within a couple of seconds, and
your app can withdraw it once it no longer applies.

A notice is a short line of text and a link into your app. Your app keeps the details
on its own pages, where it checks who may see them.

:::{important}
**Never put patient data in a notice.** No names, medical record numbers, accession
numbers, dates of birth, or report text, in any field. Name things by your own ID or a
generic label ("Request 1042", "Export 77"), and let the page behind the link show the
details to whoever may see them. The launchpad can't check this. Reviewing an app that
sends notices includes reviewing what it sends.
:::

:::{note}
Some of what this guide describes depends on Scout changes that are not released yet.
Each is marked **Not yet available**. Until then, notices work on a site whose
operator has set them up by hand ([site settings](#site-settings)), for users named
by username.
:::

## What a notice holds

| Field         | Limit                                   | Notes                                                                                                  |
| ------------- | --------------------------------------- | ------------------------------------------------------------------------------------------------------ |
| `title`       | 1-120 characters                        | Plain text on one line, shown as typed. HTML and Markdown are not rendered.                            |
| `detail`      | up to 300 characters                    | Optional. Plain text on one line.                                                                      |
| `category`    | one of your registered categories       | Used later for people's preferences by kind of notice.                                                 |
| `link`        | a path under one of your path prefixes  | Opens on your app's subdomain, inside the launchpad. No full URLs.                                      |
| `recipients`  | usernames and roles of your own client  | At most 1000 people once roles are expanded (a site can change this).                                 |
| `collapseKey` | up to 200 printable characters          | Optional. Names the notice so you can replace or withdraw it.                                          |
| `expiresAt`   | an ISO 8601 time                        | Optional. Every notice is deleted after 90 days, or at its expiry if sooner.                           |

Text may not contain line breaks, tabs, other control characters, or characters that
reverse the direction of text.

## Register your app

Publish a ConfigMap labelled `launchpad.scout.xnat.org/notifications: "true"`, the way
you publish a [chip](launchpad-chips.md). Each data key holds one registration:

```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: my-service-notices # convention: <component>-notices
  namespace: my-service
  labels:
    launchpad.scout.xnat.org/notifications: 'true'
data:
  sender.yaml: |
    apiVersion: launchpad.scout.xnat.org/v1alpha1
    kind: NotificationSender
    displayName: My Service
    clientId: my-service
    link:
      subdomain: my-service
      pathPrefixes: [/requests, /exports/]
    categories: [review-needed, job-finished]
```

| Field               | Notes                                                                                                                          |
| ------------------- | ------------------------------------------------------------------------------------------------------------------------------ |
| `displayName`       | Shown above each notice, at most 60 characters.                                                                               |
| `clientId`          | Your app's Keycloak client, the `clientId` in your [fragment](service-authentication.md). Your notices are sent with its token. |
| `link.subdomain`    | Where your links open: `https://<subdomain>.<scout host>`. Use your chip's subdomain, so links open in your app's frame.      |
| `link.pathPrefixes` | The paths your links may use. `/requests` covers `/requests` and `/requests/1042`, not `/requests-old`. `/` covers every path.  |
| `categories`        | The kinds of notice you send, as lowercase slugs.                                                                              |

A registration with any invalid field is skipped, and so is every registration of a
client that two registrations claim. The launchpad logs why, on lines starting
`[notifications]`. Deleting the ConfigMap stops your sends; notices already sent stay
until they expire.

**Not yet available:** the launchpad chart does not watch for this label yet. Until it
does, a site operator puts registrations in the directory named by
`LAUNCHPAD_NOTIFICATION_SENDER_DIRS`.

## Get a token

Your app sends as its own Keycloak client, with an access token from the
client-credentials grant:

```bash
curl -s https://keycloak.scout.example.edu/realms/scout/protocol/openid-connect/token \
  -d grant_type=client_credentials \
  -d client_id=my-service \
  -d client_secret="$CLIENT_SECRET"
```

The token must hold the launchpad client's `notifications-send` role. The launchpad
treats the client the token was issued to as the sender, so your app can only send as
itself. Reuse a token until shortly before it expires.

**Not yet available:** fragments can't ask for a service account or the send role yet.
A fragment capability for notifications will add both.

## Send a notice

Call the launchpad from inside the cluster, at its Service (on-prem,
`http://launchpad.scout-core.svc.cluster.local:3000`). The public launchpad address sits
behind the sign-in gate and refuses calls without a browser session.

```bash
curl -s -X POST http://launchpad.scout-core.svc.cluster.local:3000/api/notifications \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{
    "recipients": { "users": ["alice"], "roles": ["my-service-reviewer"] },
    "category": "review-needed",
    "title": "Request 1042 is waiting for review",
    "detail": "Opened by a project member.",
    "link": "/requests/1042",
    "collapseKey": "request-1042-review",
    "idempotencyKey": "request-1042-opened"
  }'
```

`recipients.users` are Scout usernames. `recipients.roles` are role names of your own
client, as in your fragment's `roles`; the launchpad delivers to everyone who holds the
role when you send, directly or through a group. Someone who gets the role later
doesn't see notices sent before. Each person gets the notice once, however many ways it
reaches them.

**Not yet available:** sending to a role needs a Keycloak extension that isn't
released. Until it is, a send naming a role answers `503 role_lookup_unavailable`.

A send answers `201` with the notice's id and how many people it reached:

```json
{ "id": "3f0c0c0e-7d55-4d0f-9a43-0b6a5c3e8c11", "deliveries": 3, "replaced": false }
```

## Replace and withdraw

A send with a `collapseKey` replaces your earlier notice with the same key, for
everyone it reached, and `replaced` is `true`. Use it for a notice whose state moves
on, such as a review that has changed.

Withdraw a notice once nobody needs to act on it, for example when one reviewer has
taken a request that every reviewer was told about:

```bash
curl -s -X DELETE \
  'http://launchpad.scout-core.svc.cluster.local:3000/api/notifications?collapseKey=request-1042-review' \
  -H "Authorization: Bearer $TOKEN"
```

It answers `{"withdrawn": true, "deliveries": 3}`, or `withdrawn: false` when there was
nothing to withdraw. Withdrawing is your app's job: a "needs review" notice that is
never withdrawn stays until it expires. Collapse keys are your own; another app's
notices are never affected.

## Retries

Send an `idempotencyKey` with anything you might retry. The same key within 24 hours
returns the first answer, with the header `Idempotent-Replayed: true`, and sends
nothing new.

## Errors and limits

| Status | `error`                                                                          | What to do                                                                                                   |
| ------ | -------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------ |
| 400    | `invalid_json`, `invalid_request`                                                | Fix the body. `message` names the field. Unknown fields are refused, so a misspelt key fails loudly.          |
| 401    | `missing_token`, `invalid_token`                                                 | Send a fresh token from this realm.                                                                          |
| 403    | `missing_role`, `unregistered_sender`, `sender_disabled`                         | Your client lacks the send role, has no registration, or the site has turned your notices off.              |
| 413    | `too_large`                                                                      | Keep the body under 128 KiB.                                                                                 |
| 422    | `unknown_category`, `link_not_allowed`, `unknown_role`, `too_many_recipients`, `already_expired` | Use a registered category and prefix, one of your own roles, fewer people, or a later expiry.       |
| 429    | `rate_limited`                                                                   | Wait the `Retry-After` seconds. Each app may make 60 calls a minute by default.                              |
| 503    | `unavailable`, `role_lookup_unavailable`, `keys_unavailable`                     | Retry later, with the same `idempotencyKey`.                                                                 |

## What people see

The bell shows how many notices are new. Opening it lists them newest first and marks
them seen, which clears the count. Opening a notice marks it read and opens its link
in your app's frame, as a chip would. People can mark everything read and archive a
notice to remove it from their list. Read state is each person's own.

## Site settings

Notices are off unless the launchpad has a database. With none, the launchpad has no
bell and its notices API answers `404`.

| Environment variable                     | Default | Meaning                                                                                                   |
| ---------------------------------------- | ------- | --------------------------------------------------------------------------------------------------------- |
| `LAUNCHPAD_DATABASE_URL`                 | unset   | The launchpad's Postgres database, as a connection URL. Setting it turns notices on; tables are created at startup. |
| `LAUNCHPAD_NOTIFICATION_SENDER_DIRS`     | unset   | Colon-separated directories of registrations.                                                             |
| `LAUNCHPAD_NOTIFICATION_DISABLED_SENDERS` | unset | Comma-separated client ids whose sends are refused and whose notices are hidden.                          |
| `LAUNCHPAD_NOTIFICATION_RATE_LIMIT`      | `60`    | Calls per app per minute, across all launchpad replicas.                                                  |
| `LAUNCHPAD_NOTIFICATION_MAX_DELIVERIES`  | `1000`  | People one notice may reach once its roles are expanded.                                                  |

Point `LAUNCHPAD_DATABASE_URL` at Postgres itself, not at a transaction-pooling proxy:
replicas tell each other about changes with `LISTEN`, which such a proxy can't carry.
Browsers must reach the launchpad over HTTP/2: each open tab holds a stream, and over
HTTP/1.1 a browser allows only six connections to a host across all its tabs.

**Not yet available:** the launchpad chart, Ansible role and GitOps base don't provision
the database or set these yet.

## Reference

- Design and rationale: ADR 0043 in the Scout repository
  (`docs/internal/adr/0043-in-app-notifications.md`).
