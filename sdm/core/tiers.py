"""取樣分層：頂端列需要的資料一直取樣；各頁的詳細資料只在頁面顯示時才取樣。

- 電源：各 App 的 CPU（耗電排行）。
- 總覽：磁碟 SMART 屬性（補上 SATA 等沒有 hwmon 的磁碟溫度）。
- CPU／GPU：各核心、平均負載、時脈、溫度分組與風扇、各 App 的 CPU。
- 記憶體：組成與壓力、各 App 的記憶體。
- 磁碟：讀寫速率與累計、容量、SMART 屬性與 NVMe 壽命資料。
- 網路：主要介面、IPv4、各介面累計流量。
頁面顯示狀態改變時世代加一，之前送出的取樣結果一律丟棄（參考專案 SamplingGate）。
"""
from dataclasses import astuple, dataclass
from functools import reduce

PAGE_POWER, PAGE_OVERVIEW, PAGE_CPU, PAGE_MEMORY, PAGE_DISK, PAGE_NETWORK = (
    "power", "overview", "cpu", "memory", "disk", "network")
STORAGE_PAGES = (PAGE_OVERVIEW, PAGE_DISK)


@dataclass(frozen=True)
class DetailRequest:
    cpu: bool = False               # 各核心、平均負載、時脈
    thermal: bool = False           # 溫度分組與風扇
    apps_cpu: bool = False          # 各 App 的 CPU（程序群組）
    apps_memory: bool = False       # 各 App 的記憶體
    memory: bool = False            # 記憶體組成與壓力
    disks: bool = False             # 磁碟讀寫、累計與容量
    smart: bool = False             # UDisks2 SMART 屬性（每 60 秒）
    smart_attributes: bool = False  # NVMe 壽命資料（每 10 分鐘）
    network: bool = False           # 主要介面、IPv4、各介面累計

    def union(self, other):
        return DetailRequest(*(a or b for a, b in zip(astuple(self), astuple(other))))


PAGE_REQUESTS = {
    PAGE_POWER: DetailRequest(apps_cpu=True),
    PAGE_OVERVIEW: DetailRequest(smart=True),
    PAGE_CPU: DetailRequest(cpu=True, thermal=True, apps_cpu=True),
    PAGE_MEMORY: DetailRequest(memory=True, apps_memory=True),
    PAGE_DISK: DetailRequest(disks=True, smart=True, smart_attributes=True),
    PAGE_NETWORK: DetailRequest(network=True),
}


def request_for(pages):
    """顯示中的頁面 → 需要的詳細取樣；沒有任何需要時回傳 None（不排詳細取樣）。"""
    requests = [PAGE_REQUESTS[page] for page in pages if page in PAGE_REQUESTS]
    return reduce(DetailRequest.union, requests) if requests else None


def storage_visible(pages):
    """總覽或磁碟頁顯示中：lsblk 改為每 30 秒。"""
    return any(page in STORAGE_PAGES for page in pages)


class DetailGate:
    """目前的詳細取樣需求與世代；需求改變（含關閉視窗）時世代加一。"""

    def __init__(self):
        self.request, self.generation = None, 0

    def update(self, request):
        if request == self.request:
            return False
        self.request, self.generation = request, self.generation + 1
        return True

    def ticket(self):
        return None if self.request is None else self.generation

    def accepts(self, ticket):
        return self.request is not None and ticket == self.generation
