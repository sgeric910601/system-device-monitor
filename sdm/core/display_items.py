"""「設定 → 顯示項目」可以隱藏的電力流向細項與詳細資訊欄位（不載入 GTK）。

- 電力流向細項（桌機的耗電流向與拆解清單）：顯示卡、處理器、其他（估算）；
  顯示卡底下再分 NVIDIA 與 AMD。與參考專案的「晶片運算 → CPU／GPU／ANE」相同，
  隱藏上層時下層一起隱藏。處理器功耗之後的版本才有，讀不到的項目會自動不顯示。
- 詳細資訊欄位：筆電 18 項（sdm.ui.battery_details），桌機 8 項（這裡）。
代號存在 preferences.json 的 hidden_flow_items／hidden_detail_fields（小寫英文與底線）。
不載入 GTK，也不依賴介面模組。
"""
from dataclasses import dataclass
from typing import Optional

from sdm.core.power_breakdown import OTHER_ID

GPU, CPU, OTHER = "gpu", "cpu", OTHER_ID
GPU_PREFIX = "gpu:"
VENDORS = ("nvidia", "amd")


@dataclass(frozen=True)
class FlowItem:
    id: str
    title: str
    parent: Optional[str] = None


FLOW_ITEMS = (
    FlowItem(GPU, "顯示卡"),
    FlowItem(CPU, "處理器"),
    FlowItem(OTHER, "其他（估算）"),
    FlowItem("gpu_nvidia", "NVIDIA", parent=GPU),
    FlowItem("gpu_amd", "AMD", parent=GPU),
)
PARENTS = {item.id: item.parent for item in FLOW_ITEMS}
DESKTOP_DETAIL_FIELDS = (
    ("processor", "處理器"), ("model", "機型"), ("uptime", "開機時間"),
    ("ups_model", "不斷電系統型號"), ("line_voltage", "市電電壓"), ("ups_load", "負載"),
    ("nominal_power", "額定功率"), ("ups_runtime", "預估續航"),
)


def top_level():
    return tuple(item for item in FLOW_ITEMS if item.parent is None)


def children(parent):
    return tuple(item for item in FLOW_ITEMS if item.parent == parent)


def flow_category(item_id):
    """拆解項目的代號（例如 gpu:nvidia:0）→ 設定裡的細項代號。"""
    if item_id.startswith(GPU_PREFIX):
        vendor = item_id.split(":")[1] if item_id.count(":") >= 1 else ""
        return f"gpu_{vendor}" if vendor in VENDORS else GPU
    return item_id if item_id in PARENTS else OTHER


def flow_hidden(item_id, hidden):
    """細項本身或它的上層被隱藏時為 True。"""
    category = flow_category(item_id)
    return category in hidden or PARENTS.get(category) in hidden


def visible_flow_items(items, hidden):
    """items 為（代號、名稱、瓦數）；回傳沒有被隱藏的項目。"""
    if not hidden:
        return tuple(items)
    return tuple(item for item in items if not flow_hidden(item[0], hidden))


def toggled(hidden, item_id, visible):
    """回傳新的隱藏清單（不修改原本的）。"""
    if visible:
        return tuple(name for name in hidden if name != item_id)
    return hidden if item_id in hidden else tuple(hidden) + (item_id,)
