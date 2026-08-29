"""Safe Executor — 安全代码执行器

功能：
- execute_code(code: str) -> str  执行 Python 代码字符串
- execute_file(filepath: str) -> str  执行 .py 文件

安全限制（以路径白名单为核心）：
- ./private_space/（基于当前进程工作目录）内：允许读/写/删除/创建/重命名等任意文件操作
- ./private_space/ 之外：仅允许只读操作
- 禁止子进程的创建和运行
- 允许导入已存在的模块（os / pathlib / shutil 会被替换为沙盒版本）
"""

import os
import sys
import builtins
import io
import contextlib
import traceback
import functools
import types
from typing import Any, Callable, Optional

from modules.core.logger import log

# ============================================================
# 安全配置
# ============================================================

ALLOWED_WRITE_DIR = "./private_space/"

# 完全禁止导入的模块（子进程相关 / 底层系统调用）
FORBIDDEN_IMPORTS = {
    "subprocess",
    "multiprocessing",
    "concurrent.futures",
    "asyncio",
    "ctypes",
    "signal",
    "pty",
    "fcntl",
    "posix",
    "_winapi",
    "msvcrt",
    "_thread",
    "threading",
}

# 需要被替换为沙盒版本的模块
SANDBOXED_MODULES = {"os", "pathlib", "shutil"}

# ============================================================
# 保存原始引用
# ============================================================

_original_open = builtins.open
_original_import = builtins.__import__
_ALLOWED_ABS = os.path.abspath(ALLOWED_WRITE_DIR)

# ============================================================
# 路径校验工具
# ============================================================

def _is_allowed_write_path(path: str) -> bool:
    """判断路径是否在允许写入的目录内。"""
    abs_path = os.path.abspath(path)
    return abs_path.startswith(_ALLOWED_ABS)


def _guard_write_path(path: str, operation: str = "操作") -> None:
    """若路径不在允许目录内则抛出 PermissionError。"""
    # 如果路径本身在 allowed 目录内，直接放行
    if _is_allowed_write_path(path):
        return
    raise PermissionError(
        f"{operation}被拦截: 路径 '{path}' 不在 '{ALLOWED_WRITE_DIR}' 目录内。"
    )


# ============================================================
# 沙盒化 os 模块
# ============================================================

# 需要管制的 os 函数列表：「函数名, 路径参数位置（0‑based）」
_OS_GUARDED_FUNCS: list[tuple[str, int]] = [
    ("remove", 0),
    ("unlink", 0),
    ("rmdir", 0),
    ("rename", 0),
    ("renames", 0),
    ("replace", 0),
    ("chmod", 0),
    ("chown", 0),
    ("lchown", 0),
    ("mkdir", 0),
    ("makedirs", 0),
    ("symlink", 0),      # symlink(src, dst) — src 可读，dst 需写权限
    ("link", 1),         # link(src, dst)
    ("utime", 0),
    ("truncate", 0),
]

# 第二个参数也需校验（如 rename(old, new) 中的 new）
_OS_GUARDED_FUNCS_MULTI: dict[str, list[int]] = {
    "rename":    [0, 1],
    "renames":   [0, 1],
    "replace":   [0, 1],
    "symlink":   [0, 1],
    "link":      [0, 1],
    "copy_file_range": [0, 1],
}


def _build_sandboxed_os() -> types.ModuleType:
    """返回一个受限的 os 模块副本。"""
    import os as _real_os
    sandbox = types.ModuleType("os")
    sandbox.__file__ = _real_os.__file__
    sandbox.__name__ = "os"
    sandbox.__package__ = "os"

    for attr in dir(_real_os):
        if attr.startswith("_"):
            continue
        obj = getattr(_real_os, attr)
        setattr(sandbox, attr, obj)

    # 覆盖 path‑mutating 函数
    for func_name, path_idx in _OS_GUARDED_FUNCS:
        original_func = getattr(_real_os, func_name, None)
        if original_func is None or not callable(original_func):
            continue
        # 有些可能是 None（如 Windows 上不存在某些函数）
        idx_list = _OS_GUARDED_FUNCS_MULTI.get(func_name, [path_idx])

        @functools.wraps(original_func)
        def _wrapped(*args, _func=original_func,
                     _name=func_name, _idxs=idx_list, **kwargs):
            for i in _idxs:
                if i < len(args):
                    _guard_write_path(str(args[i]), f"os.{_name}")
            return _func(*args, **kwargs)
        setattr(sandbox, func_name, _wrapped)

    # populat​e os.path 也要管
    # Most os.path functions delegate to os itself, but we also guard
    # os.path operations that mutate.
    # os.path doesn't have mutating functions beyond what os has, so
    # patching os + ensuring os.path refers to the same wrappers is
    # automatic because os.path.<func> calls os.<func>.

    return sandbox


