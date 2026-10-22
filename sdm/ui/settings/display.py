"""設定 → 顯示項目：電力流向細項與詳細資訊欄位要不要顯示（參考專案 DisplayItemsView）。

隱藏上層細項（顯示卡）時下層（NVIDIA、AMD）一起隱藏，下層的勾選框也停用。
筆電的電力流向只有變壓器、系統與電池三個節點，沒有可隱藏的細項。
"""
import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk

from sdm.core.display_items import children, top_level
from sdm.ui.battery_details import detail_fields
from sdm.ui.monitor_widgets import card
from sdm.ui.settings.controls import wrapped
from sdm.ui.widgets import box, label

FLOW_KEY, DETAIL_KEY = "hidden_flow_items", "hidden_detail_fields"
COLUMNS = 2
LAPTOP_FLOW = "筆電的電力流向只有變壓器、系統與電池三個節點，沒有可隱藏的細項。"


def flow_note(hidden, parent, child_titles):
    if parent.id in hidden:
        return f"隱藏{parent.title}後不會顯示{'與'.join(child_titles)}。"
    return "讀不到的項目會自動隱藏；處理器功耗要等之後的版本才有。"


def flow_card(controls):
    store = controls.store
    tops = top_level()
    row = box(False, 16)
    for item in tops:
        row.pack_start(controls.hidden_check(FLOW_KEY, item.id, item.title), False, False, 0)
    content = [row]
    for parent in tops:
        kids = children(parent.id)
        if not kids:
            continue
        sub = box(False, 16)
        sub.pack_start(label(f"{parent.title}細項", "muted"), False, False, 0)
        for item in kids:
            sub.pack_start(controls.hidden_check(
                FLOW_KEY, item.id, item.title,
                when=lambda parent=parent: parent.id not in store.preferences.hidden_flow_items),
                False, False, 0)
        content.append(sub)
        titles = tuple(item.title for item in kids)
        content.append(controls.note(lambda parent=parent, titles=titles: flow_note(
            store.preferences.hidden_flow_items, parent, titles), {FLOW_KEY}))
    return card("電力流向細項", *content)


def details_card(controls, laptop):
    grid = Gtk.Grid(column_spacing=16, row_spacing=6, column_homogeneous=True)
    for index, (field, title) in enumerate(detail_fields(laptop)):
        row, column = divmod(index, COLUMNS)
        grid.attach(controls.hidden_check(DETAIL_KEY, field, title), column, row, 1, 1)
    return card("詳細資訊欄位", grid)


def show_all_button(controls):
    store = controls.store
    button = Gtk.Button(label="全部顯示", halign=Gtk.Align.END)
    button.connect("clicked", lambda *_: controls.apply(**{FLOW_KEY: (), DETAIL_KEY: ()}))
    controls.enable_when(button, FLOW_KEY, when=lambda: bool(
        store.preferences.hidden_flow_items or store.preferences.hidden_detail_fields))
    return button


def build(controls, context):
    page = box(spacing=16)
    flow = card("電力流向細項", wrapped(LAPTOP_FLOW, "muted")) if context.laptop \
        else flow_card(controls)
    for child in (flow, details_card(controls, context.laptop), show_all_button(controls)):
        page.pack_start(child, False, False, 0)
    return page
