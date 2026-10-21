"""機型（DMI 廠商與產品名稱）與開機時間；只讀這兩個欄位，絕不讀序號或 UUID。"""
from pathlib import Path
import re

from sdm.core.fmt import number
from sdm.data.sources import clean_text, read

MODEL_MAX_CHARS = 64
# 主機板廠商未填寫時常見的預設字串，不顯示
PLACEHOLDERS = {"to be filled by o.e.m.", "system manufacturer", "system product name",
                "default string", "not applicable", "not specified", "none", "o.e.m.",
                "unknown"}
CORPORATE_SUFFIX = re.compile(
    r"[\s,]*(technology\s+)?(co\.,?\s*ltd\.?|inc\.?|corporation|corp\.?|gmbh|ltd\.?)$",
    re.IGNORECASE)


def dmi_value(root, name):
    value = clean_text(read(Path(root)/"sys/class/dmi/id"/name), MODEL_MAX_CHARS)
    return "" if value.lower() in PLACEHOLDERS else value


def read_model(root=Path("/")):
    """例如「Example Board X1」；廠商去掉公司型態字尾，產品名稱已含廠商時不重複。"""
    vendor = CORPORATE_SUFFIX.sub("", dmi_value(root, "sys_vendor")).strip()
    product = dmi_value(root, "product_name")
    if vendor and product.lower().startswith(vendor.lower()):
        vendor = ""
    return " ".join(part for part in (vendor, product) if part)[:MODEL_MAX_CHARS]


def read_uptime(root=Path("/")):
    """開機至今的秒數；讀不到時回傳 None。"""
    value = number(read(Path(root)/"proc/uptime"))
    return value if value is not None and value >= 0 else None
