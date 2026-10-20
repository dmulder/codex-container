# Wrapper validation

## Offline process tests

Run from this checkout:

```sh
python3 -m unittest discover -s tests -v
bash -n codex
```

The suite copies only the single-file launcher into an isolated installation, supplies its own
`.packages`, `.files`, homes and cache, and runs a fake Podman executable. It
exercises real pipes, terminals, locks, and signals, including killing the launcher
while an independently running simulated container remains alive. It does not
build an image, authenticate, or call an AI API. The double verifies the command
boundary; it cannot establish Podman's actual mount or namespace behavior.
It also checks configuration lookup through symlinks, the embedded image recipe,
and image reuse or rebuilding when package configuration changes.

One additional test loads the unchanged Himmelblau PR #1880 helper when
`CODEX_VET_HELPER` points to a local copy. Download the pinned reference into a
temporary directory or use an existing checkout:

```sh
reference_dir=$(mktemp -d)
curl -fsSL \
  https://raw.githubusercontent.com/himmelblau-idm/himmelblau/9fec8abcc3603c8c92c7473b8a756d2430957a74/scripts/cargo_vet_review.py \
  -o "$reference_dir/cargo_vet_review.py"
CODEX_VET_HELPER="$reference_dir/cargo_vet_review.py" \
  python3 -m unittest discover -s tests -v
rm -rf -- "$reference_dir"
```

This test invokes the helper's actual analysis methods against the fake container
boundary for a single review, two batches, final synthesis, and exemption-note
generation. The harness points its project at a disposable checkout; it does not
modify the helper file or run cargo-vet certification/exemption commands.

## Real rootless Podman acceptance checks

These checks require the target Linux host and authenticated disposable homes.
They have not been run in the development container, which has no Podman binary.

1. Record the wrapper commit, `podman --version`, container image ID, and the
   installed CLI version. List containers with
   `podman ps --filter label=io.codex-pro.project`; the role label identifies
   update and execution containers. Obtain the CLI version with
   `podman exec NAME /home/codex/.local/bin/codex --version`.
2. Launch two different disposable projects sequentially with the same
   `CODEX_PRO_HOME`. Verify both mount that home at `/home/codex` and reuse its
   authentication. Compare mounts and direct container listings: only the current
   checkout is mounted. Account for deliberate exceptions in `.files`.
3. With the same disposable `CODEX_PRO_HOME`, launch containers concurrently.
   Verify host and other-container temporary markers and sockets are inaccessible.
   Inspect private PID/IPC/network namespaces, `/tmp`, `/var/tmp`, `/run`, and the
   mode-0700 `XDG_RUNTIME_DIR` in both update and execution containers.
4. Attempt another invocation sharing the same home during update and execution,
   including a different command or checkout: it must succeed while the first run remains active. Stop either run and verify
   the other stays active and retains its own recovery journal.
   Verify lifecycle records are outside the mounted home.
5. Resume a shared conversation after the previous run exits. Verify a fresh marker
   through its AI tools agrees with the currently mounted checkout. Preserve
   evidence if execution context disagrees with direct container inspection.
6. Enable the `.files.example` vet rule in a disposable wrapper installation and
   run the unchanged PR #1880 helper. Exercise a small review, a large multi-batch
   review, synthesis, and exemption-note generation. Avoid actually certifying or
   adding an exemption unless that write is intended. Confirm every supplied input
   is readable in full and the host receives the final output file. Verify the
   nested individual file mounts remain present beneath the private tmpfs mounts.
7. Confirm that two matching input files are both readable under the opted-in
   regex rule and unrelated temporary files remain absent. Modify an original
   input after startup: the container must continue to see its initial snapshot.
   Match a symlink or socket deliberately and confirm a clear rejection.
8. Exercise successful exit, CLI failure, `SIGINT`, `SIGTERM`, and a Python
   `subprocess.run(..., timeout=...)` kill during both update and execution. After
   the cleanup worker finishes, confirm the exact invocation's containers and
   staging files are gone and its recovery journal is removed. Deleting the caller's input
   and output files immediately on timeout must not break cleanup or recreate
   the output file. Other active invocations sharing the same home must remain running.
9. Verify an interactive terminal and a piped/captured helper call; only the former
   gets a TTY. Include project, output, and rule paths containing spaces, plus a
   launcher reached through a symlink.

Do not mount host `/tmp`, runtime directories, or engine sockets to make these
checks pass. Keep the original routing incident open: successful acceptance checks
do not establish its root cause. Preserve logs and session IDs without tokens;
do not delete session databases as part of recovery or investigation.
