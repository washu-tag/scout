# ADR 0037: Keycloak Realm - Fragments and Single Writer

**Date:** 2026-09  
**Status:** Pending  
**Decision Owner:** TAG Team

## Context

This is another step towards architecting Scout for Pluggable Apps: components that live outside the Scout monorepo, can be installed per site, and operate as first-class members of the platform. ADR 0034 took the first step, letting a component put itself on the Launchpad without any change to Scout. This one lets a component authenticate its users without any change to Scout.

Scout's services which need to authenticate users do so by declaring keycloak clients in a keycloak "realm". Currently that is done with a single realm template document. This has all kinds of negative consequences:

- If we need to add a new service, we have to edit this single realm document. That means third-party services—i.e. "pluggable apps"—can't add keycloak clients in the same way first-party services can.
- The keycloak client exists independently of the service itself; services do not "own" their keycloak clients. The client is created when keycloak is installed and the realm is applied, then the service that uses that keycloak client for making tokens gets installed later. And if we ever remove them, we have to remove both or we could leave one or the other orphaned.

## Decision

### Keycloak realm fragments

We must have a mechanism by which services can include in their deployments (by which I mean helm charts) a representation of their keycloak client and associated groups and roles. I call this a service's keycloak "fragment".

Any third-party pluggable app can include a keycloak fragment and get a keycloak client defined for it just the same as a first-party service can by including itself in the central realm template. And some of those first-party services could extract themselves from the central realm and publish themselves as fragments as well.

