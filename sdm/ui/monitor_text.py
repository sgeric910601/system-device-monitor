"""系統監控頁（CPU／GPU、記憶體、磁碟、網路）的文字；不載入 GTK，方便測試。

容量、速率用 sdm.core.byte_format（十進位，與磁碟標示相同），記憶體以 1024 進位；
總覽頁與頂端列沿用原本的 KiB／MiB 格式。
"""
from sdm.core import byte_format as bytes_text
from sdm.core.disk_health import bar_fraction, bar_tone
from sdm.core.fmt import PLACEHOLDER, percent, temperature, value_text
from sdm.core.memory import CRITICAL, WARNING

LOADING = "讀取中…"
CALCULATING = "計算中…"
MHZ_PER_GHZ = 1000
PERCENT = 100
PRESSURE_NAMES = {WARNING: "偏高", CRITICAL: "嚴重"}
LEGEND_NAMES = (("app", "App"), ("shared", "共享"), ("kernel", "核心"), ("cache", "快取"),
                ("other", "其他"), ("free", "空閒"))
DISCONNECTED = "未連線"


def cpu_header(detail):
    """（總使用率、使用者與系統比例）；第一次取樣只建立基準時顯示「計算中…」。"""
    if detail is None or detail.sample is None:
        return PLACEHOLDER, CALCULATING
    usage = detail.sample.total
    return (bytes_text.percent(usage.total),
            f"使用者 {bytes_text.percent(usage.user)}・系統 {bytes_text.percent(usage.system)}")


def load_text(load):
    return PLACEHOLDER if not load else "／".join(f"{value:.2f}" for value in load)


def frequency_text(mhz):
    if mhz is None:
        return PLACEHOLDER
    return f"{mhz / MHZ_PER_GHZ:.2f} GHz" if mhz >= MHZ_PER_GHZ else f"{mhz:.0f} MHz"


def topology_text(info):
    cores, threads = info.get("cores"), info.get("threads")
    if not cores and not threads:
        return PLACEHOLDER
    return f"{cores or PLACEHOLDER} 核心・{threads or PLACEHOLDER} 執行緒"


def gpu_title(gpu):
    return gpu.name + ("（處理器內建）" if gpu.integrated else "")


def vram_text(gpu):
    if gpu.vram_used is None or gpu.vram_total is None:
        return PLACEHOLDER
    return f"{bytes_text.memory(gpu.vram_used)} / {bytes_text.memory(gpu.vram_total)}"


def gpu_rows(gpu):
    return (("使用率", percent(gpu.usage)), ("顯示記憶體", vram_text(gpu)),
            ("溫度", temperature(gpu.temp_c)), ("風扇", percent(gpu.fan_pct)),
            ("耗電", value_text(gpu.watts, " W")))


def cpu_app_rows(ranked, cores):
    """（名稱、單一核心百分比、全機百分比）；100% 代表用滿一個核心。"""
    return tuple((row.name, f"{row.value:.1f}%", f"全機 {row.value / max(1, cores):.1f}%")
                 for row in ranked)


def memory_app_rows(ranked):
    return tuple((row.name, bytes_text.memory(row.value), "") for row in ranked)


def memory_header(detail):
    if detail is None or detail.composition is None:
        return PLACEHOLDER, LOADING
    memory = detail.composition
    return (bytes_text.percent(memory.used_fraction),
            f"已使用 {bytes_text.memory(memory.used)} / {bytes_text.memory(memory.total)}")


def pressure_badge(level):
    return "記憶體壓力：" + PRESSURE_NAMES.get(level, "正常")


def pressure_text(pressure):
    if pressure is None or pressure.some_avg10 is None:
        return "核心沒有提供記憶體壓力資訊"
    full = "" if pressure.full_avg10 is None else f"（全部停住 {pressure.full_avg10:.1f}%）"
    return f"等待記憶體 {pressure.some_avg10:.1f}%{full}・近 10 秒"


def memory_legend(memory):
    """組成長條的圖例：（代號、名稱、容量文字）。"""
    values = dict(memory.segments())
    return tuple((key, name, bytes_text.memory(values[key])) for key, name in LEGEND_NAMES)


def swap_text(memory):
    if not memory.swap_total:
        return "沒有設定 Swap"
    return f"{bytes_text.memory(memory.swap_used)} / {bytes_text.memory(memory.swap_total)}"


def zswap_text(memory):
    if memory.zswap is None:
        return None
    return f"{bytes_text.memory(memory.zswap)}（壓縮前 {bytes_text.memory(memory.zswapped or 0)}）"


def disk_title(disk):
    return f"{disk.model}・{disk.connection}・{bytes_text.size(disk.size)}"


def volume_title(volume):
    return volume.mount + (f"・{volume.fstype.upper()}" if volume.fstype else "")


def volume_fraction(volume):
    return bar_fraction(volume.used, volume.total)


def volume_usage(volume):
    return (f"已用 {bytes_text.percent(volume_fraction(volume))}"
            f"（可用 {bytes_text.size(volume.available)}／共 {bytes_text.size(volume.total)}）")


def volume_tone(volume):
    return bar_tone(volume_fraction(volume))


def disk_rates(disk):
    return f"讀取 {bytes_text.rate(disk.read_rate)}・寫入 {bytes_text.rate(disk.write_rate)}"


def disk_totals(disk):
    if disk.read_total is None or disk.write_total is None:
        return PLACEHOLDER
    return f"讀取 {bytes_text.size(disk.read_total)}・寫入 {bytes_text.size(disk.write_total)}"


def disk_temperature(disk):
    return "溫度未回報" if disk.temperature is None else temperature(disk.temperature)


def network_title(detail):
    primary = detail.primary if detail is not None else None
    return DISCONNECTED if primary is None else f"{primary.kind}・{primary.name}"


def address_text(detail):
    address = detail.address if detail is not None else None
    return f"IPv4 {address or PLACEHOLDER}"


def compact_rates(detail):
    rx = detail.rx_rate if detail is not None else None
    tx = detail.tx_rate if detail is not None else None
    return f"↓ {bytes_text.compact_rate(rx)}　↑ {bytes_text.compact_rate(tx)}"


def network_rows(detail):
    return (("下載", bytes_text.rate(detail.rx_rate)), ("上傳", bytes_text.rate(detail.tx_rate)))


def network_totals(interface):
    if interface is None:
        return PLACEHOLDER
    return (f"下載 {bytes_text.size(interface.rx_total)}・"
            f"上傳 {bytes_text.size(interface.tx_total)}")


def other_rows(detail):
    """其他連線中的介面：（名稱、種類、速率、累計）。"""
    return tuple((item.name, item.kind,
                  f"↓ {bytes_text.rate(item.rx_rate)}　↑ {bytes_text.rate(item.tx_rate)}",
                  f"累計 ↓ {bytes_text.size(item.rx_total)}　↑ {bytes_text.size(item.tx_total)}")
                 for item in detail.others)
