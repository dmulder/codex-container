# Codex in Podman

This project runs Codex in an openSUSE Tumbleweed container with development tools you choose. It keeps compilers, libraries, and other build dependencies in the container while letting Codex work directly on a host project. Codex's installation, settings, and sessions persist between runs.

The `codex` Bash script builds the image when needed, updates Codex and skills in a short-lived container, then starts Codex in your current directory.

## Requirements

- A Linux host with Bash, Podman configured for your user, and standard utilities including `readlink`, `sha256sum`, and `awk`.
- Support for Podman's `--userns=keep-id` and Containerfile heredocs.
- Internet access to download the base image, packages, Rust tools, Codex, and skills.
- A Codex account or credentials for authentication when required.

The script mounts `$HOME/.agents/skills` into the container. Create that directory before your first run, even if you have no host skills:

```sh
mkdir -p "$HOME/.agents/skills"
```

## Setup

Keep `codex` and `.packages` together in a permanent directory. From this checkout:

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

- Your launch directory is mounted read-write at `/workspace` and, when different, at its original absolute path. Codex starts in `/workspace`.
- Processes run with your host UID and GID, so newly created project files have your ownership.
- The persistent home is read-only during normal use, except for `.codex`, which remains writable for settings, authentication, and sessions. The startup update container can write to the entire persistent home.
- Host skills from `$HOME/.agents/skills` are mounted read-only at `/etc/codex/skills`.
- If `$HOME/.gitconfig` exists, it is mounted read-only for your Git configuration. Other host credentials, such as SSH keys, are not explicitly mounted.

The main container uses `--security-opt label=disable`. Containers are removed after exit; changes outside the mounted directories are temporary.

On each launch, the update step runs the Codex installer and installs all skills from `posit-dev/skills` into the persistent home.

## Storage and configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `CODEX_PRO_IMAGE` | `localhost/codex-pro:latest` | Image name to build and run. |
| `CODEX_PRO_HOME` | `${XDG_DATA_HOME:-$HOME/.local/share}/codex-pro` | Persistent container home, including the Codex installation and `.codex` state. |
| `XDG_CACHE_HOME` | `$HOME/.cache` | The generated Containerfile is stored in the `codex-pro` subdirectory. |

For example:

```sh
CODEX_PRO_HOME="$HOME/.local/share/codex-work" \
CODEX_PRO_IMAGE=localhost/codex-work:latest \
    /absolute/path/to/this/checkout/codex
```

Use absolute paths for storage overrides. The wrapper creates its data and cache directories automatically.

The script forwards `HTTP_PROXY`, `HTTPS_PROXY`, and `NO_PROXY`, plus their lowercase equivalents, to the update and main containers when set.

The image is rebuilt when the generated Containerfile changes or the expected image label is missing. A rebuild uses `--pull=newer`; an unchanged configuration does not automatically refresh the base image or its zypper packages. To force a build with a fresh image name, set `CODEX_PRO_IMAGE` to an unused tag.

## Troubleshooting

- **Missing `.packages`:** Put it beside the physical `codex` script, not beside a symlink or in the project directory.
- **Invalid package name or empty list:** Check the format described above. At least one package is required.
- **Package installation fails:** Check package availability and repository/network access. The generated Containerfile is in `${XDG_CACHE_HOME:-$HOME/.cache}/codex-pro/Containerfile`.
- **Missing bind-mount source:** Ensure `$HOME/.agents/skills` exists.
- **Startup update fails:** Check network access and that the persistent home is writable. The wrapper exits if the update commands fail.
