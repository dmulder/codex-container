# Codex in Podman

This project runs Codex in an openSUSE Tumbleweed container with development tools you choose. It keeps compilers, libraries, and other build dependencies in the container while letting Codex work directly on a host project. Codex's installation, settings, and sessions persist between runs.

The `codex` Bash launcher embeds its Python implementation and Containerfile recipe in one portable script. It builds the image when needed, updates Codex and skills in a short-lived container, then starts Codex in your current directory. All launch directories and commands use one shared persistent home.

## Requirements

- A Linux host with Bash, Python 3.9 or later (standard library only), `readlink`, and local rootless Podman configured for your user. Remote Podman is not supported.
- Support for Podman's `--userns=keep-id`, private networking, nested bind mounts beneath tmpfs, and Containerfile heredocs.
- Internet access to download the base image, packages, Rust tools, Codex, and skills.
- A Codex account or credentials for authentication when required.

The script mounts `$HOME/.agents/skills` read-only if that directory exists. To provide host skills:

```sh
mkdir -p "$HOME/.agents/skills"
```

## Setup

Keep `codex` and your `.packages` together in a permanent directory, with an optional `.files` configuration beside them. No companion Python file or Containerfile template is needed; the script generates the build Containerfile in its cache. From this checkout:

```sh
chmod +x ./codex
```

You can run the script by its absolute path or add a symlink in a directory on your `PATH`:

```sh
mkdir -p "$HOME/.local/bin"
ln -s /absolute/path/to/this/checkout/codex "$HOME/.local/bin/codex"
```

Ensure `$HOME/.local/bin` is on your `PATH`. If you also have Codex installed on the host, use the wrapper's absolute path when you want to select this container explicitly.

## Configure packages

Edit `.packages` beside the **physical script**. The script resolves symlinks before locating this file, so it always uses that configuration regardless of the current directory or where a symlink lives.

The supplied file contains the existing development environment's package list. Add or remove openSUSE package names as needed:

```text
# Basic tools
curl
git
ripgrep

# Build dependencies; multiple names on one line are allowed
cmake make gcc gcc-c++
python3  # Inline comments are allowed
```

Names are separated by whitespace. Blank lines and text following `#` are ignored. The file must exist and contain at least one package. Entries must start with a letter or digit and contain only letters, digits, `+`, `_`, `.`, `:`, or `-`. Shell commands, quoting, and zypper options are not supported.

Packages are installed using:

```sh
zypper --non-interactive install --no-recommends ...
```

Use package names available in the configured openSUSE Tumbleweed repositories. The image also enables the `security:idm` repository.

Some packages are required by the wrapper itself: keep `cargo` for the image's Rust tool installation, and keep `curl`, `nodejs24`, and `npm24` for the startup update commands. The image separately installs `cargo-deb` and `cargo-generate-rpm` with Cargo; these are not `.packages` entries.

Changes to the effective package list trigger an image build on the next launch. Changes only to comments or whitespace do not trigger a build.

## Run

Change to the project you want Codex to work on, then invoke the wrapper:

```sh
cd /path/to/project
/absolute/path/to/this/checkout/codex
```

With the symlink on your `PATH`:

```sh
cd /path/to/project
codex
```

Arguments are passed through to Codex:

```sh
codex "Explain how this project is organized"
codex --help
```

Every invocation, including `--help`, first checks the image and runs the update step. The first launch can take time because it builds the development environment. Subsequent launches reuse the image when its generated Containerfile matches.

The wrapper starts Codex with `--yolo`, disabling Codex's approval prompts and sandbox. Commands run inside the container, and the mounted project is writable: edits and deletions affect your host files. Run it from the project directory you intend to expose.

## What the container can access

