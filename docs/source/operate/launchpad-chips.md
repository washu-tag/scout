# Put Your Service on the Scout Launchpad

The launchpad renders its tiles ("chips") and sections ("groups") from catalog documents
it discovers at runtime. To put a service on the page, publish a ConfigMap — in your
service's own namespace — labelled `scout.washu.edu/launchpad-apps: "1"`. The launchpad
imports it within seconds. No launchpad configuration, rebuild, or restart is involved,
and deleting the ConfigMap removes the chip.

This works the same for Scout's own components, for site-installed services, and for
anything else running in the cluster: presence on the landing page follows
installation.

## The 30-second version

```yaml
# yaml-language-server: $schema=https://raw.githubusercontent.com/washu-tag/scout/main/docs/source/technical/launchpad-catalog.v1alpha1.schema.json
apiVersion: v1
kind: ConfigMap
metadata:
  name: my-service-launchpad # convention: <component>-launchpad
  namespace: my-service
  labels:
    scout.washu.edu/launchpad-apps: '1'
data:
  apps.yaml: |
    apiVersion: scout.washu.edu/v1alpha1
    chips:
      - id: my-service
        title: My Service
        description: One line about what it does
        icon: beaker
        tone: violet
        link: { subdomain: my-service }
        group: more
```

`kubectl apply` that and a "My Service" card appears on the launchpad (in a synthesized
"More" section, since the `more` group isn't otherwise defined — see
[groups](#defining-a-group-section) to control the section).

Ship it however you deploy: a template in your Helm chart (so uninstalling the chart
removes the chip), a Flux-reconciled manifest, or — for Scout-internal roles — the
`scout_common` `launchpad_catalog` task. The schema comment on the first line gives you
editor validation and completion; the same
[JSON Schema](launchpad-catalog.v1alpha1.schema.json) is what the launchpad's validator
enforces, generated from the same source.

## The document

Each `data` key in the ConfigMap holds one YAML **catalog document**:

```yaml
apiVersion: scout.washu.edu/v1alpha1 # required, exactly this value
chips: [] # tiles
groups: [] # section definitions (only when introducing a section)
```

A document with any other `apiVersion` is skipped. Unknown fields inside a known
version are ignored (with a logged warning), so a newer document still renders its
basics on an older launchpad.

## Chip fields

| Field            | Required | Default   | Notes                                                                                                                                           |
| ---------------- | -------- | --------- | ----------------------------------------------------------------------------------------------------------------------------------------------- |
| `id`             | yes      | —         | Lowercase DNS-label-style slug (`[a-z0-9-]`, max 63). Duplicate ids within one document reject the later chip.                                   |
| `title`          | yes      | —         | Max 60 characters (longer is truncated).                                                                                                         |
| `link`           | yes      | —         | Exactly one destination — see [links](#links).                                                                                                   |
| `description`    | no       | `""`      | One line, max 200 characters.                                                                                                                    |
| `icon`           | no       | `app`     | A name from the [icon registry](#icons-and-tones).                                                                                               |
| `iconData`       | no       | —         | `data:image/(png\|jpeg\|svg+xml);base64,…`, max 16 KiB. Wins over `icon`.                                                                        |
| `tone`           | no       | `indigo`  | One of `indigo emerald amber violet rose cyan red orange slate`.                                                                                 |
| `newTab`         | no       | `true`    | Open the destination in a new tab.                                                                                                               |
| `group`          | no       | `more`    | Which section the chip renders in.                                                                                                               |
| `weight`         | no       | `100`     | Lower renders first; ties break by title, then id.                                                                                               |
| `audience`       | no       | `user`    | `user` (any authenticated user) or `admin`. This is display filtering only — your service must still enforce its own access.                     |
| `enabled`        | no       | `true`    | Set `false` to hide a shipped chip without deleting the ConfigMap (useful when your chart gates the chip on a value).                            |

Invalid values in the optional presentation fields fall back to their defaults (the
chip still renders, and the problem is reported — see
[troubleshooting](#troubleshooting)). A chip without a valid `id`, `title`, and `link`
is skipped entirely.

### Links

A link is exactly one destination, in one of three shapes:

```yaml
link: { subdomain: my-service } # https://my-service.<scout-host>
link: { subdomain: temporal, path: /auth/sso } # subdomain plus a path suffix
link: { path: /admin/users } # same-origin path on the launchpad host
link: { url: https://example.com/docs } # absolute http(s) URL
```

You state a subdomain, never a hostname — the launchpad resolves it against the host
the browser is already on. Path suffixes matter for services with their own login
bootstrap (Temporal's `/auth/sso` is the canonical example: the bare subdomain lands on
an unauthenticated UI). Only `http(s)` URLs, rooted paths, and DNS-label subdomains
validate; anything else (e.g. `javascript:`) rejects the whole chip.

## Defining a group (section)

Chips reference a group id; groups render as the page's titled sections. Reference an
undefined group and the launchpad synthesizes one from the id — fine for a first pass.
Define the group when you want to control its header, position, and layout:

```yaml
groups:
  - id: imaging
    title: Imaging
    description: Imaging platforms and data retrieval
    icon: photograph
    weight: 15 # Scout's own sections sit at 10 (core) and 30 (admin)
    layout: cards
```

| Field         | Required | Default                        | Notes                                                                                              |
| ------------- | -------- | ------------------------------ | -------------------------------------------------------------------------------------------------- |
| `id`          | yes      | —                              | What chips reference.                                                                               |
| `title`       | yes      | —                              | Section heading, max 60 characters.                                                                 |
| `description` | no       | `""`                           | Section subtitle, max 200 characters.                                                               |
| `icon`        | no       | `folder`                       | Small icon beside the heading.                                                                      |
| `weight`      | no       | `100`                          | Section order on the page.                                                                          |
| `layout`      | no       | `cards`                        | `cards` (large), `rows` (compact list with arrows), or `tiles` (compact grid).                      |
| `maxColumns`  | no       | `cards` 3, `tiles` 2, `rows` 1 | Rendered columns = `min(visible chips, maxColumns)`.                                                |
| `width`       | no       | `full`                         | Consecutive `half` groups pair side by side; an unpaired `half` renders full.                       |
| `audience`    | no       | `user`                         | A group also hides automatically whenever it has no visible chips.                                  |
| `footerLink`  | no       | —                              | `{text, url}` rendered as an inline link under the group's grid.                                   |

Several documents may define the same group id (two services can both put chips in
`imaging` and both ship the definition). The launchpad's own catalog wins a definition
conflict, then the lowest `weight`, deterministically; chips from all sources land in
the group regardless.

## Icons and tones

Icons come from a curated registry bundled into the launchpad image
(`launchpad/src/lib/catalog/icons.tsx`) — inline SVGs, no network fetches, so the page
works air-gapped. The generic glyphs are
[Heroicons](https://github.com/tailwindlabs/heroicons) v1 (MIT) and the brand marks are
[Simple Icons](https://simpleicons.org/) (CC0), both shipped via the
[react-icons](https://react-icons.github.io/react-icons/) package — but the *names*
below are Scout's own, so use this gallery rather than the upstream sites. It is
generated from the registry itself (`npm run docs-assets` in `launchpad/`), so what you
see is exactly what renders:

<!-- generated:icons:start — npm run docs-assets in launchpad/ -->

| Icon | Name | Icon | Name | Icon | Name |
| --- | --- | --- | --- | --- | --- |
| ![academic-cap](../images/launchpad/icons/academic-cap.svg) | `academic-cap` | ![app](../images/launchpad/icons/app.svg) | `app` | ![archive](../images/launchpad/icons/archive.svg) | `archive` |
| ![beaker](../images/launchpad/icons/beaker.svg) | `beaker` | ![bell](../images/launchpad/icons/bell.svg) | `bell` | ![book-open](../images/launchpad/icons/book-open.svg) | `book-open` |
| ![calendar](../images/launchpad/icons/calendar.svg) | `calendar` | ![chart](../images/launchpad/icons/chart.svg) | `chart` | ![chart-bar](../images/launchpad/icons/chart-bar.svg) | `chart-bar` |
| ![chat](../images/launchpad/icons/chat.svg) | `chat` | ![clipboard-check](../images/launchpad/icons/clipboard-check.svg) | `clipboard-check` | ![clock](../images/launchpad/icons/clock.svg) | `clock` |
| ![cloud](../images/launchpad/icons/cloud.svg) | `cloud` | ![cog](../images/launchpad/icons/cog.svg) | `cog` | ![collection](../images/launchpad/icons/collection.svg) | `collection` |
| ![cube](../images/launchpad/icons/cube.svg) | `cube` | ![database](../images/launchpad/icons/database.svg) | `database` | ![docker](../images/launchpad/icons/docker.svg) | `docker` |
| ![document-text](../images/launchpad/icons/document-text.svg) | `document-text` | ![download](../images/launchpad/icons/download.svg) | `download` | ![external-link](../images/launchpad/icons/external-link.svg) | `external-link` |
| ![eye](../images/launchpad/icons/eye.svg) | `eye` | ![film](../images/launchpad/icons/film.svg) | `film` | ![folder](../images/launchpad/icons/folder.svg) | `folder` |
| ![github](../images/launchpad/icons/github.svg) | `github` | ![globe](../images/launchpad/icons/globe.svg) | `globe` | ![grafana](../images/launchpad/icons/grafana.svg) | `grafana` |
| ![heart](../images/launchpad/icons/heart.svg) | `heart` | ![jupyter](../images/launchpad/icons/jupyter.svg) | `jupyter` | ![key](../images/launchpad/icons/key.svg) | `key` |
| ![kubernetes](../images/launchpad/icons/kubernetes.svg) | `kubernetes` | ![lightning-bolt](../images/launchpad/icons/lightning-bolt.svg) | `lightning-bolt` | ![link](../images/launchpad/icons/link.svg) | `link` |
| ![mail](../images/launchpad/icons/mail.svg) | `mail` | ![minio](../images/launchpad/icons/minio.svg) | `minio` | ![photograph](../images/launchpad/icons/photograph.svg) | `photograph` |
| ![postgresql](../images/launchpad/icons/postgresql.svg) | `postgresql` | ![puzzle](../images/launchpad/icons/puzzle.svg) | `puzzle` | ![python](../images/launchpad/icons/python.svg) | `python` |
| ![search](../images/launchpad/icons/search.svg) | `search` | ![server](../images/launchpad/icons/server.svg) | `server` | ![shield-check](../images/launchpad/icons/shield-check.svg) | `shield-check` |
| ![sparkles](../images/launchpad/icons/sparkles.svg) | `sparkles` | ![table](../images/launchpad/icons/table.svg) | `table` | ![temporal](../images/launchpad/icons/temporal.svg) | `temporal` |
| ![terminal](../images/launchpad/icons/terminal.svg) | `terminal` | ![upload](../images/launchpad/icons/upload.svg) | `upload` | ![user-group](../images/launchpad/icons/user-group.svg) | `user-group` |

<!-- generated:icons:end -->

Need a logo the registry lacks? Embed it as `iconData` (a base64 image data URI, 16 KiB
max) — or open a PR adding a name to
`launchpad/src/lib/catalog/icons.tsx`. The published JSON Schema always carries the
authoritative name list as the `icon` enum.

Tones are named bundles of coordinated light- and dark-mode styling defined in
`launchpad/src/lib/catalog/tones.ts` over the
[Tailwind CSS default palette](https://tailwindcss.com/docs/colors). A chip's tone
drives its icon chip, hover border, and accents together — which is what keeps every
chip readable in both themes; there is no custom-color escape hatch. Each tone's
swatches show the icon-chip treatment (background, border, icon color) on a light card
and on a dark one — both halves ship in the same bundle, and the dark tokens' alpha is
composited over the real dark card surface, exactly as the browser renders it:

<!-- generated:tones:start — npm run docs-assets in launchpad/ -->

| Light | Dark | Name | Background | Icon color |
| --- | --- | --- | --- | --- |
| ![indigo light](../images/launchpad/tones/indigo.svg) | ![indigo dark](../images/launchpad/tones/indigo-dark.svg) | `indigo` | `bg-indigo-50` `dark:bg-indigo-950/40` | `text-indigo-600` `dark:text-indigo-400` |
| ![emerald light](../images/launchpad/tones/emerald.svg) | ![emerald dark](../images/launchpad/tones/emerald-dark.svg) | `emerald` | `bg-emerald-50` `dark:bg-emerald-950/40` | `text-emerald-600` `dark:text-emerald-400` |
| ![amber light](../images/launchpad/tones/amber.svg) | ![amber dark](../images/launchpad/tones/amber-dark.svg) | `amber` | `bg-amber-50` `dark:bg-amber-950/40` | `text-amber-600` `dark:text-amber-400` |
| ![violet light](../images/launchpad/tones/violet.svg) | ![violet dark](../images/launchpad/tones/violet-dark.svg) | `violet` | `bg-violet-50` `dark:bg-violet-950/40` | `text-violet-600` `dark:text-violet-400` |
| ![rose light](../images/launchpad/tones/rose.svg) | ![rose dark](../images/launchpad/tones/rose-dark.svg) | `rose` | `bg-rose-50` `dark:bg-rose-950/40` | `text-rose-600` `dark:text-rose-400` |
| ![cyan light](../images/launchpad/tones/cyan.svg) | ![cyan dark](../images/launchpad/tones/cyan-dark.svg) | `cyan` | `bg-cyan-50` `dark:bg-cyan-950/40` | `text-cyan-600` `dark:text-cyan-400` |
| ![red light](../images/launchpad/tones/red.svg) | ![red dark](../images/launchpad/tones/red-dark.svg) | `red` | `bg-red-50` `dark:bg-red-950/40` | `text-red-600` `dark:text-red-400` |
| ![orange light](../images/launchpad/tones/orange.svg) | ![orange dark](../images/launchpad/tones/orange-dark.svg) | `orange` | `bg-orange-50` `dark:bg-orange-950/40` | `text-orange-500` `dark:text-orange-400` |
| ![slate light](../images/launchpad/tones/slate.svg) | ![slate dark](../images/launchpad/tones/slate-dark.svg) | `slate` | `bg-slate-100` `dark:bg-slate-800` | `text-slate-600` `dark:text-slate-300` |

<!-- generated:tones:end -->

## Troubleshooting

The launchpad never lets one bad document break the page — it renders everything valid
and reports the rest. Checks, in the order problems actually occur:

1. **No label, no chip.** The ConfigMap must carry
   `scout.washu.edu/launchpad-apps: "1"` exactly.
2. **YAML syntax error** — the whole document is skipped and the diagnostic includes
   line and column.
3. **Wrong `apiVersion`** — the document is skipped.
4. **Chip missing `id`/`title`/valid `link`** — that chip is skipped.
5. **Bad optional field** (unknown icon or tone, overlong text) — the chip renders
   with defaults and the coercion is reported.

Where diagnostics appear, most convenient first:

- **On the page**: launchpad admins (`launchpad-admin` role) see a "catalog entries
  reported problems" panel listing every skip and coercion with its source.
- **Launchpad logs**: `kubectl logs -n scout-core deploy/launchpad -c launchpad`
  (each line prefixed `[catalog]`) — also queryable in Grafana → Explore → Loki.
- Expect up to ~10 seconds of propagation after `kubectl apply` (sidecar write plus the
  launchpad's snapshot refresh).

Removal is deletion: uninstall whatever shipped the ConfigMap (or
`kubectl delete configmap <name>`) and the chip disappears. If your chip is created by
an Ansible role rather than a chart, deleting it on decommission is your role's
responsibility.

## Reference

- Machine-readable schema: [launchpad-catalog.v1alpha1.schema.json](launchpad-catalog.v1alpha1.schema.json)
  (generated from the validator's own definitions — they cannot disagree).
- Design and rationale: ADR 0034 in the Scout repository
  (`docs/internal/adr/0034-runtime-configurable-launchpad-catalog.md`).