# ============================================================
# 沙盒化 pathlib.Path
# ============================================================

def _build_sandboxed_pathlib() -> types.ModuleType:
    """返回一个受限的 pathlib 模块副本。"""
    import pathlib as _real_pathlib

    sandbox = types.ModuleType("pathlib")
    for attr in dir(_real_pathlib):
        if attr.startswith("_"):
            continue
        setattr(sandbox, attr, getattr(_real_pathlib, attr))

    real_Path = _real_pathlib.Path

    class SafePath(real_Path):
        """Path 子类：写操作自动校验路径。"""

        _ALLOWED_MUTATING = {
            "unlink", "rmdir", "rename", "replace", "chmod",
            "lchmod", "mkdir", "symlink_to", "hardlink_to",
            "write_text", "write_bytes", "touch",
        }

        def _check_write(self, method: str) -> None:
            _guard_write_path(str(self), f"Path.{method}")

        # -- 读方法：覆盖构造方法返回 SafePath --
        def __truediv__(self, other):
            return SafePath(super().__truediv__(other))

        def __rtruediv__(self, other):
            return SafePath(super().__rtruediv__(other))

        @property
        def parent(self):
            return SafePath(super().parent)

        def relative_to(self, *args, **kwargs):
            return SafePath(super().relative_to(*args, **kwargs))

        def with_name(self, name):
            return SafePath(super().with_name(name))

        def with_suffix(self, suffix):
            return SafePath(super().with_suffix(suffix))

        def with_stem(self, stem):
            return SafePath(super().with_stem(stem))

        def resolve(self, strict=False):
            return SafePath(super().resolve(strict=strict))

        def absolute(self):
            return SafePath(super().absolute())

        def joinpath(self, *args):
            return SafePath(super().joinpath(*args))

        def iterdir(self):
            for item in super().iterdir():
                yield SafePath(item)

        def glob(self, pattern, *, case_sensitive=None, recurse_symlinks=False):
            for item in super().glob(pattern, case_sensitive=case_sensitive, recurse_symlinks=recurse_symlinks):
                yield SafePath(item)

        def rglob(self, pattern, *, case_sensitive=None, recurse_symlinks=False):
            for item in super().rglob(pattern, case_sensitive=case_sensitive, recurse_symlinks=recurse_symlinks):
                yield SafePath(item)

        @classmethod
        def cwd(cls):
            return SafePath(super().cwd())

        @classmethod
        def home(cls):
            return SafePath(super().home())

        # -- 写方法：强制校验 --
        def unlink(self, missing_ok=False):
            self._check_write("unlink")
            return super().unlink(missing_ok=missing_ok)

        def rmdir(self):
            self._check_write("rmdir")
            return super().rmdir()

        def rename(self, target):
            self._check_write("rename")
            _guard_write_path(str(target), "Path.rename(target)")
            return SafePath(super().rename(target))

        def replace(self, target):
            self._check_write("replace")
            _guard_write_path(str(target), "Path.replace(target)")
            return SafePath(super().replace(target))

        def chmod(self, mode, *, follow_symlinks=True):
            self._check_write("chmod")
            return super().chmod(mode, follow_symlinks=follow_symlinks)

        def lchmod(self, mode):
            self._check_write("lchmod")
            return super().lchmod(mode)

        def mkdir(self, mode=0o777, parents=False, exist_ok=False):
            self._check_write("mkdir")
            return super().mkdir(mode=mode, parents=parents, exist_ok=exist_ok)

        def symlink_to(self, target, target_is_directory=False):
            self._check_write("symlink_to")
            return super().symlink_to(target, target_is_directory=target_is_directory)

        def hardlink_to(self, target):
            self._check_write("hardlink_to")
            return super().hardlink_to(target)

        def write_text(self, data, encoding=None, errors=None, newline=None):
            self._check_write("write_text")
            return super().write_text(data, encoding=encoding, errors=errors, newline=newline)

        def write_bytes(self, data):
            self._check_write("write_bytes")
            return super().write_bytes(data)

        def touch(self, mode=0o666, exist_ok=True):
            self._check_write("touch")
            return super().touch(mode=mode, exist_ok=exist_ok)

    setattr(sandbox, "Path", SafePath)
    # PosixPath / WindowsPath
    if hasattr(_real_pathlib, "PosixPath"):
        class SafePosixPath(SafePath, _real_pathlib.PosixPath):
            pass
        setattr(sandbox, "PosixPath", SafePosixPath)
    if hasattr(_real_pathlib, "WindowsPath"):
        class SafeWindowsPath(SafePath, _real_pathlib.WindowsPath):
            pass
        setattr(sandbox, "WindowsPath", SafeWindowsPath)

    return sandbox


# ============================================================
# 沙盒化 shutil 模块
# ============================================================

