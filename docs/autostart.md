# Coming back by itself after a crash

The goal: you leave the laptop running, something goes wrong (Hivegrid crashes, the kernel faults, the machine locks up), and a few minutes later the phone reaches [`hivegrid-remote`](remote.md) again and your agents are back in their chats. `hivegrid-autostart` sets up the parts of that which software can do, and this page says plainly which parts it cannot.

```sh
hivegrid-autostart status                      # read-only: what is set up, what is missing, what to run
hivegrid-autostart install --dry-run --sysctl --watchdog --autologin   # print every file and command, change nothing
hivegrid-autostart install                     # user services and linger (no sudo)
hivegrid-autostart install --sysctl --watchdog # kernel faults and hard hangs become a reboot (asks sudo)
hivegrid-autostart install --autologin         # log in at boot (asks sudo; read the warning below first)
hivegrid-autostart uninstall --all             # remove everything it installed
```

`install.sh` copies the program to `~/.local/bin` with the other tools; from a checkout run `bin/hivegrid-autostart`. It runs as your normal user, never as root, and calls `sudo` itself only for the steps behind `--sysctl`, `--watchdog` and `--autologin`. Running `install` twice changes nothing the second time. `render DIR` writes every file it would install into `DIR` (to read them or to run `systemd-analyze verify --user DIR/*.service`).

## What survives what

| What happens | What comes back | By what |
| --- | --- | --- |
| `hivegrid-remote` crashes or is killed | The server, 3 s later (waits longer after repeated crashes, up to 60 s, and never gives up). Not after exit status 1 or 2 (no or unsafe token file, port taken, bad arguments): looping cannot fix those. | `hivegrid-remote.service` with `Restart=always` |
| Hivegrid (the WezTerm window) crashes or is killed | The window, 5 s later, with its layout and the omp chats ([session restore](features.md)). At most five restarts in ten minutes, then the unit stays failed. Closing the window yourself does not reopen it. | `hivegrid.service` with `Restart=on-failure` |
| Kernel oops, panic, soft or hard lockup, hardware NMI | The machine reboots after 10 s, then the rows below apply. | `--sysctl` |
| Total hang: no panic, no NMI (the kernel stops, timers stop) | A reset after at most 60 s, **only** when the machine has a hardware watchdog timer and its driver is loaded. | `--watchdog` |
| Total hang on a machine without a hardware watchdog | **Nothing.** Software cannot reset a machine whose CPUs no longer run software. The power button is the only way. | |
| Frozen display or GPU while the kernel still runs (the common failure with proprietary GPU drivers) | **Nothing automatic.** No panic happens, so nothing reboots; `hivegrid-remote` may keep answering the phone, but nothing can restart the compositor from outside. A reboot from the phone is not offered. | |
| Power loss, then power back | A boot, then the rows below. On a laptop the battery usually bridges short outages, and the BIOS option "power on after AC loss" does not apply (a laptop with an empty battery waits for the power button). | |
| Lid closed | A laptop suspends by default and is unreachable until it wakes. `hivegrid-autostart` does not change that, see "Lid and sleep". | |

After a reboot the chain is: firmware, disk passphrase (if the disk is encrypted), display manager, automatic login, graphical session, `hivegrid.service`, Hivegrid restores its layout. In parallel: `tailscaled` (a system service), then `hivegrid-remote.service` (a user service that runs without a login because of linger). Every link must hold; the next sections cover the ones that need your decision.

## The user services (no sudo)

`install` writes two units into `~/.config/systemd/user/` and enables them:

