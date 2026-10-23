"""測試用的假根目錄：目錄一律 0755、檔案 0644，擁有者是執行測試的使用者。

管理員小程式要求路徑上的每一層都屬於指定的擁有者且群組與其他人不可寫；
測試把「root」換成目前的使用者，以暫存目錄代替 /。
"""
import os
from pathlib import Path
import tempfile

DIRECTORY_MODE = 0o755
FILE_MODE = 0o644


class FakeRoot:
    def __init__(self, case):
        temp = tempfile.TemporaryDirectory()
        case.addCleanup(temp.cleanup)
        self.path = Path(temp.name)/"root"
        self.path.mkdir()
        self.path.chmod(DIRECTORY_MODE)

    def directory(self, relative):
        current = self.path
        for part in Path(relative).parts:
            current = current/part
            if not current.exists():
                current.mkdir()
                current.chmod(DIRECTORY_MODE)
        return current

    def file(self, relative, text="synthetic\n", mode=FILE_MODE):
        path = self.path/relative
        self.directory(path.parent.relative_to(self.path))
        path.write_text(text)
        path.chmod(mode)
        return path

    def mode(self, relative):
        return os.stat(self.path/relative, follow_symlinks=False).st_mode & 0o7777
