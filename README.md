# Stella site

The docs site for [Stella](https://github.com/contre95/stella), at
[stella.contre.io](https://stella.contre.io): a Hugo ([Doks](https://getdoks.org)) site.
The pages come from the `docs/` folder of the stella repo; this repo only holds the theme,
layouts and homepage.

The docs are built from the latest stella release by `.github/workflows/site.yaml` (on push,
on demand, and daily when a new release is out). The workflow publishes the build as the
single commit of a branch, and a git-connected Cloudflare Pages project deploys that branch
as-is (no build command):

| Branch | Cloudflare Pages project | Domain |
| --- | --- | --- |
| `build/docs` | `stella-docs` | stella.contre.io |

## Layout

| Path | What |
| --- | --- |
| `content/` | Homepage, license and privacy pages |
| `layouts/`, `assets/`, `static/`, `config/` | Theme overrides and Hugo config |
| `data/sidebar.toml` | Docs sidebar icons, per page |
| `scripts/fetch-stella.sh` | Puts a stella checkout at `.stella/` (latest release, a given ref, or a local working copy) |
| `scripts/publish-branch.sh` | Force-pushes a built folder as the only commit of a `build/*` branch |

`config/_default/module.toml` mounts `.stella/docs` as `content/docs` (minus `skills/`), and
`layouts/_markup/render-link.html` turns the docs' GitHub-style relative links
(`config.md`, `../auth.md#multi-user`) into site URLs.

## Local development

```sh
npm ci

# Docs against your stella working copy (or omit STELLA_DIR for the latest release)
STELLA_DIR=../stella scripts/fetch-stella.sh
npm run dev  # needs Hugo extended 0.148.x
```
