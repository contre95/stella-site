# Stella site

The website for [Stella](https://github.com/contre95/stella):

- **Docs**: [stella-docs.contre.io](https://stella-docs.contre.io), a Hugo ([Doks](https://getdoks.org)) site.
  The pages come from the `docs/` folder of the stella repo; this repo only holds the theme,
  layouts and homepage.
- **Demo**: [stella.contre.io](https://stella.contre.io), a read-only static
  snapshot of a real Stella instance, built by `demo/build.sh`.

Both are built from the latest stella release by `.github/workflows/site.yaml` (on push,
on demand, and daily when a new release is out). The workflow publishes each build as the
single commit of a branch, and git-connected Cloudflare Pages projects deploy those branches
as-is (no build command):

| Branch | Cloudflare Pages project | Domain |
| --- | --- | --- |
| `build/docs` | `stella-docs` | stella-docs.contre.io |
| `build/demo` | `stella-demo` | stella.contre.io (`/docs/*` redirects to the docs) |

## Layout

| Path | What |
| --- | --- |
| `content/` | Homepage, license and privacy pages |
| `layouts/`, `assets/`, `static/`, `config/` | Theme overrides and Hugo config |
| `scripts/fetch-stella.sh` | Puts a stella checkout at `.stella/` (latest release, a given ref, or a local working copy) |
| `scripts/publish-branch.sh` | Force-pushes a built folder as the only commit of a `build/*` branch |
| `demo/` | Demo builder: deck content, history generator, seeding, crawler and the browser shim |

`config/_default/module.toml` mounts `.stella/docs` as `content/docs` (minus `skills/`), and
`layouts/_markup/render-link.html` turns the docs' GitHub-style relative links
(`config.md`, `../auth.md#multi-user`) into site URLs.

## Demo

`demo/build.sh` runs these steps against the stella checkout:

1. `demo/gen` (Go) turns `demo/decks.json` into deck exports. Studied decks go through weeks of
   simulated daily sessions with Stella's own scheduler (`src/srs`), so the dashboard, streak and
   due counts are real. Deck covers and flags are drawn by the generator.
2. A Stella instance starts with `demo/config.yaml`, and `demo/seed.py` imports the decks, then
   sets groups and tags over MCP.
3. `demo/crawl.py` saves every page and HTMX fragment as static files, and `demo/static-demo.js`
   points HTMX at them in the browser and blocks anything that would change state.

To change the demo content, edit `demo/decks.json`.

## Local development

```sh
npm ci

# Docs against your stella working copy (or omit STELLA_DIR for the latest release)
STELLA_DIR=../stella scripts/fetch-stella.sh
npm run dev  # needs Hugo extended 0.148.x

# Demo (needs go, curl and python3)
STELLA_DIR=../stella demo/build.sh
cd dist-demo && python3 -m http.server 8000
```

Serve the demo over HTTP (not `file://`): its pages use root-relative paths and HTMX requests.
