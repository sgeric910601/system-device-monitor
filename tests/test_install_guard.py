"""原始碼安裝的安全檢查（sdm.services.install_guard）：使用者私人群組的判斷與 git 目錄的遞迴檢查。

帳號與群組資料庫、檔案系統都以假的代替；不需要真正的 git 目錄。
"""
import errno
import stat
from types import SimpleNamespace
import unittest

from sdm.services import install_guard as guard

ME, GID = 1000, 1000
TOP = "/src/app/.git"


def account(name, uid, gid):
    return SimpleNamespace(pw_name=name, pw_uid=uid, pw_gid=gid)


def group(name, gid, members=()):
    return SimpleNamespace(gr_name=name, gr_gid=gid, gr_mem=list(members))


class PrivateGroup(unittest.TestCase):
    def private(self, accounts, groups, uid=ME):
        users = {entry.pw_uid: entry for entry in accounts}
        by_gid = {entry.gr_gid: entry for entry in groups}

        def getpwuid(value):
            return users[value]

        def getgrgid(value):
            return by_gid[value]
        return guard.user_private_gid(uid, getpwuid=getpwuid, getgrgid=getgrgid,
                                      getpwall=lambda: list(accounts))

    def test_user_private_group_is_recognized(self):
        accounts = [account("eric", ME, GID), account("root", 0, 0), account("bob", 1001, 1001)]
        groups = [group("eric", GID), group("root", 0), group("bob", 1001)]
        self.assertEqual(self.private(accounts, groups), GID)

    def test_group_shared_as_another_accounts_primary_group_is_refused(self):
        accounts = [account("eric", ME, GID), account("bob", 1001, GID)]
        self.assertIsNone(self.private(accounts, [group("eric", GID)]))

    def test_other_shapes_are_refused(self):
        cases = (([account("eric", ME, GID)], [group("eric", GID, ["bob"])]),
                 ([account("eric", ME, GID)], [group("users", GID)]),
                 ([account("eric", ME, 100)], [group("users", 100)]),
                 ([], [group("eric", GID)]),
                 ([account("eric", ME, GID)], []))
        for accounts, groups in cases:
            with self.subTest(accounts=accounts, groups=groups):
                self.assertIsNone(self.private(accounts, groups))


def node(kind="dir", uid=ME, mode=None, gid=GID):
    bits = {"dir": stat.S_IFDIR, "file": stat.S_IFREG, "link": stat.S_IFLNK,
            "fifo": stat.S_IFIFO}[kind]
    default = 0o755 if kind == "dir" else 0o644
    return SimpleNamespace(st_mode=bits | (default if mode is None else mode), st_uid=uid,
                           st_gid=gid)


class FakeGitDir:
    """路徑 → 假的 stat；目錄的內容由路徑推導；packed-refs 的內容另外指定。"""

    def __init__(self, entries, packed=b"", acls=None):
        self.entries = {TOP: node()}
        for relative, value in entries.items():
            self.entries[TOP + "/" + relative] = value
        self.packed = packed
        self.acls = {TOP + "/" + name: value for name, value in (acls or {}).items()}

    def listxattr(self, path):
        value = self.acls.get(path, [])
        if isinstance(value, BaseException):
            raise value
        return value

    def lstat(self, path):
        try:
            return self.entries[path]
        except KeyError:
            raise FileNotFoundError(path) from None

    def listdir(self, path):
        prefix = path + "/"
        return sorted({key[len(prefix):].split("/")[0] for key in self.entries
                       if key.startswith(prefix)})

    def read(self, path):
        return self.packed


CLEAN = {"HEAD": node("file"), "config": node("file"), "refs": node(), "refs/heads": node(),
         "refs/heads/main": node("file"), "objects": node(), "objects/ab": node(),
         "objects/ab/cdef": node("file", mode=0o444), "objects/info": node(),
         "objects/pack": node(), "info": node(), "info/exclude": node("file"),
         "hooks": node(), "hooks/pre-commit": node("file", uid=4321, mode=0o777)}


