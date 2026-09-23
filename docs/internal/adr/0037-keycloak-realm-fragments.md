# ADR 0037: Keycloak Realm Fragments and Reconciler Service

**Date:** 2026-09  
**Status:** Pending  
**Decision Owner:** TAG Team

## Context

This is another step towards architecting Pluggable Apps for Scout: components that live outside the Scout monorepo, can be installed per site, and operate as first-class members of the platform. ADR 0034 took the first step, letting a component put itself on the Launchpad without any change to Scout. This one lets a component authenticate its users without any change to Scout.

Scout's services which need to authenticate users do so by declaring keycloak clients in a keycloak "realm". Currently that is done with a single realm template document that lives in the scout monorepo. This has all kinds of negative consequences:

- If we need to add a new service, we have to edit this single realm document. That means third-party services—i.e. "pluggable apps"—can't add keycloak clients in the same way first-party services can.
- The keycloak client exists independently of the service itself; services do not "own" their keycloak clients. The client is created when keycloak is installed and the realm is applied, then the service that uses that keycloak client for making tokens gets installed later. And if we ever remove the service we have to also remember to remove the keycloak client, or _vice versa_, otherwise we could leave one or the other orphaned.

## Decision

### Keycloak realm fragments

We must have a mechanism by which services can include in their deployments[^1] a simplified representation of their keycloak client and associated roles. I call this a service's keycloak "fragment".

Any third-party pluggable app can include a keycloak fragment and get the same kind of a keycloak client defined for it as a first-party service can define for itself by writing the client directly in the central realm template. And some of those first-party services could extract themselves from the central realm and publish themselves as fragments as well, with no change to its keycloak client or the token behavior.