- `hivegrid-remote.service`: the server of [remote.md](remote.md) with `Restart=always`, no start limit and a growing delay. It runs `hivegrid-remote serve`, so it binds `127.0.0.1:8787` exactly as before and `tailscale serve` publishes it. A user unit cannot be ordered after `tailscaled` or `network-online.target` (those belong to the system manager); it does not need to, because loopback is always there and `tailscaled` publishes the page itself once it is up. With `--bind <tailscale ip>` the server would fail while the address does not exist yet and, since a failed bind exits with status 1, would not be restarted: keep the loopback setup.
- The remote unit sets `UMask=0077` (the shells of the phone terminal inherit it: new files are mode `0600`; see Terminal in [remote.md](remote.md#terminal)). It has no `NoNewPrivileges`, `LockPersonality` or `RestrictAddressFamilies`: in a user unit each of them sets the kernel's no-new-privs flag, which the shells inherit and which makes `sudo` and `su` refuse to run. It has no `ProtectHome` or `ProtectSystem` on purpose: the server writes `acks.json` and `seen.json` below `$XDG_RUNTIME_DIR/hivegrid-agents`, and `wezterm cli` writes logs of its own. It has no `PrivateTmp` either: in a user service that runs the server in a user namespace, where it can no longer read `/proc/<pid>/exe` of the Hivegrid window, so it finds no window and answers `503 no_mux`. To start the server with the phone terminal, set `remote.terminal` in `settings.json`; a hand-edited `ExecStart` is overwritten by the next `install`.
- `hivegrid.service`: `hivegrid start --cwd .` bound to `graphical-session.target`, so it starts with the graphical session and stops with it. It is enabled, not started, by `install`: starting it while you sit in a Hivegrid would open a second window. A unit file of your own with the same name is moved to `<name>.orig` and put back by `uninstall`.
- `loginctl enable-linger <user>`: the user manager (and so `hivegrid-remote`) starts at boot instead of at your login. `uninstall` turns it off again only when `install` turned it on. Opt out with `--no-linger`.

The remote server still needs its token (`hivegrid-remote token`) and `tailscale serve` set up once, as in [remote.md](remote.md); `status` shows both. `tailscaled` must be enabled (`systemctl is-enabled tailscaled`), the `tailscale serve` configuration is kept by it across reboots (check `tailscale serve status` after a reboot), and the machine's Tailscale key must not expire while you are away (disable key expiry for this machine in the Tailscale admin console, or it drops off the tailnet).

## Kernel faults become a reboot (`--sysctl`)

`install --sysctl` writes `/etc/sysctl.d/90-hivegrid-recovery.conf` and applies it (`sudo sysctl -p`). It records the values it replaced, and `uninstall --sysctl` puts them back in the running kernel too.

| Setting | Value | Why, and what it costs |
| --- | --- | --- |
| `kernel.panic` | 10 | Reboot 10 s after a panic. The kernel default (0) waits on the panic screen forever. |
| `kernel.panic_on_oops` | 1 | An oops (a driver bug) normally kills only the task that hit it; the display or the driver is often dead afterwards while the machine "runs". A panic makes it reboot. A harmless oops now costs a reboot. |
| `kernel.softlockup_panic` | 1 | A CPU stuck in kernel code for 20 s (2 x `kernel.watchdog_thresh`). |
| `kernel.hardlockup_panic` | 1 | A CPU that serviced no interrupt for 10 s. Works only with `kernel.nmi_watchdog = 1` (`status` warns when it is 0). |
| `kernel.panic_on_unrecovered_nmi`, `kernel.panic_on_io_nmi` | 1 | Chipset-reported memory, bus or I/O errors: the machine is not trustworthy afterwards. |

Left out on purpose, and listed commented-out in the file so you can opt in: `kernel.hung_task_panic` (a task in uninterruptible sleep for 120 s: a slow disk, a USB drive, a network or FUSE mount or a suspend/resume race trips it without the machine being dead), `kernel.panic_on_rcu_stall` (usually recovers; a real one shows as a soft lockup), `kernel.unknown_nmi_panic` (some firmwares raise harmless NMIs), `kernel.panic_on_warn` (graphics and Wi-Fi drivers warn routinely: never on a laptop). Each key starts with `-` in the file, so a kernel that lacks one skips it instead of failing the file.

Limits to know:

- A panic has to reach the reboot. After a panic some firmware and GPU combinations do not reset cleanly ("Rebooting in 10 seconds..." and then nothing); the kernel option `reboot=` (`acpi`, `efi`, `pci`, `kbd`) selects another method. Hivegrid does not set it: you can only find out which one works by testing.
- The log of the last seconds before a panic often never reaches the disk. `journalctl -k -b -1` shows what did; a persistent journal (`/var/log/journal`) helps.
- Nothing here helps against a crash that leaves the kernel running and the machine "up" with a frozen screen (see the table above).

## A hard hang becomes a reset (`--watchdog`)

A hardware watchdog is a timer in the chipset that resets the machine unless something keeps feeding it. systemd feeds it from PID 1 (`RuntimeWatchdogSec`), so a kernel or PID 1 that stops entirely stops the feeding and the hardware resets the machine. `install --watchdog` writes `/etc/systemd/system.conf.d/90-hivegrid-watchdog.conf` (`RuntimeWatchdogSec=60s`, `RebootWatchdogSec=2min`) and, when the hardware has such a timer, `/etc/systemd/system/hivegrid-watchdog.service`, which it enables. That unit runs `modprobe <driver>` at every boot and then re-executes systemd (`systemctl daemon-reexec`) so PID 1 opens the new device. `install` does the same once right away (`sudo modprobe`, `sudo systemctl daemon-reexec`).

Limits:

- It needs the hardware: a platform device `iTCO_wdt` (Intel), `sp5100-tco` (AMD) or a WDAT table. `status` and `install --watchdog` look for them in `/sys/bus/platform/devices` and do nothing when there is none. `softdog`, the software watchdog, is deliberately not offered: it is a timer inside the kernel it should watch and dies with it.
- Distributions blacklist these drivers, and `systemd-modules-load` honours that blacklist (`Module 'iTCO_wdt' is deny-listed (by kmod)`), so an entry in `modules-load.d` does not load one. An explicit `modprobe`, which the unit runs, ignores the blacklist. Some firmware locks the timer (`reboot disabled by hardware/BIOS` in `sudo journalctl -k -b`); then no `/dev/watchdog0` appears, the unit fails and the setting does nothing. Check `ls /dev/watchdog*` after a reboot.
- Under a load so extreme that PID 1 gets no CPU for 60 s, the machine resets too. Sleep: the Intel driver stops the timer across suspend; suspend and resume once after installing to confirm that your machine does not reboot.

## Unattended login (`--autologin`)

Hivegrid is a window: it needs a graphical session, and after a reboot a session exists only when something logs in. Without automatic login the machine waits at the login screen, `hivegrid-remote` runs (linger), but there is no Hivegrid for it to show, and the phone page answers `503 no_mux` until you log in. Find your display manager:

```sh
systemctl status display-manager      # or: readlink /etc/systemd/system/display-manager.service
echo "$XDG_SESSION_TYPE"            # x11 or wayland
```

`install --autologin` knows these three; it does not restart the display manager (that would end your session), so the setting applies at the next boot.

| Display manager | What the program writes (or write it yourself) |
| --- | --- |
| GDM (GNOME; Ubuntu and Debian `gdm3`, Fedora `gdm`) | In `/etc/gdm3/custom.conf` (Fedora: `/etc/gdm/custom.conf`) under `[daemon]`, between `# hivegrid-autostart begin` and `# hivegrid-autostart end` markers: `AutomaticLoginEnable=true` and `AutomaticLogin=<user>`. A different automatic login that is already in the file stops the program (you decide); `uninstall --autologin` removes exactly its own lines. |
| SDDM (KDE) | `/etc/sddm.conf.d/50-hivegrid-autologin.conf`: `[Autologin]` and `User=<user>` (add `Session=plasma` or `Session=plasmax11` there when SDDM does not pick yours). |
| LightDM (Xfce, MATE, Cinnamon, Unity) | `/etc/lightdm/lightdm.conf.d/50-hivegrid-autologin.conf`: `[Seat:*]`, `autologin-user=<user>`, `autologin-user-timeout=0`. |

**What you accept with automatic login.** Whoever switches the machine on gets your unlocked desktop: your terminals, agents, accounts and tokens, without a password. The lock screen appears only after the idle timeout. The login keyring is not unlocked by a password you did not type, so programs that ask the keyring (browsers on the "gnome-libsecret" store, `gh`, mail clients) prompt for it or fail until you unlock it; Hivegrid's web panes use Chrome's basic store and are not affected. Decide whether the machine is somewhere that is acceptable. The program does not turn this on without `--autologin`.

**Disk encryption.** With LUKS on the root or home volume, the boot stops at the passphrase prompt before any of the above runs: **unattended boot is impossible**, and no setting here changes that. The ways around it are a decision about your security, not about convenience: unlocking with the TPM (`systemd-cryptenroll --tpm2-device=auto --tpm2-pcrs=7 /dev/<luks device>` plus `tpm2-device=auto` in `/etc/crypttab`; needs a systemd-based initramfs, and a Secure Boot or firmware change breaks the unlock until you enrol again; see `systemd-cryptenroll(1)` and your distribution's notes, none of it is done by Hivegrid), a network unlock server (Clevis/Tang), or no encryption. `status` lists open dm-crypt volumes. The same trade applies there: TPM auto-unlock plus automatic login means the machine hands your data to whoever powers it on.

## Lid and sleep

A closed lid suspends a laptop by default, and a suspended laptop answers nobody. If the laptop should keep running with the lid closed, tell logind (not done by Hivegrid):

```sh
sudo install -D -m 0644 /dev/stdin /etc/systemd/logind.conf.d/90-lid-ignore.conf <<'EOF'
[Login]
HandleLidSwitch=ignore
HandleLidSwitchExternalPower=ignore
EOF
```

It applies at the next boot (restarting `systemd-logind` in a running session can end it). A laptop in a bag with the lid closed and the CPU running gets hot: use it on a desk. Remove the file to undo.

## What happens to the agents after a reboot

Hivegrid saves its layout every few seconds (`~/.local/state/hivegrid/session.json`) and builds it again when it starts: windows, tabs, splits and what each pane ran. An omp pane gets `omp --restore <session file>` typed into its shell: the chat comes back with its history, and a turn that a crash cut off goes on. A shell comes back in its directory, a web pane reopens its page, other programs (an editor, another agent such as Claude Code) come back as a plain shell. A snapshot with fewer panes replaces the saved one only after holding for 8 s, so the panes dying during a shutdown or a panic do not overwrite it. Nothing is restored when `"session": { "enabled": false }` is set in `settings.json`, when Hivegrid starts with a command of its own, or while another running Hivegrid owns the file. Details: [features.md](features.md). Hivegrid does not build more than this: an agent that waited for your answer comes back at the same chat and asks again when you continue it, and anything an agent ran outside the chat (a build, a server) is gone.

## Verify

```sh
hivegrid-autostart status                              # everything "ok", "to do" empty
systemctl --user status hivegrid-remote hivegrid       # active / enabled
systemd-analyze verify --user ~/.config/systemd/user/hivegrid-remote.service ~/.config/systemd/user/hivegrid.service
loginctl show-user "$USER" -p Linger                   # Linger=yes
cat /proc/sys/kernel/panic /proc/sys/kernel/panic_on_oops
ls /dev/watchdog*                                      # the device exists when --watchdog worked
```

Crash tests (they end your Hivegrid, so save work first):

```sh
systemctl --user kill -s KILL hivegrid-remote          # back within 3 s: systemctl --user status hivegrid-remote
systemctl --user kill -s SEGV hivegrid                 # the window reopens with the saved layout after 5 s
```

The real test is a kernel panic. It reboots the machine ten seconds later and ends every program on it, so close what matters first:

```sh
echo c | sudo tee /proc/sysrq-trigger
```

After the boot: the phone page loads, `uptime -s` shows the new boot time, and `journalctl -b -1 -k | tail` shows the last kernel messages of the crashed boot (when they reached the disk). If the machine does not come back, the sections above name the link that broke; the first suspects are a login screen (no `--autologin`), a passphrase prompt, or a firmware that does not reset after a panic.

## Undo

```sh
hivegrid-autostart uninstall --all     # units, linger (if it turned it on), sysctl file and earlier values, watchdog files, automatic login
hivegrid-autostart status              # confirms
```

`uninstall` without `--all` removes only the user units and linger. It does not stop a Hivegrid or `hivegrid-remote` that is running. A driver that `--watchdog` loaded stays loaded until the next reboot. The state it keeps for the revert is `~/.local/state/hivegrid/autostart.json`. A file below `/etc` that no longer carries the `Managed by hivegrid-autostart` line (you edited it) is left alone with a warning. A GDM block whose end marker is missing is not touched by `install` or `uninstall`: both stop and ask you to repair it by hand, so nothing after it in `custom.conf` is lost.

Privileged files are handed to `sudo install` on its standard input, never through a temporary file. The automatic-login user is the account the program runs as (from the password database, not `$USER`).

## Commands you have to run yourself

Nothing here is run for you without the flag, and `sudo` is called only for these. `--dry-run` prints the exact list for your machine.

| Command | What it does |
| --- | --- |
| `hivegrid-autostart install` | Writes the two user units, enables them, runs `loginctl enable-linger`. No sudo. |
| `hivegrid-remote token`, `tailscale serve --bg --https=443 http://127.0.0.1:8787` | Once, as in [remote.md](remote.md). Not done by this program. |
| `hivegrid-autostart install --sysctl` | `sudo install` of `/etc/sysctl.d/90-hivegrid-recovery.conf`, `sudo sysctl -p` on it. |
| `hivegrid-autostart install --watchdog` | `sudo install` of `/etc/systemd/system.conf.d/90-hivegrid-watchdog.conf` and `/etc/systemd/system/hivegrid-watchdog.service`; `sudo systemctl daemon-reload` and `enable hivegrid-watchdog.service`; `sudo modprobe <driver>`; `sudo systemctl daemon-reexec`. |
| `hivegrid-autostart install --autologin` | `sudo install` of the display manager's configuration (see the table above). |
| `hivegrid-autostart uninstall [--all]` | `sudo rm` of the files above, `sudo sysctl -w` of the old values, `sudo systemctl daemon-reexec`, `loginctl disable-linger` when it enabled it. |
| Disk passphrase, TPM unlock, `HandleLidSwitch`, Tailscale key expiry | Your decision; described above, not done by this program. |

## If it does not come back

| Symptom | Cause and fix |
| --- | --- |
| Phone: timeout, the machine is off or at the login screen | Not rebooted (no panic, see the table), a passphrase prompt, or no automatic login. `status` on the machine shows the last three. |
| Phone page loads, says "no Hivegrid window" | Linger works but there is no graphical session or Hivegrid did not start: `systemctl --user status hivegrid` (a failed unit after five restarts needs `systemctl --user reset-failed hivegrid && systemctl --user start hivegrid`). |
| `hivegrid.service` fails at once after login | The display variables are not in the user manager yet: `systemctl --user show-environment \| grep -e DISPLAY -e WAYLAND`. The unit retries after 5 s; an autostart `.desktop` entry of your desktop is the alternative when `graphical-session.target` is never reached (rare on current GNOME and KDE). |
| `hivegrid-remote.service` is `failed` with status 1 | No or unsafe token file (`hivegrid-remote token`), or the port is in use; systemd does not retry those. After fixing it: `systemctl --user reset-failed hivegrid-remote && systemctl --user start hivegrid-remote`. |
| After `--sysctl` the machine reboots at odd times | A kernel setting fired on a harmless stall. Look at `journalctl -b -1 -k`, then remove the setting from the file (or `uninstall --sysctl`). |
