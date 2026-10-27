# Keys

`LEADER` is `Ctrl+Space`.

| Group | Keys | Action |
| --- | --- | --- |
| Panes | `Ctrl+Shift+\|` / `Ctrl+Shift+_` | split right / down |
| | `Ctrl+Arrow` | split in that direction |
| | `Alt+Arrow`, `Alt+h/j/k/l` | focus pane |
| | `Alt+z` | zoom pane |
| | `LEADER+r` | resize mode (arrows or `h/j/k/l`, `Esc`/`Enter` leaves) |
| | `LEADER+p` | pane mode: `r`/`R` rotate, `g` go to a pane, `s`/`S` swap with a pane (focus follows the pane / stays in the slot), `x` close, `t` to a new tab (and follow it), `z` zoom, `e` equalize, `Esc`/`q` leaves |
| Grid | `LEADER+e`, `Ctrl+Shift+E` | equalize panes (also the `▦` button of the overview dock) |
| | `Ctrl+Shift+A` | arrange the panes into the best grid (also the `⊞` button) |
| | `LEADER+1` / `2` / `3` | new tab with `omp` / 2x2 grid / 3x3 grid (also in the `+` right-click menu, which lists these, the panels and their keys; Enter, a click or the row number opens an entry) |
| Agents | `Alt+n`, `LEADER+n` | jump to the next pane needing attention |
| | `LEADER+o` | show or hide the overview dock (right edge) |
| | `Alt+]`, `Alt+[` | next / previous agent pane |
| | `LEADER+g` | agent picker (every pane of the window; the most urgent one starts selected) |
| | `LEADER+b` | toggle broadcast typing to all panes of the tab |
| Sidebar | `Ctrl+F` / `Ctrl+G` | file explorer / git view |
| | `LEADER+Tab` | hide or show, remembering the view |
| | `Enter` / click, `r`, `q` in the git view | open lazygit in the selected repository (quit lazygit to return to the list), rescan, close; `j`/`k` or arrows move, `PageUp`/`PageDown`, `g`/`G` or `Home`/`End` jump |
| | `Ctrl+C` in the explorer | copy the absolute path of the hovered file or folder, or of all selected entries one per line |
| | double click or `T` in the explorer | open a terminal in that folder (a file's folder), split right of the last active grid pane, or below it when that pane is narrow; it takes focus |
| | `Enter` / `o` in the explorer | enter a folder; open text files (the hovered one, or all selected) in `$EDITOR` (else `nano`, else `vi`) in a new grid pane placed like a double click's terminal, which takes focus; other files go to yazi's opener |
| | `q` in the explorer | close it; the keyboard returns to the grid pane (yazi's other keys are unchanged: `.` shows hidden files, `F1` or `~` help) |
| | drag a column border in the explorer | resize the parent / current / preview columns (the pointer turns into a resize cursor over a border; Hivegrid build only). Until a border is dragged the columns follow the sidebar width: below 32 columns only the file list, below 64 file list and preview, then the parent column too |
| Web | `LEADER+w`, then `l` | location: type a URL or search words (opens a web pane when the tab has none) |
| | `LEADER+w`, then `n` | new web pane beside the last grid pane (asks for the URL) |
| | `LEADER+w`, then `b` / `f` | back / forward |
| | `LEADER+w`, then `r` / `R` | reload / hard reload (`s` stops loading) |
| | `LEADER+w`, then `y` / `o` | copy the page URL / open it in the system browser |
| | click on an `http(s)` link in a terminal pane | open it in the tab's web pane (a web pane's own links stay in the page) |
| Panels | `LEADER+,` | settings panel |
| | `LEADER+h` | usage history panel |
| | `LEADER+a` | agent hive panel |
| | `LEADER+u`, then `1`-`9` | start (or switch) omp with that account |
| Misc | `Ctrl+C` / `Ctrl+V` | smart copy or interrupt / paste |
| | `Alt+s` | toggle system stats |
| | `Alt+q` | Claude quota picker with all accounts (digit = the `LEADER+u` number); `LEADER+q` hides or shows the header chip |
| | `Ctrl+Shift+R` | reload config |

Leader, sidebar toggles, the overview dock toggle, the web key table, `Ctrl+Shift+E` (equalize), `Ctrl+Shift+A` (arrange), splits, the account launcher and the panel keys can be rebound in the settings panel (Keys); everything else is fixed.
