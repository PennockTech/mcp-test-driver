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
then push `main` first and check the release would build before pushing
the tag:

```sh
git push origin main
task release:check      # then `gh run watch`; wait for it to pass
git push origin vX.Y.Z
```

The tag push runs `.github/workflows/release.yaml`: it checks the tag
against `pyproject.toml`, runs the tests, drafts a GitHub release and
publishes to PyPI.  Its build half is `release-build.yaml`, which
`release-check.yaml` also runs, by hand only, publishing nothing and
skipping only the tag comparison.  Green CI is not enough on its own: the
runner image and the Python that `setup-python` picks change between
releases with no commit here.  v0.2.0 failed that way, on a Python newer
than any `gnureadline` wheel.  `task release:check -- REF` checks another
branch.  GitHub only offers manual runs for workflows on the default
branch.
