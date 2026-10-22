"""各取樣層的重複計時器：間隔改變的層才重新排程，舊的計時器一定先移除。

計時器由外部提供：add(秒, 層名稱) 回傳來源代號，remove(代號) 移除；
介面使用 GLib，測試以假的計時器檢查沒有漏掉任何來源。
"""


class TierScheduler:
    def __init__(self, add, remove):
        self.add, self.remove = add, remove
        self.active = {}        # 層名稱 →（秒、來源代號）

    def apply(self, intervals):
        """intervals：層名稱 → 秒（None 或沒有列出代表停止）。

        回傳這次新開始（之前沒有計時器）的層，呼叫端可以立即取樣一次。
        """
        started = []
        for kind in [kind for kind in self.active if intervals.get(kind) is None]:
            self.remove(self.active.pop(kind)[1])
        for kind, seconds in intervals.items():
            if seconds is None:
                continue
            current = self.active.get(kind)
            if current is not None and current[0] == seconds:
                continue
            if current is None:
                started.append(kind)
            else:
                self.remove(current[1])
            self.active[kind] = (seconds, self.add(seconds, kind))
        return tuple(started)

    def interval(self, kind):
        current = self.active.get(kind)
        return None if current is None else current[0]

    def stop(self):
        for seconds, source in self.active.values():
            self.remove(source)
        self.active = {}
