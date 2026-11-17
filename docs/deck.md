# Stream Deck

`hivegrid-deck` turns an Elgato Stream Deck Mini (6 keys, USB `0fd9:0063`) into a control panel for Hivegrid. It works while another window has the keyboard: it never types shortcuts, it talks to the running Hivegrid through `wezterm cli`, and for equalize and arrange (which are Lua) through the left bars.

```sh
hivegrid-deck                 # the daemon: draws the keys, reacts to presses, reconnects when the deck is replugged
hivegrid-deck do ACTION       # one action without the deck: next, enter, mic, new, grid, zoom, equalize, arrange
```

| Key | Shows | Press |
| --- | --- | --- |
| 1 | how many agents wait for you: needs you, limit, or finished (`done`). Purple while one needs you, green when only finished ones wait | focus the next one, across tabs and windows; needs-you and limit come before finished |
| 2 | the logo of the account the focused agent's chat would move to; `no move` (its own logo, dimmed) when there is none | move the chat there: press once, the key turns red and says `again`, press again within 4 s. omp quits, the chat is copied to that profile and resumes there (the phone's account switch). The key shows `moving`, then `moved` or `failed` |
| 3 | mic; red while recording | start dictating; press again: the words are typed into the focused pane, without Enter |
| 4 | `+` | a new omp session on the account with the most room left (see below): in the focused pane itself when that is a plain shell (omp is typed at its prompt, no pane is added), else split off in the focused pane's tab, in its folder (it gets the keyboard) |
| 5 | grid | one press: equalize the dividers; two presses within 0.35 s: arrange the panes |
| 6 | zoom | zoom the focused pane to the whole window and back (`Alt+z`) |

After every key the Hivegrid window comes to the front. GNOME on Wayland lets no outside program raise a window, so this goes through a small GNOME Shell extension, `hivegrid-raise@hivegrid` (`gnome/`, installed by `install.sh`): it offers one D-Bus method, `org.hivegrid.Raise.Raise`, that activates the most recently used window whose title starts with `Hivegrid`. Enable it once (GNOME on Wayland loads a new extension only at the next login, and `gnome-extensions enable` does not know it before then, so add it to the list directly): `gsettings set org.gnome.shell enabled-extensions "$(gsettings get org.gnome.shell enabled-extensions | sed "s/]$/, 'hivegrid-raise@hivegrid']/")"`, then log out and in. Without it the keys still act, the window just stays where it is.

## Which account a new session gets

Key 4 starts `omp --profile=<profile>` for the account with the most room left. Room is what remains in the tighter of the account's 5-hour and weekly windows (from the `claude-quota` cache, [accounts-and-quota.md](accounts-and-quota.md)); a window past its reset counts as empty. Only accounts with a paired omp profile, a quota state `ok` and a reading younger than 15 minutes count. When none qualifies (no readings, every account needs a login) the session starts on the focused agent's profile, else omp's default. The daemon logs which account it picked (`journalctl --user -u hivegrid-deck`).

## Account rescue (key 2)

The target is the other account with the most room left (the tighter of its 5-hour and weekly windows, the rule of key 4). An agent that hit its limit moves to any account with at least 5 % room; a healthy one only when the target has 20 points more room than its own account, because the move restarts omp. Nothing moves while the agent works, is busy or waits for an answer, or when it runs omp's default configuration (a chat moves only between paired omp profiles); these are the rules of the phone's **Switch account…** ([accounts-and-quota.md](accounts-and-quota.md)), and the move itself is the same code.

The logos are PNG files named after the account label in `~/.config/hivegrid/deck-logos/` (`seal.png`, `maxwell.png`, `private.png`; `HIVEGRID_DECK_LOGOS` overrides the folder); an account without one gets its initial on a tile.

## Setup

Plug the deck into the laptop (some hub ports do not enumerate it). The device node must be readable by your user: on a desktop session the logged-in user normally gets access through a udev ACL; if `/dev/hidraw*` of the deck is root only, add a udev rule that tags `0fd9:0063` with `uaccess`. Dictation needs `arecord` and the Gemini key of the phone's voice mode (`~/.config/hivegrid/gemini-key`, see [remote.md](remote.md#voice)); the audio of a dictation goes to Google's Gemini API for transcription. The key pictures need PIL.

## How equalize and arrange get to Lua

Nothing outside a pane can set a user var, so the daemon writes `equalize:<pane>` or `arrange:<pane>` into `$XDG_RUNTIME_DIR/hivegrid/deck.fifo`. Every `hivegrid-bar` holds that FIFO open and announces a line as the user var `hivegrid_deck`; `config/layouts.lua` accepts it only from a bar and acts on the window of the named pane (the one that had focus). Without a Hivegrid window nobody reads the FIFO and the key does nothing.
