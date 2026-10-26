# Contributing to Hivegrid

Pull requests are welcome: bug fixes, new features, documentation, themes, tests. Hivegrid is alpha software, so small focused changes land fastest. For a larger feature or a redesign, open an issue first so the direction can be agreed before you spend time on it.

## Ground rules

- One topic per pull request, against `main`.
- Keep the existing conventions: look at neighbouring code before adding a new pattern. The design language every surface follows is in `.claude/skills/hivegrid-qa/design.md`.
- User-visible changes update `README.md` in the same pull request.
- Never commit secrets, tokens, account names or personal paths. Fixtures and tests use temp directories and fake data, never your real `~/.omp`, `~/.claude*` or `~/.config`.

## Getting the code

```sh
git clone --recurse-submodules https://github.com/leongreiner/hivegrid.git
cd hivegrid
```

`wezterm/` is a submodule with the WezTerm fork that adds the Hivegrid features (clickable header, overlays, hover). The config also runs on stock WezTerm with reduced features. Without the submodule (`git clone` without `--recurse-submodules`) everything but the fork build works.

## Checks

```sh
dev/check            # everything CI runs
dev/check py lua luacheck   # only some steps
```

`dev/check` compiles and tests the Python (standard library `unittest`), Lua (`luac -p`, the stub-based tests in `tests/lua`, luacheck), the omp extension (`bun test`), and runs shellcheck and actionlint. A tool that is not installed is skipped locally; CI fails on a missing one. Please add a test for a bug you fix or a behaviour you add, following the tests next to it.

## Trying a change in a real window

Hivegrid is a GUI, so layout, focus, colours and timing only show in the running program. `dev/qa/hgq` starts an isolated Hivegrid on its own Xvfb display with a sandbox home, driven by synthetic keys and clicks and with screenshots, so your own window and settings are never touched:

```sh
dev/qa/hgq help
dev/qa/hgq up 3 1920 1080
dev/qa/hgq x 3 key ctrl+space o
dev/qa/hgq shot 3 /tmp/a.png
dev/qa/hgq down 3
dev/smoke                  # end-to-end smoke test (needs Xvfb and the fork build)
```

Needs Xvfb, ImageMagick and libXtst. The sandbox shares your real `~/.omp` and `~/.claude*`, so keep prompts trivial there, or replace `omp` with a fake script (see `.claude/skills/hivegrid-qa/SKILL.md`). For visual changes, attach before and after screenshots to the pull request.

## Pull request checklist

- [ ] `dev/check` is green.
- [ ] Tested in the sandbox (screenshots for visual changes).
- [ ] README updated when behaviour, keys or settings changed.
- [ ] No unrelated reformatting.

## Changes to the WezTerm fork

The fork lives in its own repository (`wezterm/`, branch `hivegrid`). Changes there are pull requests against that repository. A change to this repository that needs a fork change says so in its description.

## Reporting bugs

Use the bug report template. Include your Hivegrid commit, whether you run the fork build or stock WezTerm, your OS and window size, and what `dev/qa/hgq log` or the WezTerm log shows. For security problems see [SECURITY.md](SECURITY.md).

By contributing you agree that your contribution is licensed under the [MIT License](LICENSE).