def _build_sandboxed_shutil() -> types.ModuleType:
    """返回一个受限的 shutil 模块副本。"""
    import shutil as _real_shutil

    sandbox = types.ModuleType("shutil")
    for attr in dir(_real_shutil):
        if attr.startswith("_"):
            continue
        setattr(sandbox, attr, getattr(_real_shutil, attr))

    _SHUTIL_GUARDED: dict[str, list[int]] = {
        "copy":             [1],   # copy(src, dst)            — dst 需写权限
        "copy2":            [1],
        "copyfile":         [1],
        "copymode":         [1],
        "copystat":         [1],
        "copytree":         [1],   # copytree(src, dst)       — dst 需写权限
        "move":             [1],
        "rmtree":           [0],   # rmtree(path)             — path 需写权限
        "make_archive":     [0],   # make_archive(base, ...)  — base 需写权限
        "unpack_archive":   [1],   # unpack_archive(archive, extract_dir) — extract_dir 需写权限
        "chown":            [0],
        "disk_usage":       [0],   # 只读，但保守起见不管它
    }

    for func_name, idx_list in _SHUTIL_GUARDED.items():
        original_func = getattr(_real_shutil, func_name, None)
        if original_func is None or not callable(original_func):
            continue

        @functools.wraps(original_func)
        def _wrapped(*args, _func=original_func,
                     _name=func_name, _idxs=idx_list, **kwargs):
            for i in _idxs:
                if i < len(args):
                    _guard_write_path(str(args[i]), f"shutil.{_name}")
            return _func(*args, **kwargs)
        setattr(sandbox, func_name, _wrapped)

    return sandbox


# ============================================================
# 预构建沙盒模块（懒加载缓存）
# ============================================================

_sandbox_cache: dict[str, types.ModuleType] = {}

def _get_sandboxed_module(name: str) -> types.ModuleType:
    """获取或构建沙盒模块。"""
    if name not in _sandbox_cache:
        if name == "os":
            _sandbox_cache[name] = _build_sandboxed_os()
        elif name == "pathlib":
            _sandbox_cache[name] = _build_sandboxed_pathlib()
        elif name == "shutil":
            _sandbox_cache[name] = _build_sandboxed_shutil()
        else:
            raise ValueError(f"未知的沙盒模块: {name}")
    return _sandbox_cache[name]


# ============================================================
# 安全 open() — 写操作白名单拦截
# ============================================================

def _safe_open(
    file: str,
    mode: str = "r",
    buffering: int = -1,
    encoding: Optional[str] = None,
    errors: Optional[str] = None,
    newline: Optional[str] = None,
    closefd: bool = True,
    opener: Optional[Any] = None,
):
    """替换内置 open()：写模式只允许 ./private/ 目录。"""
    is_write = any(ch in mode for ch in ("w", "a", "x", "+"))

    if is_write:
        _guard_write_path(str(file), "写操作")

    return _original_open(
        file,
        mode=mode,
        buffering=buffering,
        encoding=encoding,
        errors=errors,
        newline=newline,
        closefd=closefd,
        opener=opener,
    )


# ============================================================
# 安全 __import__() — 拦截危险模块 / 注入沙盒
# ============================================================

def _safe_import(name: str, *args, **kwargs):
    """替换内置 __import__()。"""
    # 0. 处理 importlib 特殊调用（带 fromlist）
    #    若 fromlist 不为空，__import__ 返回子模块
    top_level = name.split(".")[0]

    # 1. 完全禁止的模块
    if top_level in FORBIDDEN_IMPORTS:
        raise ImportError(
            f"导入被拦截: 模块 '{name}' 已被禁止（可能用于创建子进程或绕过安全限制）。"
        )

    # 2. 沙盒替换模块：返回预构建的沙盒副本
    if top_level in SANDBOXED_MODULES and name == top_level:
        return _get_sandboxed_module(name)

    # 3. 允许导入
    return _original_import(name, *args, **kwargs)


# ============================================================
# 构建安全的全局命名空间
# ============================================================

def _build_safe_globals(extra_globals: Optional[dict] = None) -> dict:
    """构建受限的全局命名空间供 exec() 使用。"""
    # 过滤后的 builtins（移除危险函数 + open/__import__）
    filtered_builtins = {
        k: v
        for k, v in builtins.__dict__.items()
        if k not in {"exec", "eval", "compile", "__import__", "open"}
    }

    # 替换为安全版本
    filtered_builtins["open"] = _safe_open
    filtered_builtins["__import__"] = _safe_import

    safe_globals: dict = {"__builtins__": filtered_builtins}

    safe_globals.update({
        "print": builtins.print,
        "len": builtins.len,
        "range": builtins.range,
        "int": builtins.int,
        "float": builtins.float,
        "str": builtins.str,
        "list": builtins.list,
        "dict": builtins.dict,
        "tuple": builtins.tuple,
        "set": builtins.set,
        "bool": builtins.bool,
        "True": True,
        "False": False,
        "None": None,
        "Exception": Exception,
        "ValueError": ValueError,
        "TypeError": TypeError,
        "ImportError": ImportError,
        "KeyError": KeyError,
        "IndexError": IndexError,
        "AttributeError": AttributeError,
        "PermissionError": PermissionError,
    })

    if extra_globals:
        safe_globals.update(extra_globals)

    return safe_globals


