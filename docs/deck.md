# Stream Deck

`hivegrid-deck` turns an Elgato Stream Deck Mini (6 keys, USB `0fd9:0063`) into a control panel for Hivegrid. It works while another window has the keyboard: it never types shortcuts, it talks to the running Hivegrid through `wezterm cli`, and for equalize and arrange (which are Lua) through the left bars.

```sh
hivegrid-deck                 # the daemon: draws the keys, reacts to presses, reconnects when the deck is replugged
hivegrid-deck do ACTION       # one action without the deck: next, enter, mic, new, grid, zoom, equalize, arrange
```

| Key | Shows | Press |
| --- | --- | --- |
| 1 | how many agents wait for you (needs you or limit), purple while there are any | focus the next one, across tabs and windows |
| 2 | number and state of the focused agent | Enter in the focused pane (approve, continue, send what you dictated) |
| 3 | mic; red while recording | start dictating; press again: the words are typed into the focused pane, without Enter |
| 4 | `+` | a new omp session in the focused pane's folder, split off in its tab (it gets the keyboard) |
| 5 | grid | one press: equalize the dividers; two presses within 0.35 s: arrange the panes |
| 6 | zoom | zoom the focused pane to the whole window and back (`Alt+z`) |

After every key the Hivegrid window comes to the front. GNOME on Wayland lets no outside program raise a window, so this goes through a small GNOME Shell extension, `hivegrid-raise@hivegrid` (`gnome/`, installed by `install.sh`): it offers one D-Bus method, `org.hivegrid.Raise.Raise`, that activates the most recently used window whose title starts with `Hivegrid`. Enable it once (GNOME on Wayland loads a new extension only at the next login, and `gnome-extensions enable` does not know it before then, so add it to the list directly): `gsettings set org.gnome.shell enabled-extensions "$(gsettings get org.gnome.shell enabled-extensions | sed "s/]$/, 'hivegrid-raise@hivegrid']/")"`, then log out and in. Without it the keys still act, the window just stays where it is.

## Setup

Plug the deck into the laptop (some hub ports do not enumerate it). The device node must be readable by your user: on a desktop session the logged-in user normally gets access through a udev ACL; if `/dev/hidraw*` of the deck is root only, add a udev rule that tags `0fd9:0063` with `uaccess`. Dictation needs `arecord` and the Gemini key of the phone's voice mode (`~/.config/hivegrid/gemini-key`, see [remote.md](remote.md#voice)); the audio of a dictation goes to Google's Gemini API for transcription. The key pictures need PIL.

## How equalize and arrange get to Lua

Nothing outside a pane can set a user var, so the daemon writes `equalize:<pane>` or `arrange:<pane>` into `$XDG_RUNTIME_DIR/hivegrid/deck.fifo`. Every `hivegrid-bar` holds that FIFO open and announces a line as the user var `hivegrid_deck`; `config/layouts.lua` accepts it only from a bar and acts on the window of the named pane (the one that had focus). Without a Hivegrid window nobody reads the FIFO and the key does nothing.
