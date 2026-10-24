"""充電上限滑桿的文字與可否操作（不載入 GTK）：數值、刻度、狀態列、提醒與保留開關的說明。"""
from sdm.constants import ChargeLimit
from sdm.services.charge_control import (APPLIED, APPLYING, CHECKING, FAILED, IDLE,
                                         NOT_INSTALLED, UNSUPPORTED)

TITLE = "充電上限"
UNLIMITED = "不限制"
PERSIST_TITLE = "開機與喚醒後保留"
PERSIST_NOTE = "開機或從睡眠喚醒後，由系統服務重新套用這個上限（只保留你自己設定的值）。"
DEMO = "示範模式不會寫入"
SETTINGS_LINK = "開啟「設定 → 管理員功能」"
TONE_MUTED, TONE_WARNING = "muted", "warning"
HINT = "放開滑桿或停 0.6 秒後套用（需要管理員授權）"
TEXTS = {
    CHECKING: "確認管理員元件中…",
    APPLYING: "套用中…",
    APPLIED: "已套用",
}
NOT_INSTALLED_TEXT = "需要先安裝管理員元件才能調整"
REINSTALL_TEXT = "管理員元件版本不符，需要重新安裝"


def value_text(value):
    return UNLIMITED if value >= ChargeLimit.MAX_LIMIT else f"{value}%"


def mark_text(value):
    return str(value)


def status_line(state):
    """（文字、色調）：套用中…／已套用／未套用：原因，或缺少元件時的說明。"""
    status = state.status
    if status == UNSUPPORTED:
        return "", TONE_MUTED
    if status == NOT_INSTALLED:
        return (REINSTALL_TEXT if state.protocol is False else NOT_INSTALLED_TEXT), TONE_WARNING
    if status == FAILED:
        return "未套用：" + state.message, TONE_WARNING
    if state.demo and status in (IDLE, APPLIED):
        return (f"{TEXTS[APPLIED]}（{DEMO}）" if status == APPLIED else DEMO), TONE_MUTED
    return TEXTS.get(status, HINT), TONE_MUTED


def visible(state):
    return state.status != UNSUPPORTED


def needs_settings(state):
    """缺少管理員元件（或版本不符）時顯示到設定的連結。"""
    return not state.demo and state.status == NOT_INSTALLED


def slider_enabled(state):
    return state.ready


def persist_enabled(state):
    return state.ready and not state.busy and state.draft is None


def conflict_text(state):
    return "\n".join(conflict.text for conflict in state.conflicts)
