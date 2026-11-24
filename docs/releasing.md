# Releasing

For the maintainer.  Nobody else, and no agent, changes the version or
tags ([decision 0013](decisions/0013-versions-and-releases.md)).

The version lives in `pyproject.toml` and, as uv records it, `uv.lock`.
Between releases it is the next patch as a dev release (`0.2.1.dev0`); at
a release commit it is exactly the tag.
[convco](https://github.com/convco/convco) (`cargo install --locked
convco`) computes the next version from the Conventional Commits since
the last tag.

```sh
task next-version:show                          # what the next release would be
VERSION_OVERRIDE=v0.3.0 task next-version:show  # or choose it
```

On an up-to-date, clean `main`:

```sh
task release:commits-and-tag                    # or VERSION_OVERRIDE=vX.Y.Z task ...
```

That commits the release version, makes a signed tag (an editor opens for
its message), and commits the next dev version.  It never pushes.  Review,
then:

```sh
git push origin main vX.Y.Z
```

The tag push runs `.github/workflows/release.yaml`: it checks the tag
against `pyproject.toml`, runs the tests, drafts a GitHub release and
publishes to PyPI.