The fragments will be written to labeled ConfigMaps, the same way as launchpad chips (ADR 0034). I won't include the exact schema of the Fragment document, since it is subject to change in the future and I don't want to be editing or superseding the ADR any time we make an edit. But the initial schema is based on these principles:
1. Make it as close as possible to the [OIDCClientRepresentation](https://www.keycloak.org/admin-api/admin-api-v2#_oidcclientrepresentation) from the in-development experimental Keycloak Admin API v2. That's where Keycloak is headed, and in the future I would like all of this infrastructure to migrate to use keycloak's supported APIs and operators and such. But since all that is still in progress, we do need to make our own version now. I'd like to hew closely to where it looks like they're going.
2. I want most or all of the existing Scout keycloak clients to be expressible in this form. That paves the way for us to to migrate some services' clients out of the realm and into fragments after this is all implemented and running.

A fragment declares Scout concepts, not direct Keycloak objects or configuration. The capabilities we expose in the fragment are deliberately narrower than what can be accomplished directly in the Keycloak realm. A fragment published with an app states what it is and what its users need, while Scout interprets that fragment and turns it into the narrow slice of Keycloak config we allow. The design of the fragment schema is as much about what apps _cannot_ express as what they _can_.

A fragment which is published and discovered is merged into the Keycloak realm without any human intervention, review, or approval. We assume that any person or entity able to write a ConfigMap into a trusted namespace (which can be configured to be `ALL` namespaces) is trusted with realm configuration. We do limit the effects these writers can have on the realm through the limited fragment vocabulary.

### Reconciler: Centralized realm composition

For the foreseeable future there will still be a Scout base keycloak realm in addition to the fragments that get deployed with services. We need a central "reconciler" service which will watch for any changes to the base realm, the fragments, or secrets; join the base realm together with all the fragments; and apply that composed realm to keycloak.

#### One writer

The reconciler is the only thing that ever applies a realm to Keycloak; every other producer writes a document and stops. This is a change from the current model where the ansible role and Flux apply both directly launch `keycloak-config-cli` Jobs which apply the realm.

In the new model, at deploy time they only have access to the _base_ realm. If they still wrote directly then an update to the base realm or a secret rotation would wipe out all clients defined in fragments until the reconciler was able to notice the change and re-apply. During that window all services with fragment-defined clients would be inaccessible. Instead, they only write the changes to the base realm or the secrets and let the reconciler pick up those changes and apply them to the realm.

#### The Ready state means the base realm has applied, not the fragments

This one is a bit of safety that we have to include because of the one-writer policy.

When we deploy the `keycloak-realm` chart, either with ansible or the Flux lane, it reports Ready when it is done. In today's world that Ready means the realm has been applied and the clients exist. Now that we decouple _writing the realm template_ from _applying the realm to keycloak_, we need to be careful about the `keycloak-realm` chart's Readiness signal. If it reported Ready when it was finished writing the template, then downstream consumers who need their client to exist might start running before the realm gets applied and before their client exists. So we need the chart to report Ready when the realm exists. However, this also needs to be fast and robust against other sources of errors and delays. In particular, we don't want to keep the chart waiting around while we make sure we get all the fragments, validate them, merge them into the realm, etc. Anything that is waiting on the realm to exist is waiting on the _base_ realm only, not fragments. So we have the `keycloak-realm` chart report Ready once the base realm has been applied, a closed set known at deploy time.

#### Fragment installation is immediate, but retraction is damped

Another safety feature, this time about guarding against spurious retractions.

If a fragment appears, we know we can add it to the realm right away. But if one disappears, we can't be quite sure right away. It may be that an operator has uninstalled an app and that removed a fragment, but it may be that they're upgrading their app and helm has momentarily removed the ConfigMap only to re-add one a moment later. There may also be other reasons for brief blips where fragment files disappear and reapper. 

For this reason, when we see a fragment disappear we deliberately wait for a short timeout to pass before removing it. If the fragment reappears after the timeout, we do nothing. If a slightly changed version appears, we apply it, with ideally no downtime where we've deleted the fragment's client. 

(The exact implementation details of the timing windows are outside the scope of this ADR. But that's the idea.)

#### The realm is continuously converged

So far we've described how the reconciler is responsible for applying changes to the sources (base realm and fragments) into the realm at runtime, not just at deploy time. But it is also responsible for noticing if the live realm has diverged from what we expect and what we have applied, and correcting those divergences. The published state in the form of the base realm, fragments, and client secrets make up the source of truth for the realm. 

#### Secrets are not stored in the base or composed realm

In the current ansible deploy, we apply all the client secrets into the realm template before storing the composed realm into a Secret, which is sent to the `keycloak-config-cli`. The Flux deploy works differently: the realm that it publishes carries no client secrets, only `$(env:service)` placeholders. The `keycloak-config-cli` Job gets both the realm definition _and_ the client secrets mounted in, and it is responsible for applying the client secrets.

The reconciler allows us to make that improvement in the ansible deploy as well. Both deployment lanes will publish the base realm with no embedded client secrets, the latter of which will be published separately in a Secret. The base realm and the composed (base + fragments) realm need not be considered sensitive, and can be stored, hashed, inspected, etc. as needed. This will make the job of the reconciler easier.

Those client secrets do need to live somewhere, however. The base client secrets will live in a single Secret, and any clients that are defined in a fragment will have their own separate Secrets. All of those will be mounted into the `keycloak-config-cli` Job that ultimately writes the realm, so it can evaluate the `$(env:service)` placeholders with the real values. 

But this opens up a danger: if a particular client secret does not exist or has an empty value, a static string will be passed in as the secret value in the realm. This leaves a client's tokens insecure and vulnerable to impersonation. To prevent this, the reconciler will need to have access to all the Secrets where client secrets are kept. It can inspect the composed realm for any `$(env:service)` placeholders and ensure a non-empty secret value exists. If there is any lingering non-resolvable placeholder, it will refuse to apply it.

Exactly what that looks like—whether the whole realm update is stopped, whether we can remove a fragment with no client and apply the rest of the realm, or some other possiblility—is left as an implementation detail. What was built splits on who owns the client: a fragment whose own Secret is missing is rejected by itself, because that is one component's problem and the rest of the realm should still apply; an unresolvable placeholder belonging to the base realm stops the whole apply, because a platform client cannot be dropped the same way.

#### Broken or rejected fragments should not block the whole realm

As fragments come in, they are validated before they are applied. If a single fragment is broken or invalid, it should be left off while the rest of the apply continues. This is the same approach taken by ADR 0034 for the launchpad chips: a single broken chip should not prevent the whole page from rendering.

### In case of emergency the base realm can still be applied directly

The single-writer stance is a _policy_, not a structural impossibility. We can keep the existing `keycloak-config-cli` Job in the realm chart behind a default-`false` flag. If for whatever reason the reconciler is not functioning properly and we have some need to change the base realm configuration, we can flip that switch and deploy the chart to directly apply the base realm. In that way the core Scout services can still be updated through a reconciler outage. 

Note: A reconciler outage does not change anything about the realm. If the reconciler went out and we didn't need to make any changes, all other services would continue working perfectly fine. This "emergency" scenario only exists for the case where the reconciler is not working _and_ we need to update the base realm.

Note also: any pluggable app services which require clients published in fragments are not guaranteed to work in this emergency scenario, because the chart only has access to the base realm. Applying the base realm outside of the reconciler will not delete fragment clients or roles, but the `scout-user` / `scout-admin` role grants are on a centralized part of the realm and will be wiped. That will leave a pluggable app's client defined and able to mint tokens, but the tokens will carry no roles so will be rejected by the app service.

The fragments continue to exist, however, so once the reconciler comes back into working order it will repopulate these grants into the realm.

## Consequences

The reconciler becomes critical-path infrastructure for platform auth. In exchange, a component can own its own client end to end, third-party clients become possible, and first-party clients can migrate out of the monolithic template one at a time rather than all at once.

## Alternatives Considered

The main alternative is adopting Keycloak's own operator and CRs. The Keycloak Admin API v2 is purported to be heading in exactly this direction, with better support for declarative realm state. However, at time of writing, this Admin API v2 is currently being actively worked on. It is only partially released, and what does exist carries a prominent warning:

> This feature is **experimental** and may introduce breaking changes in future versions of Keycloak. Do not use this feature in production environments.

Once this API becomes more stable we may move towards using it. But for now we cannot rely on it.

## Supersedes

- **ADR 0031 §2 ("One-off operations become Jobs with dependencies")** — specifically its closing claim that "the keycloak-config-cli realm import is already a Helm-hooked Job and doesn't change"; the import becomes reconciler-driven and the Helm hook becomes the break-glass path.
- **ADR 0026** — the description of the `xnat` client's lifecycle as a block in the realm template gated on `enable_xnat`; the client's provenance becomes a fragment shipped by the XNAT chart. Its consequence about orphaned XNAT *users* stands.

## Related

- **ADR 0034** — precedent, not superseded: the labelled-ConfigMap-plus-sidecar discovery contract is the same one the launchpad catalog established.
- **ADR 0003** — the per-service client model is preserved; what changes is provenance, so "adding a protected service" gains a fragment step.
- **ADR 0030 / 0031 / 0033** — the reconciler becomes a first-class released artifact, with the CI, release, and air-gap bundling wiring those ADRs require.
- **ADRs 0020–0025** — untouched; the base realm keeps the Trino AuthZ attribute machinery, the OPA bundle SPI, and the `scout-users` admin surface.

## References

- [Keycloak Admin API v2](https://www.keycloak.org/admin-api/admin-api-v2)