- Your physical launch directory is mounted read-write at `/workspace` and, when different, at its original absolute path. Codex starts in `/workspace`. Both mounts expose the same checkout; parent project directories are not mounted.
- Processes run with your host UID and GID, so newly created project files have your ownership.
- The shared persistent home is mounted at `/home/codex`, read-write for installations, settings, authentication, sessions, and caches. Host-side locks and cleanup journals stay outside this mount.
- `/tmp`, `/var/tmp`, and `/run` are private tmpfs mounts. `TMPDIR=/tmp`; the entrypoint creates a private mode-0700 `XDG_RUNTIME_DIR`. PID, IPC, and network namespaces are private. No host service, container-engine, or SSH-agent socket is mounted.
- Host skills from `$HOME/.agents/skills`, when present, are mounted read-only at `/etc/codex/skills`. `$HOME/.gitconfig`, when present, is mounted read-only for Git identity and configuration.
- Optional `.files` entries add deliberately configured access, as described below.

Both update and execution containers currently use `--security-opt label=disable`. Restoring SELinux enforcement with suitable mount labels is a separate follow-up; the wrapper does not relabel the host home or shared skills. Network isolation does not prevent Codex or configured tools from contacting network-accessible services. Review remote tools and absolute state paths in settings before migrating them.

## Shared persistent storage

All commands and launch directories use `CODEX_PRO_HOME` as their persistent home, mounted at `/home/codex`. Credentials, settings, sessions, caches, and installations are shared. Run `codex login --device-auth` once before using scripted commands. `CODEX_PRO_PROFILE` is no longer used; Codex's own `--profile` option still selects configuration within the shared home.

Concurrent invocations, including interactive sessions and scripted exec calls, can share the same persistent home. Separate `CODEX_PRO_HOME` values provide independent homes when needed.

The wrapper sets both `CODEX_HOME` and `CODEX_SQLITE_HOME` to `/home/codex/.codex` and forces the matching `sqlite_home` CLI setting; explicit user CLI overrides of that setting are rejected.

| Variable | Default | Purpose |
| --- | --- | --- |
| `CODEX_PRO_IMAGE` | `localhost/codex-pro:latest` | Image name to build and run. |
| `CODEX_PRO_HOME` | `${XDG_DATA_HOME:-$HOME/.local/share}/codex-pro` | Shared persistent container home. |
| `XDG_CACHE_HOME` | `$HOME/.cache` | Generated Containerfile, build lock, and host-only lifecycle records in the `codex-pro` subdirectory. |

Storage overrides must be absolute. A checkout must neither contain nor be inside wrapper state/cache storage, which would expose it through the checkout mount. Lifecycle records live under `<cache>/codex-pro/state/<SHA-256 of resolved CODEX_PRO_HOME>/`. Each invocation has its own recovery journal so concurrent sessions clean up independently. The build lock serializes image preparation; each invocation pins the resulting image ID.

Updates still run on every launch. The wrapper forwards `HTTP_PROXY`, `HTTPS_PROXY`, and `NO_PROXY` and their lowercase equivalents. An unchanged Containerfile does not refresh the base image automatically; choose a new `CODEX_PRO_IMAGE` tag to force a rebuild.

### Existing installations

Existing `.codex` and `.local` directories directly under `CODEX_PRO_HOME` are reused, including credentials and sessions. Previously created `projects/<hash>/<profile>/` directories remain untouched but unused; their state is not automatically migrated or deleted.

## Optional file mounts: .files

Create `.files` beside the **physical launcher**, using [.files.example](.files.example) as a starting point. It is optional and ignored by Git. No temporary-file matching is enabled by default. Each rule uses literal tab separators:

```text
ro<TAB>/absolute/file-or-directory
rw<TAB>/absolute/file-or-directory
ro-regex<TAB>/absolute/directory<TAB>filename-regex
```

Replace `<TAB>` with an actual tab. Paths may contain spaces. Blank lines and lines beginning with `#` are ignored. There is no shell evaluation, environment-variable expansion, tilde expansion, or inline-comment syntax.

`ro` and `rw` mount the exact file or directory at the same absolute container pathname. `ro-regex` uses Python regular expressions with full filename matching, enumerates only immediate children of its directory, and mounts read-only **snapshots** of all matching files at their original absolute paths. The regex is not a shell glob: `tmp[^/]+\.diff` matches filenames such as `tmpABC.diff`. Rules are evaluated once per invocation before containers start. Missing literal sources, invalid rules, conflicting or overlapping destinations, and matching symlinks/special files fail clearly; zero regex matches are allowed. Regex files must belong to the current host user.

