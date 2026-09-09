# Operating the App Manager

```{warning}
**Draft, and not yet ready for a site to run.** There is no console: what the app manager
has done is reported through a status ConfigMap, an alert, and its pod log. Read the
[trust boundary](#the-trust-boundary) section before enabling it on a site whose
namespaces are not all operator-controlled.
```

A component can ship its own Keycloak client as a
[fragment](../customize/keycloak-fragments.md). The `scout-app-manager` reconciler
discovers every ConfigMap labelled `keycloak.scout.xnat.org/fragment: "true"`, composes
the valid ones into the platform's rendered realm, and applies the result with
keycloak-config-cli. The discovery sidecar calls the reconciler when a fragment changes,
so an edit reaches the realm in a couple of seconds without an operator doing anything.
The same is true of the rendered base realm and of a rotated client credential: the
reconciler watches its own namespace's Secrets and the base realm ConfigMap directly. Those
two it reads *by name*, never by label — the base realm is applied wholesale, so anything
label-selected could become a realm without passing a fragment's rails.
`app_manager_resync_seconds` (default 60) is only a floor, bounding how long a missed
notification — or a realm written by something other than the reconciler, which no
Kubernetes watch can see — can go unnoticed.

## The trust boundary

Discovery *is* the gate. Anyone who can create a labelled ConfigMap in a watched namespace
gets a Keycloak client in the Scout realm, and can grant its roles to every `scout-user`
and `scout-admin`. The composer bounds what a fragment may ask for — `https` redirect URIs
inside the Scout domain, its own client roles only, no protocol mappers, no scope
handling, no adopting a platform-owned client — but within those bounds nothing reviews a
fragment before it takes effect.

So the set of namespaces the app manager watches is the set of namespaces whose writers
you trust with realm configuration. `app_manager_discovery_namespace` defaults to `ALL`;
set it to a comma-separated list to narrow it.

## Fragment states

