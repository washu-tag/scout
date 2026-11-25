# ADR 0043: In-App Notifications

**Date:** 2026-11
**Status:** Proposed
**Decision Owner:** TAG Team

## Context

Scout's services and Pluggable Apps have things to tell people while they are
somewhere else in Scout: a new user is waiting for approval (ADR 0025), a long job has
finished, a request someone filed was approved. Today a service can only show this on
its own pages, so people find out the next time they open that service. The launchpad
rail (ADR 0042) is on every Scout page, which makes it the place to show these.

Requirements:

1. One place in the rail shows what needs a person's attention, from any service, with
   a count of what is new.
2. A service can address a person, or everyone holding one of its roles, and can
   withdraw a notice that no longer applies, so work one admin has handled clears for
   the others.
3. A service can't speak for another service, address people outside its own roles, or
   link outside Scout.
4. No patient data in a notice. Sites hold PHI; a notice points to the page with the
   details, and that page checks access as it always does.
5. New notices show within seconds, on every site, without a new datastore.
6. Email and other channels can be added later without changing how services send.

## Decision

### Notices live in the launchpad

The launchpad gets a database of its own: a `launchpad` database and role on the
platform Postgres, beside Superset's and Temporal's (CloudNativePG on-prem, RDS on
AWS). It holds:

- **Notices:** the sender, a category, a title (at most 120 characters), an optional
  detail (at most 1,000, with line breaks), a link, an optional collapse key and
  idempotency key, the time sent, and an optional expiry.
- **Deliveries:** one per recipient, naming the notice, the username, the role it came
  through (if any), and when it was seen, read and archived.

Roles are expanded when a notice is sent. The launchpad resolves each role to the users
who hold it at that moment, directly or through a group or composite role, and writes
one delivery per user, without duplicates. Each person's read state is their own. A
delivery stays until the sender withdraws the notice or it expires, even once its person
no longer holds the role it came through; the page behind the link checks their access
as always. Someone who gains the role later doesn't see older notices.

Notices are kept for 90 days, or until their expiry if sooner, and then deleted with
their deliveries. Who was told what, and when they read it, is itself sensitive, so the
tables are handled like the rest of the platform's PHI-adjacent data.

### Sending

- **Registration.** An app that sends ships a ConfigMap labelled
  `launchpad.scout.xnat.org/notifications: "true"`, the way it ships a catalog entry
  (ADR 0034). It names the sender as people will see it, the app's Keycloak client,
  the subdomain its links open on with the path prefixes they may use, and its
  categories.
- **Identity.** The app calls the launchpad with a token for its own Keycloak client
  (the client-credentials grant). Fragments can't enable service accounts today
  (ADR 0037), so the fragment schema gains a notifications capability. Scout turns it
  into a service account and links a base-realm client scope to the app's client as
  optional; the scope puts the launchpad client's `notifications-send` role in a token
  that asks for it. Assigning the role to the service account instead would take
  `manage-users`, which the fragment reconciler doesn't hold (ADR 0037) and which would
  let it change any user's roles. A user's sign-in to the app could ask for the scope
  too, so the launchpad accepts only client-credentials tokens: Keycloak marks them with
  a `client_id` claim, which a user's token lacks. The sender of a notice is the token's
  client, matched to its registration; a token without the role, a token issued to a
  user, or a client with no registration is refused.
- **Addressing.** Usernames, and the roles of the app's own client. Realm roles (such as
  `scout-admin`) and other clients' roles are refused, except for senders Scout
  registers itself in the launchpad chart. The launchpad expands a role through a new
  scout-users endpoint (ADR 0025) that returns its effective members, callable only by
  the launchpad's client.
- **API.** `POST /api/notifications` takes the recipients, category, title, detail, link
  path, and optional collapse and idempotency keys.
  - The same idempotency key within 24 hours returns the first result instead of
    sending again.
  - A collapse key replaces the sender's earlier notice with the same key: its
    deliveries are removed and the new notice is delivered.
  - `DELETE /api/notifications?collapseKey=<key>` withdraws the sender's notice for
    everyone it reached. This is how a "needs review" notice clears once someone has
    handled it.
