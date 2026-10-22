"""電源模式（power-profiles-daemon）：讀取、監看變更與非同步切換。

一律使用假的系統匯流排：測試絕不呼叫真正的切換。
"""
import unittest

from gi.repository import Gio, GLib

from sdm.services.power_profiles import (DemoProfiles, PowerProfiles, ProfileState,
                                         parse_properties, profile_title)

NEW = ("org.freedesktop.UPower.PowerProfiles", "/org/freedesktop/UPower/PowerProfiles",
       "org.freedesktop.UPower.PowerProfiles")
OLD = ("net.hadess.PowerProfiles", "/net/hadess/PowerProfiles", "net.hadess.PowerProfiles")
PROPERTIES = {"ActiveProfile": "balanced",
              "Profiles": [{"Profile": "power-saver", "Driver": "x"},
                           {"Profile": "balanced", "Driver": "x"},
                           {"Profile": "performance", "Driver": "x"}],
              "Version": "0.21"}


def error(name="org.freedesktop.DBus.Error.ServiceUnknown"):
    return GLib.Error.new_literal(Gio.dbus_error_quark(), name, 0)


class FakeBus:
    """記錄每個呼叫；由測試以 reply()／fail() 決定結果。"""
    def __init__(self):
        self.calls, self.subscriptions, self.unsubscribed = [], [], []

    def call(self, destination, path, interface, method, parameters, reply_type, flags,
             timeout, cancellable, callback, user_data):
        self.calls.append(dict(destination=destination, path=path, interface=interface,
                               method=method, parameters=parameters, timeout=timeout,
                               callback=callback, user_data=user_data))

    def call_finish(self, result):
        if isinstance(result, GLib.Error):
            raise result
        return result

    def reply(self, value, index=-1):
        call = self.calls[index]
        call["callback"](self, value, call["user_data"])

    def signal_subscribe(self, sender, interface, member, path, arg0, flags, callback):
        self.subscriptions.append(dict(sender=sender, path=path, arg0=arg0, callback=callback))
        return len(self.subscriptions)

    def signal_unsubscribe(self, subscription):
        self.unsubscribed.append(subscription)


def all_properties(values=PROPERTIES):
    return GLib.Variant("(a{sv})", ({key: GLib.Variant("s", value) if isinstance(value, str)
                                      else GLib.Variant("aa{sv}", [{k: GLib.Variant("s", v)
                                                                   for k, v in item.items()}
                                                                  for item in value])
                                      for key, value in values.items()},))


class Parse(unittest.TestCase):
    def test_known_profiles_in_fixed_order(self):
        state = parse_properties({"ActiveProfile": "performance",
                                  "Profiles": [{"Profile": "performance"}, {"Profile": "custom"},
                                               {"Profile": "power-saver"}]})
        self.assertEqual(state, ProfileState(("power-saver", "performance"), "performance"))

    def test_bad_values_give_nothing(self):
        self.assertEqual(parse_properties({"ActiveProfile": 3, "Profiles": "x"}), ProfileState())

    def test_titles(self):
        self.assertEqual([profile_title(name) for name in ("power-saver", "balanced",
                                                          "performance")],
                         ["省電", "平衡", "效能"])
        self.assertEqual(profile_title(None), "—")


class Service(unittest.TestCase):
    def setUp(self):
        self.bus, self.changes = FakeBus(), []
        self.profiles = PowerProfiles(self.bus, self.changes.append)
        self.profiles.start()

    def test_reads_the_new_service_name_first(self):
        call = self.bus.calls[0]
        self.assertEqual((call["destination"], call["path"], call["method"]),
                         (NEW[0], NEW[1], "GetAll"))
        self.bus.reply(all_properties())
        self.assertEqual(self.profiles.state,
                         ProfileState(("power-saver", "balanced", "performance"), "balanced"))
        self.assertEqual(self.changes[-1], self.profiles.state)
        self.assertEqual(self.bus.subscriptions[0]["path"], NEW[1])
        self.assertEqual(self.bus.subscriptions[0]["arg0"], NEW[2])

    def test_falls_back_to_the_old_name_then_gives_up(self):
        self.bus.reply(error())
        self.assertEqual(self.bus.calls[1]["destination"], OLD[0])
        self.bus.reply(error())
        self.assertEqual(self.profiles.state, ProfileState())
        self.assertEqual(len(self.bus.calls), 2)
        self.assertEqual(self.bus.subscriptions, [])

    def test_switch_uses_properties_set_with_a_timeout(self):
        self.bus.reply(all_properties())
        results = []
        self.profiles.set("performance", results.append)
        call = self.bus.calls[-1]
        self.assertEqual((call["method"], call["timeout"]), ("Set", 1800))
        self.assertEqual(call["parameters"].unpack(),
                         (NEW[2], "ActiveProfile", "performance"))
        self.bus.reply(GLib.Variant("()", ()))
        self.assertEqual(results, [True])
        self.assertEqual(self.profiles.state.active, "performance")

    def test_failed_switch_is_logged_and_keeps_the_mode(self):
        self.bus.reply(all_properties())
        results = []
        self.profiles.set("power-saver", results.append)
        with self.assertLogs("sdm.services.power_profiles", "WARNING"):
            self.bus.reply(error("org.freedesktop.DBus.Error.AccessDenied"))
        self.assertEqual(results, [False])
        self.assertEqual(self.profiles.state.active, "balanced")

    def test_unknown_or_unavailable_profile_is_refused_without_a_call(self):
        results = []
        self.profiles.set("performance", results.append)       # 還沒讀到服務
        self.bus.reply(all_properties())
        count = len(self.bus.calls)
        self.profiles.set("turbo", results.append)
        self.assertEqual(results, [False, False])
        self.assertEqual(len(self.bus.calls), count)

    def test_change_signal_rereads(self):
        self.bus.reply(all_properties())
        callback = self.bus.subscriptions[0]["callback"]
        changed = GLib.Variant("(sa{sv}as)", (NEW[2], {"ActiveProfile": GLib.Variant("s", "x")},
                                              []))
        callback(self.bus, NEW[0], NEW[1], "org.freedesktop.DBus.Properties",
                 "PropertiesChanged", changed)
        self.assertEqual(self.bus.calls[-1]["method"], "GetAll")
        self.bus.reply(all_properties(dict(PROPERTIES, ActiveProfile="power-saver")))
        self.assertEqual(self.profiles.state.active, "power-saver")

    def test_stop_unsubscribes_and_ignores_late_replies(self):
        self.bus.reply(all_properties())
        self.profiles.stop()
        self.assertEqual(self.bus.unsubscribed, [1])
        self.profiles.set("performance", lambda ok: None)
        self.assertEqual(self.bus.calls[-1]["method"], "GetAll")

    def test_without_a_bus_nothing_is_available(self):
        profiles = PowerProfiles(None, self.changes.append)
        profiles.start()
        results = []
        profiles.set("balanced", results.append)
        self.assertEqual((profiles.state, results), (ProfileState(), [False]))


class Demo(unittest.TestCase):
    def test_demo_switches_in_memory(self):
        changes, results = [], []
        demo = DemoProfiles("balanced", changes.append)
        demo.start()
        demo.set("performance", results.append)
        self.assertEqual((demo.state.active, results), ("performance", [True]))
        self.assertEqual(len(demo.state.available), 3)
        self.assertEqual(DemoProfiles(None, changes.append).state, ProfileState())


if __name__ == "__main__":
    unittest.main()
