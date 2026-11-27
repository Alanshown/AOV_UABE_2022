# Build and verify AOV UABE 2022 2.5

Build from the **final, reviewed integration commit**, not the packaging
commit alone if further code changes were integrated after it. Use an exact
40-character lowercase Git commit SHA and a fresh clean checkout. Record that
same SHA in every artifact and in any eventual release tag. This workflow only
builds and uploads temporary CI artifacts; it does not create tags, merge a
branch, or publish a release.

## GitHub Actions, no release publication

1. After all local checks and with permission to push a candidate, put the
   exact commit on the repository's `release/2.5` branch. A push to that branch
   starts `.github/workflows/aov-2-5-build.yml` without changing the default
   branch or an existing tag. Confirm the run's head SHA is the reviewed SHA.
2. If this workflow is already present on the default branch, an explicitly
   authorized manual rerun can use
   `gh workflow run aov-2-5-build.yml --ref release/2.5 -f commit=<full-sha>`.
   The supplied SHA must match the selected ref's event commit. GitHub requires
   workflow dispatch definitions to exist on the default branch; use the
   candidate branch push trigger for the first run instead.
3. Wait for both native jobs. Download their artifacts for the matching SHA.
   Check each `SOURCE-*.txt`, recompute the individual binaries’ SHA-256 and
   compare with each job’s `SHA256SUMS.txt`. Verify the Windows window capture,
   upgrade log and Linux installed-window report. Do not treat a source-only
   pytest pass or a generated-but-uninstalled binary as this gate.
4. For a later approved release, combine only the two checksum lines for
   the new binaries into one release checksum file; publish the exact
   artifacts once native validation is complete. Do not modify
   `latest2.4`, replace its assets, or move an existing release tag.

## Native local build

On a clean supported Windows x64 runner with Python 3.12 x64, Inno Setup 6
and Git, run from a verified checkout:

```powershell
git switch --detach <full-sha>
./packaging/windows/build.ps1 -ExpectedCommit <full-sha>
./packaging/windows/verify-install.ps1
```

The installation verification requires a disposable clean Windows user
profile: it executes the public 2.3.6 and 2.4 installers after validating their
SHA-256, checks custom-path upgrades, one uninstall registration per identity,
Start Menu/Desktop shortcuts, retained user data, side-by-side preservation,
and captures the actual 2.5 window. Don't run it on a workstation containing
an AOV UABE installation.

Use Debian 13 amd64 with system Python 3.13, Tk 8.6 and the dependencies
listed in the Linux job configuration:

```sh
git switch --detach <full-sha>
python3 -m venv .release-venv
. .release-venv/bin/activate
sh packaging/linux/build.sh <full-sha>
# Only on a disposable Debian 13 system:
sudo apt install ./artifacts/AOV_UABE_2022-2.5-Linux-x64.deb
sh packaging/linux/verify-install.sh
```

The .deb uses package version `2.5-1` with app display version `2.5` and
stable install paths; package version checks require it to sort above
the previous `2.4-2`. A fresh install and launch smoke do not by themselves
verify every Linux desktop, package manager, 3D preview, or input bundle.

See [GitHub manual workflow rules](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/manually-run-a-workflow)
and [branch push and dispatch events](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows).