- **Limits.** A rate limit per sender (`429` past it), a cap on deliveries per notice
  after roles are expanded, and a site setting that disables a sender, which also hides
  its notices.

### Content

- Plain text, shown as text: never HTML or Markdown.
- A link is a path. The launchpad joins it to the registration's subdomain, accepts it
  only under a registered prefix, and opens it in the shell like any service page
  (ADR 0042). Notices carry no free-form URLs.
- Services name things by an ID or a generic label (a request number, a project), never
  by a patient, an accession, or report content. The page behind the link shows the
  details to whoever may see them.

### Delivery to the browser

- The rail opens a Server-Sent Events stream at `/api/notifications/stream`. Its events
  carry only the unseen and unread counts and a change marker; the rail fetches the list
  from `GET /api/notifications` when the bell is opened or the marker changes.
- A comment line every 25 seconds keeps the stream open through the AWS load balancer's
  60-second idle timeout and on-prem proxies.
- With more than one launchpad replica, a send on one reaches the streams on the others
  through Postgres `LISTEN/NOTIFY`. The payload names the affected users, never a
  notice's content.
- The stream checks the session at each keep-alive. Once the session has ended it sends
  a `session` event and closes, and the rail signs the window in again as ADR 0042
  describes. Reconnects wait a randomized interval, so a launchpad rollout doesn't bring
  every tab back at once.
- Browsers must reach the launchpad over HTTP/2. Over HTTP/1.1 each stream holds one of
  the six connections a browser allows per host, across all its tabs.
- Opening the bell marks the deliveries it shows as seen, and the count clears. Opening
  a notice marks it read; the bell also has "mark all read" and archive.

### Other channels later

Email and other channels are not part of this decision. Every notice has a category, so
preferences by category and channel can be added; collapse keys give digests something
to group by. Notice text stays free of patient data so it can leave Scout later.

## Security

- A sender is a Keycloak client whose client-credentials token carries the send role,
  through a scope Scout links from the app's fragment; the app can't grant it to itself.
  A token names its own client, so one app can't send as another, and a token issued to
  one of the app's users can't send at all.
- Apps address only usernames and their own client's roles.
- Links stay on the app's own subdomain, under its registered prefixes.
- Text is plain and length-capped.
- A notice comes from inside Scout, so people will trust it. Rate limits, the delivery
  cap and the disable switch bound what a buggy or compromised sender can do with that.
- No patient data is a rule the launchpad can't check. The authoring guide states it,
  and reviewing an app that sends includes its notices.
- The list, stream and read-state endpoints use the launchpad session; a person sees and
  changes only their own deliveries.

## Consequences

- The launchpad becomes stateful. Every site provisions its database, the launchpad runs
  migrations at startup, and the database is backed up with the rest of the platform
  Postgres.
- The fragment reconciler, the realm (the `notifications-send` role on the launchpad's
  client and the client scope that carries it) and the scout-users extension each
  change.
- Every site serves the launchpad to browsers over HTTP/2.
- Apps get an authoring guide for sending, beside the chip guide
  (`docs/source/customize/launchpad-chips.md`).
- New holders of a role don't see notices sent before they had it. A service whose work
  queue matters to them keeps the queue on its own pages.
- Someone who loses a role keeps the notices it brought them until the sender withdraws
  them or they expire. The page behind each link checks their access.
- Withdrawing a notice is the sender's job. One that never withdraws leaves stale
  "needs review" notices until they expire.

## Alternatives considered

- **A notification product (Novu).** Self-hosting needs MongoDB, Redis and S3 beside its
  own services, and Novu describes its self-hosted edition as for testing. Nothing
  credible ran on Postgres alone, air-gapped.
- **A separate notifications service.** Another deployment, with its own sign-in for the
  rail to read through; the launchpad already holds the session.
- **Resolving roles when people read.** Role-addressed notices would follow current
  membership, but every count becomes a join against the person's roles, and an event
  meant for the people who held a role at the time reaches whoever holds it later.
- **Polling.** Simpler, and enough for most notices, but a new notice would take up to
  the poll interval to show.
- **Kubernetes service-account tokens for sender identity.** No Keycloak change, but
  in-cluster only, and a second identity system beside the one Scout already decides
  access with.