| State        | Meaning                                                                             |
| ------------ | ----------------------------------------------------------------------------------- |
| `installed`  | Discovered, valid, and composed into the realm.                                     |
| `invalid`    | Failed schema validation. Excluded and logged; every other fragment still composes.  |
| `rejected`   | Valid but not composable — name collision, missing secret, unknown group.            |
| `retracting` | Was installed and has gone missing. See [retraction](#retraction-is-damped).         |

## Deploying the reconciler

It installs as part of `make install-auth`, between Keycloak and oauth2-proxy. It is not
optional: the auth play publishes the base realm document and this is what applies it.

The pod reports Ready only once *this process* has reconciled the realm successfully — not
merely once the realm was applied at some point in the past, which a reconciler that is now
failing on every pass would still be able to claim. On top of that the play waits until the
reconciler reports having applied the exact document it just published. So a base realm
naming a credential that does not resolve, or a wedged apply Job, fails `make install-auth`
rather than leaving it green over stale platform auth.

Fragment outcomes never affect readiness: a rejected fragment is one service's problem, not
the platform's, and a fragment waiting out its retraction grace keeps the pod Ready.

## Looking at what it did

The reconciler publishes a status document after every reconcile. This is the first place
to look, and it does not require reading logs:

```console
$ kubectl get cm scout-app-manager-status -n scout-core -o yaml
```

`phase` is the headline — `Applied`, `Pending`, `Failed`, or one of the two "the realm was
deliberately left alone" phases, `Holding` and `Refused`. Each fragment carries its
`state`, the `contentHash` that was applied, and the reasons if it was excluded.

`Refused` has two causes and `lastResult` says which: a retraction the reconciler will not
act on because discovery never reported a sync, or a credential the realm names that
nothing resolves (see [below](#credentials-are-named-not-carried)).

```console
$ kubectl exec -n scout-core deployment/scout-app-manager -- scout-app-manager status
```

`status` reads that document — it does not reconcile — and adds each fragment's realm
effect recomputed from disk: the clients, their resolved redirect URIs, whether PKCE is
enforced, the roles, and who the grants reach. It also flags a fragment edited since the
last apply. `scout-app-manager reconcile` does write, and exists as break-glass only;
running it puts a second writer on the realm, which is what the single-writer design
exists to prevent.

The pod log carries the same decisions (`INSTALLED`, `EXCLUDED` with the reason, `HOLDING`,
`RETRACTED`, the composed realm's hash, and each config-cli Job's outcome), and
`/metrics` on port 8080 exposes them as
`scout_app_manager_fragments{state=...}`, `scout_app_manager_phase`,
`scout_app_manager_base_realm_applied`, and `scout_app_manager_discovery_synced`. The
**Keycloak Fragment Rejected** alert fires when a fragment has been excluded for ten
minutes, because that component stays deployed and healthy while being unable to
authenticate — nothing else would tell you.

## Retraction is damped

Removing a fragment removes its realm objects, but not immediately. A fragment that
disappears is indistinguishable from one being redeployed, and a chart upgrade that
deletes and recreates its ConfigMap would otherwise retract a live client and kill its
sessions within a second.

So a previously-installed fragment that goes absent enters `retracting` and **keeps its
realm objects**, composed from the copy the reconciler last applied, until it has been gone
for `app_manager_retraction_grace_seconds` (default 300). If it comes back inside that
window, nothing happened at all. Everything else — the base realm, other fragments,
credential rotations — goes on reaching Keycloak meanwhile.

The one absence that does stop the apply is a fragment this pod has never composed, which
after a restart is every fragment that has not been rediscovered yet. There is nothing to
stand in for it, so applying would drop its realm objects; the reconciler reports
`phase: Holding` and leaves the realm alone until it comes back or its grace runs out.

A retraction additionally requires that the discovery sidecar has reported a complete
initial sync. If it has not, nothing is ever retracted — because on a fresh pod an empty
fragment directory is not evidence that anything was deleted, and acting on it would
retract every fragment-created client at once. A fragment held with no composed copy in
that state reports `phase: Refused` rather than `Holding`.

A credential is damped the same way and for the same window: a `secretRef` that stops
resolving — deleted, or the API momentarily refusing — is served from the value last read
rather than rejecting the fragment, because a rejected fragment is one the apply prunes.

## Credentials are named, not carried

Neither the platform's realm nor the composed one holds a client secret. Both write
`$(env:<name>)` and keycloak-config-cli resolves it at import from the job's environment:
the platform's credentials come from the `keycloak-client-secrets` Secret, whose keys are
exactly those names, and each fragment client's from the Secret its `secretRef` points at.
So `keycloak-config-composed` is a ConfigMap you can read, diff and hash freely.

The failure this creates is quiet, which is why the reconciler guards it. config-cli leaves
an unresolvable `$(env:superset)` alone, and Keycloak stores that string as superset's
client secret — a working-looking client that anyone who can read the realm can
authenticate as. So before every apply the reconciler checks each name in the document
resolves to a non-empty value, and refuses the whole apply if one does not:

```text
phase: Refused
lastResult: refusing to apply: the realm names $(env:superset), which
  keycloak-client-secrets does not resolve
```

A *fragment* whose own Secret is missing is only that fragment's problem and is rejected
on its own. A platform client cannot be dropped that way, so its apply stops everything.

Rotating a credential is enough on its own — the reconciler watches each Secret's
`resourceVersion` (`observedSecretsVersion` in the status document) because replacing a
value no longer changes the realm document at all.

## Drift: a realm written by something else

`appliedHash` only says what this reconciler last applied. To notice another writer, the
reconciler reads back the checksum config-cli records on the realm after each import and
compares it with what it saw after its own:

```text
appliedImportChecksum: 9dfdad68...
liveImportChecksum:    ffffffff...
driftDetected:         true
```

A mismatch forces an apply, which puts the realm back. Nothing in a normal Scout deploy
writes the realm any more — that is the point of the reconciler being the only writer — so
drift now means either the [break-glass path](#break-glass-applying-the-base-realm-without-the-reconciler)
below or a hand edit through the admin console. Either way, the next reconcile undoes it.

Two coarser cases count as drift too, and both are repaired the same way: the realm has
been **deleted**, or it exists but carries no import checksum at all, meaning
keycloak-config-cli has never written it. Either sets `realmUnmanaged: true` and makes the
pod unready, because a reconciler with nothing pending would otherwise go on reporting
`Applied` about a document that is no longer in any Keycloak.

Comparing checksums needs two known values, though, and none of this is inferred from a
read that did not land. If Keycloak is unreachable, the admin Secret unreadable, or the
reconciler has not applied since it started,
`scout_app_manager_realm_checksum_readable` goes to 0 and both `driftDetected` and
`realmUnmanaged` stay `false` — that is "not known", not "in step", and it is deliberately
not a reason to re-apply or to go unready.

A rotation wakes the reconciler: it watches its own namespace's Secrets, and a credential
the realm names — the platform's `keycloak-client-secrets` or any Secret a fragment's
`secretRef` points at — reaches Keycloak within a reconcile of being rotated. Drift does
not, and cannot: the live import checksum is a Keycloak read rather than a Kubernetes
event, so a realm written by something else is repaired within
`app_manager_resync_seconds` (default 60).

## Break-glass: applying the base realm without the reconciler

The reconciler being the only writer is a policy, not a structural impossibility. If it is
broken and the platform's own clients have to come back, set in your inventory:

```yaml
keycloak_apply_realm_directly: true
```

and run `make install-auth`. That turns the config-cli Job back on as a Helm hook, so the
play waits for the import and fails if it fails.

Know what you get, because it is not the whole realm:

- **The base realm only.** A client that a component ships as a fragment is not updatable
  while this is the writer — the Job has never seen that fragment.
- **A fragment's client survives, its roles do not.** Both lanes pass
  `import.managed.*=no-delete`, which keeps a fragment's client, its roles, its credential
  and its scope-mappings. It does **not** keep the `scout-user` / `scout-admin` grants on
  those roles: config-cli prunes a group's client-role map even under `no-delete`. So users
  of a fragment app can still log in and will have no permissions, which usually surfaces
  as a 403 with nothing in any log.

Both are temporary. Once the reconciler is healthy it sees the import checksum has moved,
re-applies, and restores the grants — measured at 32s from the break-glass Job finishing.
**Set the flag back to `false` once the incident is over**, or the next `make install-auth`
will do the same thing again.

---

**Still to build, before this is a real runbook:**

- Whether the platform wants a narrower default than `ALL` for discovery.
- Moving Scout's own clients out of the base realm and into fragments, one at a time.
