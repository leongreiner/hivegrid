// Hivegrid raise: GNOME on Wayland lets no outside program activate a window, so hivegrid-deck
// (docs/deck.md) asks the shell through this extension. One D-Bus method on the session bus,
// org.hivegrid.Raise.Raise() -> b: activates the most recently used window whose title starts
// with "Hivegrid" (config/wezterm.lua's format-window-title). Nothing else is reachable.
import Gio from 'gi://Gio';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';

const NAME = 'org.hivegrid.Raise';
const PATH = '/org/hivegrid/Raise';
const IFACE = `<node><interface name="${NAME}">
  <method name="Raise"><arg type="b" direction="out" name="raised"/></method>
</interface></node>`;

export default class HivegridRaise extends Extension {
    enable() {
        this._object = Gio.DBusExportedObject.wrapJSObject(IFACE, this);
        this._object.export(Gio.DBus.session, PATH);
        this._owner = Gio.bus_own_name_on_connection(Gio.DBus.session, NAME, Gio.BusNameOwnerFlags.NONE, null, null);
    }

    disable() {
        Gio.bus_unown_name(this._owner);
        this._object.unexport();
        this._object = null;
    }

    Raise() {
        const wins = global.get_window_actors()
            .map(actor => actor.meta_window)
            .filter(w => (w.get_title() || '').startsWith('Hivegrid'))
            .sort((a, b) => b.get_user_time() - a.get_user_time());
        if (wins.length === 0)
            return false;
        Main.activateWindow(wins[0]);
        return true;
    }
}
