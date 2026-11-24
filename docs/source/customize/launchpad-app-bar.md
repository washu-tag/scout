# The App Bar

An app that opens from a launchpad tile starts every page with the same bar, so people
can always see which app they are in, where they are in it, and where its tools are.
This page is the standard for that bar and for where the page's content begins. The
launchpad's own pages follow it: **Admin Tools / Users** is one.

The standard is for apps built for Scout. Third-party tools that a chip opens, such as a
notebook server or a dashboard tool, keep their own headers.

## Colors

The rules below name these colors:

| Name           | Color     | Used for                                    |
| -------------- | --------- | ------------------------------------------- |
| Surface        | `#FFFFFF` | The bar's background                        |
| Line           | `#E5E7EB` | The bar's bottom border and button borders  |
| Ink            | `#111827` | The current page's name                     |
| Secondary ink  | `#4B5563` | Earlier steps of the path                   |
| Separator      | `#9CA3AF` | The `/` between steps                       |
| Primary        | `#4F46E5` | The main action's button                    |
| Page ground    | `#F8FAFC` | The background behind the content           |

In dark mode, use your app's own dark equivalents.

## The bar

- One bar at the top of every page: 56px tall, the full width of the page, on the
  surface color, with a 1px bottom border in the line color.
- 32px of padding on the left and right. Everything in the bar sits in one row,
  vertically centered, 10px apart.

### Left: where you are

- First, a 28x28px tile with 8px rounded corners. Its background is your chip's tone,
  and it shows your chip's icon at 16px in the tone's icon color. Take both from the
  `icon` and `tone` your chip sets (see [icons and tones](launchpad-chips.md#icons-and-tones)),
  so the bar matches the tile people clicked.
- Then the app's name, at 15px. On the app's home page the name is the current page, so
  it is styled as the current page below.
- On any other page, the path to it, one step per page: `App name / Step / Step`. Every
  step but the last is weight 500 in secondary ink and links to its page. The `/`
  separators are in the separator color. The last step is the current page, weight 600
  in ink.

### Right: tools, then the main action

- Tools that apply across the app come first, as outline buttons: 34px tall, 8px rounded
  corners, a 1px border in the line color, text at 13.5px and weight 500. A tool that
  shows only an icon is 34x34px and has an `aria-label`.
- A link that leaves the app, such as **Open in Keycloak**, is an outline button with an
  external-link icon.
- Last, the page's main action, if it has one: a button filled with the primary color,
  text at 13.5px and weight 600.

## The content

- No heading that repeats the app's name or the current page, and no description
  paragraph under the bar. A page about one object, such as a single study or request,
  keeps that object's own title in its content.
- The content starts right below the bar, with 24px of padding above it and 32px on
  each side, so its left edge lines up with the tile.

## Phone width

When the page is 640px wide or narrower:

- The bar stays one row.
- The app's name and the path are cut short with an ellipsis when they don't fit.
- Tools may drop their text and show only their icon.
- The main action stays.

## Reference HTML and CSS

A bar for the **Reviews** page of an app called **My App**, with the indigo tone:

```html
<header class="app-bar">
  <span class="app-bar-tile"><!-- your chip's icon, inline SVG --></span>
  <nav class="app-bar-path" aria-label="Breadcrumb">
    <a href="/">My App</a>
    <span aria-hidden="true">/</span>
    <h1>Reviews</h1>
  </nav>
  <div class="app-bar-tools">
    <a class="app-bar-tool" href="https://docs.example.edu"
       target="_blank" rel="noopener noreferrer">
      <!-- external-link icon --> Documentation
    </a>
    <button class="app-bar-action" type="button">New review</button>
  </div>
</header>
<main class="app-content">...</main>
```

```css
.app-bar {
  display: flex; align-items: center; gap: 10px;
  height: 56px; padding: 0 32px; box-sizing: border-box;
  background: #ffffff; border-bottom: 1px solid #e5e7eb;
}
.app-bar-tile {
  flex: none; display: grid; place-items: center;
  width: 28px; height: 28px; border-radius: 8px;
  background: #eef2ff; color: #4f46e5; /* the indigo tone */
}
.app-bar-tile svg { width: 16px; height: 16px; }
.app-bar-path {
  display: flex; align-items: center; gap: 10px;
  min-width: 0; font-size: 15px;
}
.app-bar-path > * {
  margin: 0; font-size: inherit; white-space: nowrap;
  overflow: hidden; text-overflow: ellipsis;
}
.app-bar-path a {
  font-weight: 500; color: #4b5563; text-decoration: none;
}
.app-bar-path [aria-hidden] { flex: none; color: #9ca3af; }
.app-bar-path h1 { font-weight: 600; color: #111827; }
.app-bar-tools {
  display: flex; align-items: center; gap: 10px;
  flex: none; margin-left: auto;
}
.app-bar-tool, .app-bar-action {
  display: inline-flex; align-items: center; gap: 6px;
  height: 34px; padding: 0 12px; box-sizing: border-box;
  border-radius: 8px; font: inherit; font-size: 13.5px;
  text-decoration: none; cursor: pointer;
}
.app-bar-tool {
  border: 1px solid #e5e7eb; background: #ffffff;
  color: #111827; font-weight: 500;
}
.app-bar-action {
  border: 1px solid #4f46e5; background: #4f46e5;
  color: #ffffff; font-weight: 600;
}
.app-content { padding: 24px 32px; background: #f8fafc; }
```

The launchpad's own pages use the `AppBar` component in
`launchpad/src/components/shell/AppBar.tsx`.
