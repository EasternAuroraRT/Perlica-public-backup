import os
import shutil

from modules.core.logger import log

# ========== 模块级状态 ==========
_root = ""
_cwd = ""

def init(root_path: str) -> None:
    """初始化文件管理器，设置根目录"""
    global _root, _cwd
    _root = os.path.abspath(root_path)
    os.makedirs(_root, exist_ok=True)
    _cwd = ""

def get_root() -> str:
    """返回根目录绝对路径"""
    return _root

# ========== 内部辅助函数 ==========
def _resolve_path(path: str = "") -> str:
    if not path:
        target = _cwd
    else:
        if path.startswith('/') or path.startswith('\\'):
            target = path.lstrip('/\\')
        else:
            target = os.path.normpath(os.path.join(_cwd, path))
    abs_path = os.path.join(_root, target)
    abs_path = os.path.normpath(abs_path)
    if not abs_path.startswith(_root):
        raise PermissionError(f"Access Denied: you have no permission to path '{path}'.")
    return abs_path

def _format_path(internal_path: str) -> str:
    if not internal_path:
        return "/"
    return "/" + internal_path.replace(os.sep, '/')

# ========== 文件操作 ==========
def write_file(path: str, mode: str, content: str) -> None:
    abs_path = _resolve_path(path)
    parent_dir = os.path.dirname(abs_path)
    if parent_dir:
        os.makedirs(parent_dir, exist_ok=True)
    write_mode = 'w' if mode == 'w' else 'a'
    with open(abs_path, mode=write_mode, encoding='utf-8') as f:
        f.write(content)

def read_file(path: str) -> str:
    try:
        abs_path = _resolve_path(path)
    except PermissionError as e:
        log.error(f"[file_manager->read_file] {e}")
        return ""
    if not os.path.isfile(abs_path):
        return ""
    with open(abs_path, encoding='utf-8') as f:
        return f.read()

# ========== 目录操作 ==========
def cd(path: str) -> str:
    global _cwd
    error_str = f"cd: {path}: No such file or directory"
    if not path:
        return _format_path(_cwd)
    try:
        new_abs_path = _resolve_path(path)
    except PermissionError as e:
        return str(e)
    if not os.path.isdir(new_abs_path):
        return error_str
    _cwd = os.path.relpath(new_abs_path, _root)
    if _cwd == '.':
        _cwd = ""
    return _format_path(_cwd)

def pwd() -> str:
    """返回当前工作目录"""
    return _format_path(_cwd)

def ls(path: str = "") -> str:
    error_str = f"ls: cannot access '{path}': No such file or directory"
    try:
        abs_path = _resolve_path(path)
    except PermissionError as e:
        return str(e)
    if not os.path.isdir(abs_path):
        return error_str
    entries = os.listdir(abs_path)
    entries.sort()
    return "\n".join(entries)

def mkdir(path: str) -> str:
    error_permission_denied = f"mkdir: cannot create directory '{path}': Permission denied"
    try:
        abs_path = _resolve_path(path)
    except PermissionError:
        return error_permission_denied
    os.makedirs(abs_path, exist_ok=True)
    rel_path = os.path.relpath(abs_path, _root)
    if rel_path == '.':
        return "/"
    return "/" + rel_path.replace(os.sep, '/')

def rm(path: str, recursive: bool = False, force: bool = False) -> str:
    success_str = f"rm: {path} removed successfully"
    error_str = f"rm: cannot remove '{path}': "
    error_not_exist = "No such file or directory"
    error_dir = "Is a directory"
    try:
        abs_path = _resolve_path(path)
    except PermissionError as e:
        return str(e)
    if not os.path.exists(abs_path):
        if force:
            return success_str
        return error_str + error_not_exist
    if os.path.isfile(abs_path):
        os.remove(abs_path)
        return success_str
    elif os.path.isdir(abs_path):
        if recursive:
            shutil.rmtree(abs_path)
            return success_str
        else:
            if os.listdir(abs_path):
                return error_str + error_dir
            os.rmdir(abs_path)
            return success_str
    else:
        return error_str + error_not_exist

def mv(src: str, dst: str) -> str:
    success_str = f"mv: {src} moved to {dst} successfully"
    error_cannot_found = f"mv: cannot stat '{src}': No such file or directory"
    error_permission_denied = f"mv: cannot move '{src}' to '{dst}': Permission denied"
    try:
        abs_src = _resolve_path(src)
        abs_dst = _resolve_path(dst)
    except PermissionError:
        return error_permission_denied
    if not os.path.exists(abs_src):
        return error_cannot_found
    dst_parent = os.path.dirname(abs_dst)
    if dst_parent and not os.path.exists(dst_parent):
        return error_cannot_found
    if abs_src == abs_dst:
        return success_str
    try:
        shutil.move(abs_src, abs_dst)
    except Exception as e:
        return str(e)
    return success_str

def cp(src: str, dst: str, recursive: bool = False) -> str:
    success_str = f"cp: {src} copied to {dst} successfully"
    error_cannot_found = f"cp: cannot stat '{src}': No such file or directory"
    error_is_dir = f"cp: 'recursive' not specified; omitting directory '{src}'"
    error_permission_denied = f"cp: cannot copy '{src}' to '{dst}': Permission denied"
    try:
        abs_src = _resolve_path(src)
        abs_dst = _resolve_path(dst)
    except PermissionError:
        return error_permission_denied
    if not os.path.exists(abs_src):
        return error_cannot_found
    dst_parent = os.path.dirname(abs_dst)
    if dst_parent and not os.path.exists(dst_parent):
        return error_cannot_found
    if abs_src == abs_dst:
        return success_str
    if os.path.isdir(abs_src) and not recursive:
        return error_is_dir
    if os.path.isdir(abs_dst):
        target = os.path.join(abs_dst, os.path.basename(abs_src))
    else:
        target = abs_dst
    try:
        if os.path.isfile(abs_src):
            shutil.copy2(abs_src, target)
        elif os.path.isdir(abs_src):
            if os.path.exists(target) and not os.path.isdir(target):
                return error_is_dir
            shutil.copytree(abs_src, target, dirs_exist_ok=True)
        else:
            return error_cannot_found
    except Exception as e:
        return str(e)
    return success_str

init("/")

# ========== 示例用法 ==========
if __name__ == "__main__":

    print("根目录绝对路径:", get_root())
    print("当前目录:", cd("."))

    # 1. 写入文件 (自动创建父目录)
    write_file("a/b/test.txt", "w", "Hello")
    print("写入 a/b/test.txt 成功")

    # 2. 读取存在的文件
    print("读取内容:", read_file("a/b/test.txt"))

    # 3. 读取不存在的文件 → 返回空字符串
    print("读取不存在的文件:", repr(read_file("not_exist.txt")))

    # 4. cd 到不存在的目录 → 返回错误信息
    print("cd 到不存在的目录:", cd("no_such_dir"))

    # 5. ls 不存在的目录 → 返回错误信息
    print("ls 不存在的目录:", ls("no_such_dir"))

    # 6. 创建目录 mkdir
    result = mkdir("new_folder/inner")
    print("创建目录 new_folder/inner, 结果:", result)
    print("当前目录内容:", ls())

    # 7. cd 正常工作
    print("切换到 new_folder:", cd("new_folder"))
    print("当前目录内容:", ls())

    # 8. 多次 cd .. 不会产生路径冗余
    cd("inner")
    cd("../..")
    print("回到根目录:", cd("."))