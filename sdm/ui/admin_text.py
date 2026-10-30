"""設定 → 管理員功能的文字（不載入 GTK）：元件狀態（含充電上限）、安裝指令、取樣狀態與說明。"""
from pathlib import Path

from sdm.services import system_components
from sdm.services.privileged import KIND_DEB, KIND_LOCAL, SOCKET_MINE, SOCKET_OTHER

PACKAGE_ROOT = Path("/usr/share/system-device-monitor")
TITLES = {KIND_DEB: "已安裝（.deb）", KIND_LOCAL: "已安裝（原始碼）", None: "未安裝"}
CHECKING = "確認版本中…"
REINSTALL = "需要重新安裝管理員元件"
EXPLANATION = ("開啟後，系統服務以 RAPL 讀取處理器插槽與核心的耗電，電源頁會多出 CPU 項目，"
               "耗電排行也改為依 CPU 佔比估算瓦數。只在「電源」頁顯示時取樣；"
               "開啟與關閉都需要管理員授權，開啟程式時不會要求密碼。")
SIDE_CHANNEL = ("RAPL 能量計數器可能被用來推測同一台電腦上其他程式的運算（旁路攻擊），"
                "所以系統預設只讓管理員讀取。開啟後取樣服務只提供固定 1 秒視窗的平均、"
                "四捨五入到 0.1 W，只有你的帳號能連線（你的帳號執行的任何程式都讀得到）；"
                "不需要時請關閉。")
STARTUP_OFF = "CPU 取樣服務沒有為你開啟，已關閉此選項；需要時請重新開啟並授權。"
SOURCE_NOTE = ("在終端機執行下列指令安裝 CPU 功耗取樣、充電上限與智慧充電的管理員元件"
               "（會要求管理員密碼，只安裝已提交的版本）：")
CHARGE_PREFIX = "充電上限元件："
CHARGE_DEMO = "示範模式，不會寫入"
NO_BATTERY = "這台電腦沒有可調整充電上限的電池"
SOURCE_INSTALLED = ("要移除時在終端機執行 install.py --system-components remove"
                    "（會要求管理員密碼）。")
PACKAGE_MISSING = "管理員元件隨 .deb 套件安裝；找不到或版本不符時，請重新安裝套件。"
PACKAGE_INSTALLED = "管理員元件隨 .deb 套件安裝，以 apt 移除套件時一併移除。"


def packaged(root):
    return Path(root) == PACKAGE_ROOT


def component_title(state):
    title = TITLES[state.kind]
    if state.layout is None or state.protocol is True:
        return title
    return title + "・" + (CHECKING if state.protocol is None else REINSTALL)


def charge_missing(charge):
    """充電上限的小程式沒有安裝或版本不符（示範模式不算）。"""
    return charge is not None and not charge.demo and \
        (charge.layout is None or charge.protocol is False)


def install_command(state, root, charge=None):
    """原始碼資料夾還沒安裝、版本不符或缺少充電上限元件時可以直接貼上的指令；其他情況為 None。"""
    if packaged(root) or state.kind == KIND_DEB:
        return None
    if state.layout is None or state.protocol is False or charge_missing(charge):
        return system_components.install_command(root)
    return None


def component_note(state, root, charge=None):
    if packaged(root) or state.kind == KIND_DEB:
        return PACKAGE_INSTALLED if state.ready else PACKAGE_MISSING
    return SOURCE_INSTALLED if state.ready and not charge_missing(charge) else SOURCE_NOTE


def charge_line(charge):
    """充電上限元件的狀態；charge 為 sdm.services.charge_control.ChargeState。"""
    if charge is None:
        return ""
    if charge.demo:
        return CHARGE_PREFIX + CHARGE_DEMO
    if charge.layout is None:
        return CHARGE_PREFIX + TITLES[None]
    text = CHARGE_PREFIX + TITLES[charge.layout.kind]
    if charge.protocol is not True:
        return text + "・" + (CHECKING if charge.protocol is None else "需要重新安裝")
    return text if charge.supply is not None else text + "・" + NO_BATTERY


def sampling_status(state, preference):
    if state.layout is None:
        return "需要先安裝管理員元件"
    if state.protocol is False:
        return REINSTALL
    if state.busy:
        return "等待管理員授權…"
    if state.socket == SOCKET_MINE:
        return "已開啟・只在「電源」頁顯示時取樣" if preference else \
            "服務仍開啟中，本程式不會連線；關閉需要管理員授權"
    if state.socket == SOCKET_OTHER and not preference:
        return "由這台電腦的其他使用者開啟；開啟此選項會改為只允許你連線"
    return "未開啟"


def error_line(state):
    return f"上次的錯誤：{state.error}" if state.error else ""