class GitData(unittest.TestCase):
    def scan(self, changes=None, packed=b"", private_gid=GID, remove=(), acls=None,
             check_acls=True):
        entries = dict(CLEAN, **(changes or {}))
        for name in remove:
            entries.pop(name)
        tree = FakeGitDir(entries, packed, acls)
        guard.scan_git_dir(TOP, ME, private_gid, tree.lstat, tree.listdir, tree.read,
                           tree.listxattr if check_acls else None)

    def refused(self, *args, **options):
        with self.assertRaises(guard.ComponentError):
            self.scan(*args, **options)

    def test_clean_repository_passes_and_hooks_are_ignored(self):
        self.scan()

    def test_every_entry_must_belong_to_the_user_or_root(self):
        for name in ("HEAD", "config", "refs/heads/main", "objects/ab/cdef", "info/exclude",
                     "objects/ab"):
            with self.subTest(name=name):
                kind = "dir" if name == "objects/ab" else "file"
                self.refused({name: node(kind, uid=4321)})
        self.scan({"objects/ab/cdef": node("file", uid=0)})

    def test_writable_by_others_is_refused(self):
        self.refused({"refs/heads/main": node("file", mode=0o646)})
        self.refused({"objects": node(mode=0o757)})

    def test_group_writable_only_for_the_private_group(self):
        self.scan({"config": node("file", mode=0o664)})
        self.refused({"config": node("file", mode=0o664, gid=4242)})
        self.refused({"config": node("file", mode=0o664)}, private_gid=None)

    def test_acls_disable_the_private_group_relaxation(self):
        writable = {"config": node("file", mode=0o664), "objects": node(mode=0o775)}
        self.scan(writable)
        self.refused(writable, acls={"config": ["system.posix_acl_access"]})
        self.refused(writable, acls={"objects": ["user.note", "system.posix_acl_default"]})
        self.scan(writable, acls={"config": ["user.note", "security.selinux"]})

    def test_acls_on_entries_that_need_no_relaxation_do_not_matter(self):
        self.scan(acls={"config": ["system.posix_acl_access"],
                        "objects": ["system.posix_acl_default"]})

    def test_unsupported_xattrs_mean_no_acl_but_other_errors_refuse(self):
        writable = {"config": node("file", mode=0o664)}
        for code in (errno.ENOTSUP, errno.EOPNOTSUPP):
            self.scan(writable, acls={"config": OSError(code, "unsupported")})
        self.refused(writable, acls={"config": OSError(errno.EACCES, "denied")})

    def test_without_an_acl_check_there_is_no_relaxation(self):
        self.refused({"config": node("file", mode=0o664)}, check_acls=False)

    def test_acl_on_a_group_writable_parent_is_refused(self):
        entries = {"/": node(uid=0), "/src": node(uid=0), "/src/app": node(mode=0o775)}

        def lstat(path):
            return entries[path]
        self.assertTrue(guard.safe_chain("/src/app", ME, lstat, GID, listxattr=lambda p: []))
        self.assertFalse(guard.safe_chain(
            "/src/app", ME, lstat, GID,
            listxattr=lambda p: ["system.posix_acl_access"] if p == "/src/app" else []))
        self.assertFalse(guard.safe_chain("/src/app", ME, lstat, GID))

    def test_links_and_special_files_are_refused(self):
        self.refused({"refs/heads/main": node("link", mode=0o777)})
        self.refused({"info/fifo": node("fifo")})

    def test_content_redirections_are_refused(self):
        for name in ("objects/info/alternates", "objects/info/http-alternates", "info/grafts"):
            with self.subTest(name=name):
                self.refused({name: node("file")})
        self.refused({"refs/replace": node(), "refs/replace/0123abcd": node("file")})
        self.scan({"refs/replace": node()})
        self.refused({"packed-refs": node("file")},
                     packed=b"# pack-refs with: peeled\n0123 refs/replace/4567\n")
        self.scan({"packed-refs": node("file")}, packed=b"0123 refs/heads/main\n")

    def test_unreadable_directory_is_refused(self):
        tree = FakeGitDir(CLEAN)

        def broken(path):
            if path.endswith("/objects"):
                raise PermissionError(path)
            return tree.listdir(path)
        with self.assertRaises(guard.ComponentError):
            guard.scan_git_dir(TOP, ME, GID, tree.lstat, broken, tree.read, tree.listxattr)


if __name__ == "__main__":
    unittest.main()