The fragments will be written to labeled ConfigMaps, the same way as launchpad chips (ADR 0034). The initial schema[^2] is based on these guiding principles (which doesn't mean the implementation hits these targets perfectly):
1. Make it as close as possible to the [OIDCClientRepresentation](https://www.keycloak.org/admin-api/admin-api-v2#_oidcclientrepresentation) from the in-development experimental Keycloak Admin API v2. That's where Keycloak is headed, and in the future I would like all of this infrastructure to migrate to use keycloak's supported APIs and operators and such. But since all that is still in progress, we do need to make our own version now. I'd like to hew closely to where it looks like they're going.
2. Most or all of the existing Scout keycloak clients should be expressible in this form.

A fragment declares Scout concepts, not direct Keycloak objects or configuration. The capabilities we expose in the fragment are deliberately narrower than what can be accomplished directly in the Keycloak realm. A fragment published with an app states what it is and what its users need, while Scout interprets that fragment and turns it into the narrow slice of Keycloak config we allow. The design of the fragment schema is as much about what apps _cannot_ express as what they _can_. We do want fragments to be able to express everything that Scout clients currently need and use but we don't want to open up the entire world of Keycloak's syntax. If we need some new bit of Keycloak functionality that Fragments don't yet support, that will need to be a deliberate Scout change to the Fragment schema.

A fragment which is published and discovered is merged into the Keycloak realm without any human intervention, review, or approval. We assume that any person or entity able to write a ConfigMap into a trusted namespace (which can be configured to be `ALL` namespaces) is trusted with realm configuration. We do limit the effects these writers can have on the realm through the limited fragment vocabulary.

Fragments should not contain secrets, only references to existing Secrets. Those Secrets are written out of band and are not managed by the reconciler. Where exactly they come from is up to the deployment lane and is not specified here.

If a pluggable app is removed, that will remove its fragment as well. We can and should remove the keycloak client and roles that came from that fragment and are now orphaned.

### Disjoint writers

We adopt a principle that we'll expand upon in future sections. The base realm which lives in the core Scout monorepo owns all its components and is written and managed through existing mechanisms not covered by this ADR. The third-party realm components—clients, roles, and the association of those roles into realm groups—are owned by their respective pluggable apps. The reconciler service is responsible for managing them in the realm.

The base realm apply can't remove or edit the fragment clients or roles. Nor can the reconciler remove or edit anything in the base realm. The two writers and what they own need to be disjoint.

The disjoint writing is fairly easy when it comes to clients and client roles. The `config-cli` that core Scout uses to write the realm today will not touch clients it doesn't know about, and we can put similar controls into the fragment reconciler. The tricky part is getting those client roles into the central realm groups.

### Base realm holds empty composite roles for fragments

The current realm looks something like this:
```
roles.client
  launchpad    [launchpad-user, launchpad-admin]
  jupyterhub   [jupyterhub-user, jupyterhub-admin]
  xnat         [xnat-access]                          … and one block per app

roles.realm    — absent —

groups
  scout-user    clientRoles ─┬──▶ launchpad  : launchpad-user
                             ├──▶ jupyterhub : jupyterhub-user
                             ├──▶ superset   : superset_gamma, superset_sql_lab
                             ├──▶ xnat       : xnat-access
                             └──▶ …
  scout-admin   clientRoles ─┬──▶ launchpad  : launchpad-admin
                             └──▶ …
```
We have two core groups for managing user permissions. If we assign a group to a user, that brings with it corresponding roles for each of the keycloak clients that we've associated with that group. Our current practice for making that association is to have a static `clientRoles` object on each group in the base realm with all the core clients and their roles. This ADR doesn't touch that practice; it could stay or change, and there's no need to supersede this ADR for that. What this ADR defines is an extension point we're adding to the base realm: composite realm roles. This comes from [Keycloak's own guidance](https://www.keycloak.org/docs/latest/server_admin/index.html#con-comparing-groups-roles_server_administration_guide):

> Composite Roles are similar to Groups as they provide the same functionality. The difference between them is conceptual. Composite roles apply the permission model to a set of services and applications. Use composite roles to manage applications and services.
> 
> Groups focus on collections of users and their roles in an organization. Use groups to manage users.

We propose to add empty[^3] composite realm roles into the base realm, and connect them into the groups.[^4]

```
roles.realm
  scout-user    { "name": "scout-user"  }
  scout-admin   { "name": "scout-admin" }

groups
  scout-user    realmRoles  ───▶ scout-user
                clientRoles ─┬──▶ launchpad  : launchpad-user
                             ├──▶ jupyterhub : jupyterhub-user
                             └──▶ …
  scout-admin   realmRoles  ───▶ scout-admin
                clientRoles ─┬──▶ launchpad  : launchpad-admin
                             └──▶ …
```

These roles get created when the base realm is applied, but they don't do anything and nothing in base Scout writes to them. These composite roles are where fragment client roles can be written. After the reconciler defines the fragment clients and client roles in the realm, it can add those client roles into the appropriate composite realm roles. That will cause the fragment client roles to be associated to the group just the same as if we had written them into the group's `clientRoles`, but they will not be overwritten if we ever need to apply the base realm again. This is because of an important detail in the realm roles, which should be stated explicitly:

**The realm role definitions carry no `composites` key.** If this key exists in the realm role definition, there is a chance a base realm apply will overwrite and wipe out the fragment client roles from the composite realm roles.[^5] However, if `composites` is left off the realm definition entirely, applying the base realm will ignore any client roles attached to the composite role. Keeping the `composites` key off the realm definition is what keeps the two writers disjoint.

Because these composite realm roles are where all the fragment client roles must be hung, the base realm must be applied before the reconciler can apply any fragments to the realm. The reconciler treats the composite realm roles as preconditions, never creates them, and fails the fragment (retryably) if they're absent.

#### Disjoint fragments

Just as fragments are disjoint from the base realm—implying that no fragment can declare a `clientId` that exists in the base realm—so too are fragments disjoint from each other. The reconciler must enforce that no two clients share the same `clientId`, and no client reads anyone else's secrets.

### Reconciler: Centralized realm composition

For the foreseeable future there will still be a Scout base keycloak realm in addition to the fragments that get deployed with services. We need a central "reconciler" service which will watch for any changes to the fragments or their associated secrets, map the fragment into Keycloak API concepts, and apply the necessary changes to the realm.

#### Fragments apply at runtime

The existing Scout base realm is applied once at deploy time and is static after that. It is possible for an operator to modify realm settings at runtime in the Keycloak UI or API. This is "drift" from the realm document. That drift can be "corrected" at the next realm apply, or ignored, depending on the settings and what specifically has changed. (This is all somewhat outside the scope of this specific ADR, but it is included for context.)

Fragments will be applied in a similar way, but continuously at runtime instead of once at deploy time. The reconciler will watch for new or changed fragment documents, or their associated client secrets. On a change (a new fragment, an updated fragment, or an updated fragment-associated secret) the reconciler will apply the changes to the realm.

The associated client *secret* is the one input not watched at all: the reconciler reads each Secret by name under a `get`-only grant, so a rotation is picked up on the next periodic pass. A watch is possible (`resourceNames` does scope a `list` or `watch` that uses a `metadata.name` field selector), but it would mean one watch per fragment Secret and a wider grant in every app's chart. Rotation is rare and not latency-sensitive, and the alternative grant gives away every other Secret in the app's namespace. Unchanged fragments need not be applied, only those with changes. Conversely, an unchanged fragment _may_ be applied, say if the reconciler pod restarts or if it always applies everyting periodically; nothing here restricts it to either require or prohibit applying unchanged fragments.

Whenever the source fragment documents are updated, the reconciler is required to apply those changes. Notably, the reconciler is not _required_ to correct drift in the realm otherwise. If an operator makes a change to a fragment-managed component within keycloak, that drift can remain indefinitely. Conversely, the reconciler is not _prohibited_ from correcting drift. Something like a periodic resync of all fragment clients, changed or not, could correct most sources of drift. But I argue that the disjoint nature of the realm writes mean that "drift" is only likely to occur intentionally due to operator changes, and it may be best to leave it. We leave this decision out of the architecture, and defer it to the implementation.

Similarly, the exact implementation details of the change detection and correction are left unspecified here: it could be instantaneous (or nearly so) or it could happen after some delay, the updates could be received synchronously or asynchronously, this behavior could be configurable or not, insertions could behave differently from updates could behave differently from deletions, etc.

#### Broken or rejected fragments should not block other fragments

As fragments come in, they are validated before they are applied. If a single fragment is broken or invalid, it should be left off while the rest of the apply continues. This is the same approach taken by ADR 0034 for the launchpad chips: a single broken chip should not prevent the whole page from rendering.

#### Minimal state

The reconciler shouldn't need to keep any state on what the fragments are or what it owns. The fragment ConfigMaps and associated Secrets are the sources of truth for what _should be_ in the realm. In terms of what _is_ in the realm, the reconciler can write some keycloak attribute into the clients it owns that distinguishes them from the base realm's clients. That way if a fragment disappears because its service was removed, the reconciler could recognize that there is a client that it can clean up.

#### Availability floor

If the reconciler goes down, the keycloak clients defined by fragments will continue to exist. A reconciler outage should not impact user access to services which have clients defined from fragments. It could impact the availability of _new_ services which haven't yet had their fragments defined into realm clients yet, or _updates_ to existing services. But at worst this is a delay rather than an outage of previously working services.

#### Reconciler's keycloak credentials

The reconciler will authenticate to keycloak using a dedicated service-account client, defined in the base realm, holding three `realm-management` roles rather than `realm-admin`: `manage-clients`, `view-realm`, and `manage-realm`.

`manage-realm` is more than we would ideally want to grant. It is required for a single operation: writing the client role into the composite realm role, which is what binds a fragment's role to a group.

## Consequences

The realm as it exists on keycloak isn't defined in any one place. That will make it harder to understand and debug if there is some issue; we could need to look at the base realm, or a fragment realm in some third-party pluggable app, or the reconciler itself. That's kind of unavoidable based on the existence of pluggable apps, but still good to note.

Reconcile cost grows with the fragment count: each pass reads and writes per fragment and per tier role, and garbage collection lists every client in the realm. Sized for a handful of pluggable apps, which is the expectation this is accepted against. Nothing has been measured beyond one fragment, so a site heading for tens of them should expect to revisit the per-pass call count before it revisits anything else here.

## Alternatives Considered

The main alternative is adopting Keycloak's own operator and CRs. The Keycloak Admin API v2 is purported to be heading in exactly this direction, with better support for declarative realm state. However, at time of writing, this Admin API v2 is currently being actively worked on. It is only partially released, and what does exist carries a prominent warning:

> This feature is **experimental** and may introduce breaking changes in future versions of Keycloak. Do not use this feature in production environments.

Once this API becomes more stable we may move towards using it. But for now we cannot rely on it.

Another alternative is doing the writes **inside Keycloak**, as a `RealmResourceProvider` SPI — the pattern Scout already chose for the `scout-users` resource behind the launchpad admin console (ADR 0025). It is the most appealing option on permissions: an SPI has direct access to the realm model, so it needs no `realm-management` grant and the narrowing this ADR settles for in code becomes structural.

Rejected on blast radius. It puts fragment logic inside the process that issues every token, so a reconciler bug becomes a token-issuance outage rather than a delay — the same argument the paragraph below makes about the base realm, one level further in. The availability floor above holds only because the reconciler is a separate process that can crash unnoticed. Keycloak's internal model surface is also far less stable across upgrades than the admin REST API.

Another alternative is making the reconciler the sole writer of the realm: both the Scout base realm and all fragments would be composed together into a single realm document and applied with the `keycloak-config-cli`. This removes some questions of how to couple the fragments' client roles into the realm's groups without overwriting the core client roles, since we can ensure the reconciler collects everything together. But it is very risky. We need the core services to work reliably; without the core services there is no Scout and without keycloak tokens users cannot access the core services. Putting a brand new, untested, complex reconciler service directly into Scout's critical infrastructure day one is a daunting task. Better to have the brand new, untested, somewhat less complex reconciler service manage only the fragment clients, which are less critical in case of reconciler or fragment bugs. And ideally one day we have the Keycloak operator manage all of this without any dedicated Scout services anyway.

## Supersedes

- **ADR 0026** — the description of the `xnat` client's lifecycle as a block in the realm template gated on `enable_xnat`; the client's provenance becomes a fragment shipped by the XNAT chart. Its consequence about orphaned XNAT *users* stands.

## Related

- **ADR 0034** — precedent, not superseded: the labelled-ConfigMap-plus-sidecar discovery contract is the same one the launchpad catalog established.
- **ADR 0003** — the per-service client model is preserved; what changes is provenance, so "adding a protected service" gains a fragment step.
- **ADR 0030 / 0031 / 0033** — the reconciler becomes a first-class released artifact, with the CI, release, and air-gap bundling wiring those ADRs require.
- **ADRs 0020–0025** — untouched; the base realm keeps the Trino AuthZ attribute machinery, the OPA bundle SPI, and the `scout-users` admin surface.

## References

- [Keycloak Admin API v2](https://www.keycloak.org/admin-api/admin-api-v2)

## Footnotes

[^1]: By "deployment" I mean a helm chart. That's how I (the author, John Flavin) currently envision pluggable apps existing. However, that's an implementation detail and isn't particularly important to the point. I don't want that detail to need to be superseded later if we decide pluggable apps take some form other than a helm chart. If that's the case, hopefully you already know it and we can just leave this ADR alone.
[^2]: I won't include the exact schema of the Fragment document, since it is subject to change in the future and I don't want to be editing or superseding the ADR any time we make an edit.
[^3]: By "empty" I mean the realm definition has no `composites` key. That's important, and I'll explain why later.
[^4]: In the example, the realm roles have the same names as the groups. This isn't required, they could be named whatever we want.
[^5]: I say "a chance" as a bit of weasel phrasing, but this was tested explicitly. When `composites` was absent or an empty object `{}`, a base realm apply left the fragment client roles alone. If `composites` was specifically defined with its own empty structure, like `"composites": {"realm": [], "client": {}}`, that wiped out the fragment roles from the composite realm role. I didn't think this much detail was warranted in the main section, and it's much easier to state "don't define anything in `composites`" than what is or is not permitted.