The wrapper rejects broad home/root mounts, temporary-directory mounts, runtime/system sources, and wrapper/Codex state sources. Matching individual regular files in `/tmp` or `/var/tmp` is allowed. Runtime sockets and final-component source symlinks are rejected. Configured directories are deliberate additional access: their contents are not recursively audited, so choose narrow sources that contain no credentials or runtime sockets. Bind paths containing colons or newlines are unsupported.

### Unchanged Himmelblau vet helper

For [PR #1880](https://github.com/himmelblau-idm/himmelblau/pull/1880), enable this opt-in exception with literal tabs (the example file already contains the correct separators):

```sh
printf 'ro-regex\t/tmp\ttmp[^/]+\\.diff\n' >> /path/to/codex-container/.files
```

Then run `make vet` normally, with the wrapper on `PATH`. If the helper uses another temporary root through `TMPDIR`, use that absolute directory in the rule instead.

This rule snapshots **all** matching inputs, including files from another simultaneous review. That is the intentional scope of this exception; the wrapper does not inspect prompts or restrict matches to the current review. It does not mount the parent temporary directory or its unrelated files and sockets. Inputs created after the initial scan are not included.

For `codex exec`, the wrapper handles `--output-last-message PATH`, `--output-last-message=PATH`, `-o PATH`, and `-o=PATH` separately. It redirects the result to a fresh per-run writable directory, removes the container, then copies the result into the requested host file. Relative paths resolve against `--cd` (or the launch directory). Output paths must be owned regular files or new files in an existing directory; symlinks are rejected. Missing/empty output or a copy failure returns a nonzero status. Existing output is replaced only after successful execution.

The helper remains responsible for its original temporary files. The wrapper owns and cleans its snapshots and output staging after normal completion, failure, interruption, or caller timeout. If Python kills the launcher on timeout, cleanup continues briefly in its surviving worker. Input snapshots and a pinned output file descriptor allow the helper to delete its originals immediately without racing a container still using those paths.

## Cleanup and recovery

The Linux supervisor handles `SIGINT`, `SIGTERM`, `SIGHUP`, and parent death, including `SIGKILL` sent to the launcher. It tracks containers by unique name, terminates clients, removes containers, and then deletes staging files and its recovery journal. Captured stdout contains Codex output; build/update diagnostics use stderr.

A host-only `<invocation-id>.cleanup-pending.json` journal records containers before creation. If cleanup fails or both supervisor processes are forcibly killed, the journal remains available for manual recovery; other launches can continue. The journal lists exact container names and the private exchange directory. Recover by inspecting those names, running `podman rm --force --ignore NAME` for each, confirming they are gone, removing the recorded exchange directory, and finally deleting the journal. Do not delete Codex databases or sessions. During ordinary execution the journal exists temporarily; do not remove it while its invocation is running.

## Validation and incident investigation

Run the offline process-level tests with:

```sh
python3 -m unittest discover -s tests -v
```

The suite uses a fake Podman executable and real subprocesses/signals; it makes no API calls. See [tests/README.md](tests/README.md) for testing the unchanged vet helper and the required real-Podman acceptance checks.

These changes remove known sharing paths; they do not establish the cause of the observed execution-context failure or prove that the underlying Codex routing issue is fixed. Preserve session IDs, wrapper and CLI versions, image IDs, actual mount inspections, and relevant logs without authentication tokens. Keep the incident open until routing and ownership behavior are understood.

## Troubleshooting

- **Missing `.packages`:** Keep it beside the physical launcher. Package names must follow the documented format.
- **Authentication missing in exec:** Run `codex login --device-auth` using the same `CODEX_PRO_HOME`.
- **Vet input missing:** Enable the `.diff` regex rule for the helper's actual temporary directory before launching the wrapper.
- **Invalid `.files`:** Use literal tabs, absolute paths, and Python regex syntax; do not use shell quoting or globs.
- **Cleanup pending:** Follow the journal-based recovery procedure above to remove the affected invocation’s leftover containers.
- **Startup update fails:** Check network access and persistent-home permissions. The wrapper returns failure and removes the update container.
