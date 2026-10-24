"""假的計時器：schedule(秒, 回呼) 回傳來源代號，cancel(來源代號) 取消，advance() 依時間順序
觸發到期的回呼（回呼回傳 True 時與 GLib 相同，會再排一次）。"""


class FakeTimers:
    def __init__(self):
        self.now, self.pending, self.next_id = 0.0, {}, 1

    def schedule(self, seconds, callback):
        source = self.next_id
        self.next_id += 1
        self.pending[source] = (self.now + seconds, seconds, callback)
        return source

    def cancel(self, source):
        self.pending.pop(source, None)

    def advance(self, seconds):
        end = self.now + seconds
        while True:
            due = sorted((when, source) for source, (when, _, _) in self.pending.items()
                         if when <= end)
            if not due:
                break
            when, source = due[0]
            self.now = when
            _, interval, callback = self.pending.pop(source)
            if callback():
                self.pending[source] = (self.now + interval, interval, callback)
        self.now = end
