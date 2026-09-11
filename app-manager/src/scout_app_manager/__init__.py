"""The Keycloak realm reconciler, and the fragment vocabulary it reads.

Where things live, because it is the question a reader asks first and there is
one answer: **a type lives in the module that defines its meaning, and that is
the module that produces it.** There is no module of models in the middle.

- `schema` -- what a fragment may say. The external contract, and the security
  boundary: the set of things a component can express is exactly the set
  enumerated there, so widening it is a reviewable event.
- `load` -- what was found on disk, with the provenance the cluster attested.
- `compose` -- what merging those into the base realm produces: the realm, the
  per-client effect, and the credential bindings the apply Job needs.
- `placeholders` -- the `$(env:...)` variables the realm names but does not
  carry: minting them, finding them, and proving each one resolves.
- `status` -- what the reconciler reports and remembers across a restart. It is
  `service` that fills a `State` in, but every other module reads one, so the
  type belongs with the document it is rendered to rather than with the writer.
- `settings`, `k8s`, `keycloak` -- configuration, and the two things outside
  this process, each owning the errors it raises.

Everything else is behaviour with no vocabulary of its own: `service` decides,
`apply` runs the import, `loop` schedules, `watch` and `reload` ring doorbells,
`health` answers probes, `cli` prints, `shutdown` ends it.
"""

from .schema import API_VERSION, FRAGMENT_LABEL, FRAGMENT_LABEL_VALUE, KIND

__all__ = ["API_VERSION", "FRAGMENT_LABEL", "FRAGMENT_LABEL_VALUE", "KIND"]
