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
`app_manager_resync_seconds` (default 600) is only a floor, bounding how long a missed
notification can go unnoticed.

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

Start in `diff` mode with no fragments deployed. The composed realm must come out
byte-identical to the platform's own rendered realm — the cheapest proof that composition
is not quietly rewriting the base. Switch to `apply` once that holds.

Once in `apply` mode the pod reports Ready only after the realm has been applied at least
once, and `make install-app-manager` waits for that. A failed first apply therefore fails
the deploy rather than leaving a running pod that has changed nothing. Fragment outcomes
never affect readiness: a rejected fragment is one service's problem, not the platform's.

## Looking at what it did

The reconciler publishes a status document after every reconcile. This is the first place
to look, and it does not require reading logs:

```console
$ kubectl get cm scout-app-manager-status -n scout-core -o yaml
```

`phase` is the headline — `Applied`, `Pending`, `Failed`, or one of the two "the realm was
deliberately left alone" phases, `Holding` and `Refused`. Each fragment carries its
`state`, the `contentHash` that was applied, and the reasons if it was excluded.

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

So a previously-installed fragment that goes absent enters `retracting` and **suppresses
the apply entirely** (`phase: Holding`) until it has been gone for
`app_manager_retraction_grace_seconds` (default 300). If it comes back inside that window,
nothing happened at all. The cost is that an unrelated fragment installed during a hold
waits for it.

A retraction additionally requires that the discovery sidecar has reported a complete
initial sync. If it has not, the reconciler holds indefinitely and reports
`phase: Refused` — because on a fresh pod an empty fragment directory is not evidence that
anything was deleted, and acting on it would retract every fragment-created client at once.

---

**Still to build, before this is a real runbook:**

- Whether the platform wants a narrower default than `ALL` for discovery.
- Drift detection: nothing yet notices a realm changed outside the reconciler.
- An ADR, and a link to it from here.
