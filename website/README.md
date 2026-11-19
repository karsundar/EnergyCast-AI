# EnergyCast AI technical blog

This directory contains the Jekyll source for the EnergyCast AI engineering
journal. GitHub Pages is expected at:

<https://karsundar.github.io/EnergyCast-AI/>

The site uses the repository base URL `/EnergyCast-AI`; use Jekyll's
`relative_url` filter for internal links and assets.

## Preview locally

Ruby is intentionally separate from the project's Python and ROCm environments.
With Ruby 3.x and Bundler installed:

```bash
cd /home/karthiks/src/EnergyCast-AI/website
bundle config set --local path vendor/bundle
bundle install
bundle exec jekyll serve --baseurl /EnergyCast-AI
```

Open <http://127.0.0.1:4000/EnergyCast-AI/>.

## Build and validate

```bash
cd /home/karthiks/src/EnergyCast-AI/website
JEKYLL_ENV=production bundle exec jekyll build --baseurl /EnergyCast-AI
ruby scripts/validate_site.rb _site /EnergyCast-AI
```

The validation script checks required generated pages, internal links, image
references, unresolved Liquid tags, repository-base-URL handling, and the three
SVG diagrams.

## Add a post

1. Create `_posts/YYYY-MM-DD-slug.md`.
2. Add YAML front matter with `title`, `description`, `author`, `date`, and
   `reading_time`.
3. Reference assets with `{{ '/assets/path' | relative_url }}`.
4. Build and run the local validator before committing.

## GitHub Pages deployment

The workflow at `../.github/workflows/pages.yml` is manual-only. It checks out
the repository, installs the pinned GitHub Pages dependency set, builds from
this directory, validates the static output, uploads the Pages artifact, and
deploys it through the protected `github-pages` environment.

Repository setup after review:

1. Open **Settings → Pages** in `karsundar/EnergyCast-AI`.
2. Under **Build and deployment**, select **GitHub Actions** as the source.
3. Optionally protect the `github-pages` environment with required reviewers.
4. Open **Actions → Build and deploy EnergyCast AI blog**.
5. Choose **Run workflow** on the `main` branch.
6. Confirm the deployment URL shown by the workflow.

The workflow has no `push` or `pull_request` trigger, so preparing or merging
site changes does not publish automatically.
