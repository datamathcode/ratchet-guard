# CI

`github-actions.yml` is a working GitHub Actions workflow, kept here rather than
in `.github/workflows/` because the OAuth app used to create this repository was
not granted the `workflow` scope, and GitHub rejects any push that adds a file
under that path.

To enable it, either:

```bash
# grant the scope, then move the file into place
gh auth refresh -s workflow
mkdir -p .github/workflows && git mv ci/github-actions.yml .github/workflows/ci.yml
git commit -m "ci: enable GitHub Actions" && git push
```

or copy the file into place through the GitHub web UI.

Everything the workflow runs can be run locally right now:

```bash
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python examples/toy-metric-blindness/run_demo.py
```
