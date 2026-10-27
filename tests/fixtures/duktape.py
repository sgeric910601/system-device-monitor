"""以 polkitd 使用的 JavaScript 引擎（libduktape）執行 polkit 規則檔（只在測試中使用）。

Ubuntu 的 polkit 124 以 duktape 2.7 執行 /etc/polkit-1/rules.d 的規則；這裡以 ctypes 載入同一個
函式庫，先執行與 polkitd 內建 init.js 相同的 polkit、Action 定義（Action.lookup 讀
_detail_<名稱>），再載入規則檔，最後對每個情境呼叫 polkit._runRules。
沒有安裝 libduktape 的電腦上 AVAILABLE 為 False，相關測試略過。
"""
import ctypes
import json

LIBRARY = "libduktape.so.207"
COMPILE_EVAL = 1 << 3           # duktape.h 的 DUK_COMPILE_*（duk_peval_string 用的組合）
COMPILE_SAFE = 1 << 7
COMPILE_NOSOURCE = 1 << 9
COMPILE_STRLEN = 1 << 10
COMPILE_NOFILENAME = 1 << 11
PEVAL = COMPILE_EVAL | COMPILE_SAFE | COMPILE_NOSOURCE | COMPILE_STRLEN | COMPILE_NOFILENAME
INIT = r"""
var polkit = {_ruleFuncs: []};
polkit.Result = {NO: "no", YES: "yes", AUTH_SELF: "auth_self", AUTH_SELF_KEEP: "auth_self_keep",
                 AUTH_ADMIN: "auth_admin", AUTH_ADMIN_KEEP: "auth_admin_keep", NOT_HANDLED: null};
polkit.addRule = function (callback) { this._ruleFuncs.push(callback); };
polkit._runRules = function (action, subject) {
    var ret = null;
    for (var n = 0; n < this._ruleFuncs.length; n++) {
        var func_ret = this._ruleFuncs[n](action, subject);
        if (func_ret) { ret = func_ret; break; }
    }
    return ret;
};
function Action(id, details) {
    this.id = id;
    for (var key in details) { this["_detail_" + key] = details[key]; }
    this.lookup = function (name) { return this["_detail_" + name]; };
}
"""
RUN = r"""
(function () {
    var results = [];
    for (var i = 0; i < CASES.length; i++) {
        var item = CASES[i];
        var result = polkit._runRules(new Action(item[0], item[1]), item[2]);
        results.push(result === null || result === undefined ? "none" : String(result));
    }
    return JSON.stringify(results);
})();
"""


def load():
    try:
        library = ctypes.CDLL(LIBRARY)
    except OSError:
        return None
    library.duk_create_heap.restype = ctypes.c_void_p
    library.duk_create_heap.argtypes = [ctypes.c_void_p] * 5
    library.duk_eval_raw.restype = ctypes.c_int
    library.duk_eval_raw.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_size_t,
                                     ctypes.c_uint]
    library.duk_safe_to_lstring.restype = ctypes.c_char_p
    library.duk_safe_to_lstring.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p]
    library.duk_destroy_heap.argtypes = [ctypes.c_void_p]
    return library


DUK = load()
AVAILABLE = DUK is not None


class ScriptError(Exception):
    """規則檔無法編譯或執行時丟出的錯誤（訊息是 duktape 的錯誤文字）。"""


def run(source):
    """執行一段程式並回傳最後一個運算式的值（字串）。"""
    heap = DUK.duk_create_heap(None, None, None, None, None)
    try:
        code = DUK.duk_eval_raw(heap, source.encode("utf-8"), 0, PEVAL)
        text = DUK.duk_safe_to_lstring(heap, -1, None).decode("utf-8", "replace")
    finally:
        DUK.duk_destroy_heap(heap)
    if code != 0:
        raise ScriptError(text)
    return text


def decide(rule_text, cases):
    """cases 為（動作 ID、細節、subject）；回傳每個情境的結果（"yes"、"none" 等）。"""
    script = INIT + rule_text + "\nvar CASES = " + json.dumps(cases) + ";\n" + RUN
    return json.loads(run(script))
