# Installation details

An AI coding agent that installs Hivegrid for someone reads [SETUP.md](../SETUP.md): it runs the installer and then asks for the theme, the agent (omp or Claude Code), the accounts to log in and a browser for web panes. What it applies is the Settings panel's data (`LEADER+,`), so every choice can be changed there afterwards.

## Running the installer

```sh
curl -fsSL https://raw.githubusercontent.com/leongreiner/hivegrid/main/install.sh | bash [-s -- OPTIONS]
./install.sh [OPTIONS]            # from a clone
```

| Option | Effect |
| --- | --- |
| `--tools` | also downloads the latest `yazi`, `ya` and `lazygit` into `~/.local/bin` |
| `--wezterm` | downloads the Hivegrid WezTerm build again (an update) |
| `--no-wezterm` | never downloads it; the stock `wezterm` on the machine is used, with reduced features |
| `-h`, `--help` | prints the usage |

The installer first checks its tools. `curl` and `tar` are required: without them it names the command that installs them and stops. `python3` runs the panels and `unzip` unpacks the font; without them it warns and goes on. Piped into `bash` it has no checkout to install from, so it downloads the repository as a tarball (`https://github.com/leongreiner/hivegrid/archive/refs/heads/main.tar.gz`; `HIVEGRID_SRC_URL` points it at another file), runs that copy's installer with the same options and deletes the copy. Everything is copied into `$HOME`, so nothing refers to the download or to a clone afterwards, and no sudo is used.

## What it installs

It copies `config/*.lua` to `~/.config/wezterm/`, `config/yazi/` to `~/.config/hivegrid/yazi/` (the explorer's own yazi config; a global `~/.config/yazi` is never read or changed; the explorer runs from `~/.cache/hivegrid/yazi/`, which links that config and adds a theme generated from the current Hivegrid colour theme), `bin/` to `~/.local/bin/`, the omp extension that reports agent states, the icon, and a `Hivegrid` launcher that overrides the `org.wezfurlong.wezterm` desktop entry. Differing existing files are saved once as `*.bak.<timestamp>`. `./sync.sh` copies the live setup back into the repo for maintenance.

## Fonts

It downloads [DejaVu Sans Mono Nerd Font](https://github.com/ryanoasis/nerd-fonts) (the `DejaVuSansM Nerd Font Mono` family, DejaVu Sans Mono patched with Nerd Font icons and with the same cell width) into `~/.local/share/fonts/DejaVuSansMNerdFont/` and refreshes the font cache. WezTerm uses it for the terminal text and the header, and the charts draw with it; without it, plain DejaVu Sans Mono is used and Nerd Font icons show as boxes. To have omp draw its icons with Nerd Font glyphs, set `symbolPreset: nerd` in omp's settings.

## The WezTerm build

Unless `~/.local/bin/hivegrid` exists, `install.sh` downloads `hivegrid-wezterm-x86_64-linux.tar.gz` and its `.sha256` from the latest GitHub release of this repository, verifies the checksum, unpacks `wezterm`, `wezterm-gui` and the few `libxcb`/`xkbcommon-x11` libraries the build ships into `~/.local/opt/hivegrid-wezterm/` and writes the `hivegrid` launcher; the build's icon font is installed from the archive when there is no `wezterm/` submodule. It warns when the system lacks a library the binary needs (the build runs on glibc 2.35 or newer). `HIVEGRID_WEZTERM_URL` points the download at another file (a mirror, or a local `file://` archive from `dev/package-wezterm`). When the download fails it says why and carries on with the stock `wezterm` if there is one, and stops if there is neither; building the fork yourself is in the README, "Custom WezTerm build".

The release is built by `.github/workflows/release.yml` on Ubuntu 22.04 and runs only when started by hand (`gh workflow run release.yml -f tag=v0.1.0`); it builds the fork commit the `wezterm/` submodule points at and creates the release with the archive. `dev/package-wezterm [OUT_DIR]` makes the same archive from a local build.

## Account login

The usage chip in the header needs each account logged in at least once: an omp profile (`omp --profile=<name>`, then `/login` with Anthropic) and/or Claude Code (`CLAUDE_CONFIG_DIR=~/.claude-<name> claude`, which creates `~/.claude-<name>/.credentials.json`; that file is also how an account is discovered). An account paired with an omp profile (see [Settings, Accounts](panels.md)) stays logged in as long as omp runs, because the chip reads omp's own token; without a profile it follows the Claude Code token, which only `claude` refreshes: once it expires the last numbers stay, marked `~`, and each Claude Code session of that account reports fresh ones through its status line hook; `login` appears only after a day without either, or when the API rejects the token (see [Claude quota](accounts-and-quota.md)). The state is only read, never changed.