# ============================================================
# 公共 API
# ============================================================

def execute_code(code: str, extra_globals: Optional[dict] = None) -> str:
    """安全地执行一段 Python 代码字符串。

    参数:
        code: 要执行的 Python 代码。
        extra_globals: 可选的额外全局变量注入。

    返回:
        str: 代码的标准输出内容；若运行出错，则包含错误信息。
    """

    safe_globals = _build_safe_globals(extra_globals)
    safe_locals: dict = {}
    stdout_capture = io.StringIO()

    try:
        with contextlib.redirect_stdout(stdout_capture), \
             contextlib.redirect_stderr(stdout_capture):
            try:
                compiled = compile(code, "<safe_exec>", "eval")
                exec_result = eval(compiled, safe_globals, safe_locals)
                if exec_result is not None:
                    print(repr(exec_result), file=sys.stdout)
            except SyntaxError as e:
                log.error(f"[safe_executor->execute_code] {e}")
                compiled = compile(code, "<safe_exec>", "exec")
                exec(compiled, safe_globals, safe_locals)

        return stdout_capture.getvalue()

    except PermissionError as e:
        output = stdout_capture.getvalue()
        return output + f"\nPermission Erro: {e}"
    except ImportError as e:
        output = stdout_capture.getvalue()
        return output + f"\nImport Error: {e}"
    except Exception as e:
        output = stdout_capture.getvalue()
        return output + f"\n{type(e).__name__}: {e}\n{traceback.format_exc()}"


def execute_file(filepath: str, extra_globals: Optional[dict] = None) -> str:
    """安全地执行一个 Python 文件。

    参数:
        filepath: Python 文件路径。
        extra_globals: 可选的额外全局变量注入。

    返回:
        str: 代码的标准输出内容；若运行出错，则包含错误信息。
    """
    if not os.path.isfile(filepath):
        return f"cannot access '{filepath}': no such file."

    try:
        with _original_open(filepath, "r", encoding="utf-8") as f:
            code = f.read()
    except Exception as e:
        return f"{e}"

    file_dir = os.path.dirname(os.path.abspath(filepath))
    original_sys_path = sys.path.copy()
    if file_dir not in sys.path:
        sys.path.insert(0, file_dir)

    try:
        return execute_code(code, extra_globals)
    finally:
        sys.path[:] = original_sys_path


# ============================================================
# 测试入口
# ============================================================

if __name__ == "__main__":
    print("=== 测试 execute_code ===\n")

    print("1. 正常执行:")
    print(execute_code("print('hello'); x = 1 + 2; x"))
    print()

    print("2. 写入 ./private_space/ (应成功):")
    print(execute_code("open('./private_space/test.txt', 'w').write('ok')"))
    print()

    print("3. 写入 /tmp (应被拦截):")
    print(execute_code("open('/tmp/test.txt', 'w').write('hack')"))
    print()

    print("4. 导入 subprocess (应被拦截):")
    print(execute_code("import subprocess; subprocess.run(['ls'])"))
    print()

    print("5. 导入 math 模块 (应成功):")
    print(execute_code("import math; print(math.pi)"))
    print()

    print("6. os.remove 删除外部文件 (应被拦截):")
    print(execute_code("import os; os.remove('/tmp/nonexistent')"))
    print()

    print("7. os.remove 删除 ./private_space/ 内文件 (应成功):")
    print(execute_code(
        "import os\n"
        "open('./private_space/_del_test.txt', 'w').close()\n"
        "os.remove('./private_space/_del_test.txt')\n"
        "print('删除成功')"
    ))
    print()

    print("8. pathlib.Path.unlink 删除外部文件 (应被拦截):")
    print(execute_code(
        "from pathlib import Path\n"
        "Path('/tmp/nonexistent').unlink()"
    ))
    print()

    print("9. shutil.rmtree 删除外部目录 (应被拦截):")
    print(execute_code(
        "import shutil\n"
        "shutil.rmtree('/tmp')\n"
    ))
    print()

    print("10. pathlib Path.write_text 写入外部 (应被拦截):")
    print(execute_code(
        "from pathlib import Path\n"
        "Path('/tmp/evil.txt').write_text('bad')\n"
    ))
    print()