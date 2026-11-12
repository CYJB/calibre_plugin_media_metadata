# 跨平台的 ffmpeg/ffprobe 定位与子进程封装。
#
# 刻意不 import calibre，也不使用 Python 3.10+ 语法，这样可以用普通系统解释器跑单测，
# 并且能原样复制进两个插件 zip（calibre 一个 zip 只注册一个插件类，两个插件之间
# 不能互相 import）。

from __future__ import annotations

import glob as _glob
import os
import re
import shutil
import subprocess
import sys

MEDIA_EXTENSIONS = frozenset({
    'mp3', 'm4a', 'm4b', 'mp4', 'm4v', 'mov', 'mkv', 'flac',
    'ogg', 'opus', 'wav', 'aiff', 'aif', 'wma', 'webm', 'avi',
})
# '.ts' 刻意不收录：在电子书管理器里它更可能是 TypeScript，而不是 MPEG 传输流。

# 探测顺序里的候选路径。`{name}` 是裸的二进制名。
# `%VAR%` 按环境变量展开（Windows），`~` 展开成家目录（POSIX）。
# 带 `*` 的条目用 glob 展开，取版本最新的那个。
# macOS 这一串按 Apple silicon 排序：arm64 的 Homebrew 在 /opt/homebrew，
# Intel 的在 /usr/local，两个都探，arm64 在前。
_WINDOWS_CANDIDATES = (
    r'C:\ffmpeg\bin\{name}.exe',
    r'C:\Program Files\ffmpeg\bin\{name}.exe',
    r'C:\Program Files (x86)\ffmpeg\bin\{name}.exe',
    r'%LOCALAPPDATA%\Microsoft\WinGet\Links\{name}.exe',
    r'%LOCALAPPDATA%\Microsoft\WinGet\Packages\Gyan.FFmpeg*\ffmpeg-*\bin\{name}.exe',
    r'%ChocolateyInstall%\bin\{name}.exe',
    r'C:\ProgramData\chocolatey\bin\{name}.exe',
    r'%USERPROFILE%\scoop\shims\{name}.exe',
    r'C:\ProgramData\scoop\shims\{name}.exe',
    # 手工安装：把官方 release zip 解压到用户目录或 Local\Programs 的那种。
    r'%USERPROFILE%\ffmpeg\bin\{name}.exe',
    r'%LOCALAPPDATA%\Programs\ffmpeg\bin\{name}.exe',
)

_MACOS_CANDIDATES = (
    '/opt/homebrew/bin/{name}',
    '/usr/local/bin/{name}',
    '/opt/local/bin/{name}',
    '/usr/bin/{name}',
)

_LINUX_CANDIDATES = (
    '/usr/bin/{name}',
    '/usr/local/bin/{name}',
    '/snap/bin/{name}',
    '~/.local/bin/{name}',
)


def _natural_key(text):
    """排序键，让 `ffmpeg-6.0` 排在 `ffmpeg-7.1` 前面。

    每个元素都是 (类型, 数字, 字符串) 三元组，保证这个键是全序的；直接混用 int 和 str
    比较时会抛 TypeError。
    """
    parts = re.split(r'(\d+)', text)
    return [(0, int(p), '') if p.isdigit() else (1, 0, p.lower()) for p in parts]


def _expand(pattern, env, home):
    """展开候选路径里的 %VAR% 和 ~。必需的环境变量没设置时返回 None，
    调用方据此跳过这个候选。
    """
    result = pattern

    def sub_windows(match):
        var = match.group(1)
        value = env.get(var) or env.get(var.upper())
        return '\x00' if value is None else value

    if '%' in result:
        result = re.sub(r'%([^%\\]+)%', sub_windows, result)
        if '\x00' in result:
            return None
    if '~' in result and home:
        result = result.replace('~', home, 1)
    return result


def candidate_paths(name, platform, env, home, globber=None):
    """按裸二进制名给出候选绝对路径，最好的在前。glob 命中项按版本从新到旧排序。"""
    if globber is None:
        globber = _glob.glob
    if platform == 'win32':
        templates = _WINDOWS_CANDIDATES
    elif platform == 'darwin':
        templates = _MACOS_CANDIDATES
    else:
        templates = _LINUX_CANDIDATES

    for template in templates:
        pattern = _expand(template, env, home)
        if not pattern:
            continue
        pattern = pattern.replace('{name}', name)
        if '*' in pattern or '?' in pattern:
            matches = sorted(globber(pattern), key=_natural_key, reverse=True)
            yield from matches
        else:
            yield pattern


def _is_absolute(path):
    if os.path.isabs(path):
        return True
    # 盘符路径在 Windows 上是绝对路径，即使这段代码跑在别的主机上；否则 abspath() 会把它弄坏。
    return bool(re.match(r'^[A-Za-z]:[\\/]', path or ''))


def find_binary(
    name,
    platform=None,
    env=None,
    home=None,
    explicit=None,
    path_lookup=None,
    isfile=None,
    candidates=None,
):
    """按裸二进制名定位可执行文件。

    所有协作者都可注入，这样平台分支能在任何主机上测。返回绝对路径，找不到时返回 None。

    :param explicit: 用户手工配置的路径，优先级最高。
    :param path_lookup: callable(name) -> path|None，默认 shutil.which。
    :param candidates: callable(name, platform, env, home) -> 路径可迭代对象。
    """
    if platform is None:
        platform = sys.platform
    if env is None:
        env = os.environ
    if home is None:
        home = os.path.expanduser('~')
    if path_lookup is None:
        path_lookup = shutil.which
    if isfile is None:
        isfile = os.path.isfile
    if candidates is None:
        candidates = candidate_paths

    if explicit:
        expanded = _expand(explicit, env, home) or explicit
        if isfile(expanded):
            return expanded if _is_absolute(expanded) else os.path.abspath(expanded)
        # 手填的路径不存在时不能悄悄退回系统里另一个 ffmpeg；用户要的就是这一个。
        return None

    found = path_lookup(name)
    if found and isfile(found):
        return found

    for path in candidates(name, platform, env, home):
        if isfile(path):
            return path
    return None


_cache = {}


def clear_cache():
    _cache.clear()


def get_binary(name, explicit=None):
    """用当前环境缓存的 find_binary 包装。"""
    key = (name, explicit or '')
    if key not in _cache:
        _cache[key] = find_binary(name, explicit=explicit)
    return _cache[key]


# calibre 的 make_long_path_useable() 在 Windows 上会生成 `\\?\C:\...`（网络书库则是
# `\\?\UNC\srv\share`）。ffmpeg 两种形式都不认、打不开文件，所以每个 argv 元素
# 都先过这里。
_EXTENDED_PREFIX = '\\\\?\\'
_EXTENDED_UNC_PREFIX = '\\\\?\\UNC\\'


def strip_windows_long_path_prefix(path):
    if not path or not isinstance(path, str):
        return path
    if path.startswith(_EXTENDED_UNC_PREFIX):
        # \\?\UNC\server\share\x -> \\server\share\x
        return '\\\\' + path[len(_EXTENDED_UNC_PREFIX):]
    if path.startswith(_EXTENDED_PREFIX):
        return path[len(_EXTENDED_PREFIX):]
    return path


def _subprocess_kwargs():
    # 工具的 stdin 一律关掉：ffmpeg 只有在明确要求时才读管道，而 GUI 版 calibre 可能给
    # 子进程一个无效的 stdin 句柄，某些 Windows 版 ffmpeg 会因此直接退出。
    kwargs = {
        'shell': False,
        'stdin': subprocess.DEVNULL,
        'stdout': subprocess.PIPE,
        'stderr': subprocess.PIPE,
    }
    if sys.platform == 'win32':
        # 没有这一句，每次探测都会闪一个控制台窗口。
        flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
        if flags:
            kwargs['creationflags'] = flags
    return kwargs


class ToolError(Exception):
    """工具调用失败。

    `message` 是英文的技术描述，只进日志和异常链——ffutil 不能 import calibre，
    所以它自己没法翻译。界面要显示什么，由 `kind` 决定：调用方（binaries.py）
    按 kind 组装中文文案，再把 ffmpeg 自己的 stderr 原样附在后面。
    """

    def __init__(self, message, returncode=None, stderr='', kind='failed',
                 path=None, detail='', timeout=None):
        Exception.__init__(self, message)
        self.returncode = returncode
        self.stderr = stderr
        # 'not_found' / 'cannot_run' / 'timeout' / 'failed'
        self.kind = kind
        self.path = path
        self.detail = detail
        self.timeout = timeout


def run_tool(argv, timeout=600, text=True):
    """按 argv 列表运行工具。从不经过 shell，所以带空格的路径是安全的，也没有 shell 注入问题。

    返回 stdout（text=True 时是 str，否则是 bytes）。非零退出或超时抛 ToolError。
    """
    argv = [strip_windows_long_path_prefix(a) if isinstance(a, str) else a for a in argv]
    kwargs = _subprocess_kwargs()
    if text:
        # ffprobe 输出是 UTF-8；按本地代码页解码会把中文标签解成乱码。
        kwargs.update({'encoding': 'utf-8', 'errors': 'replace'})
    try:
        proc = subprocess.run(argv, timeout=timeout, **kwargs)
    except FileNotFoundError:
        raise ToolError('Executable not found: %s' % argv[0],
                        kind='not_found', path=argv[0])
    except subprocess.TimeoutExpired:
        raise ToolError('Timed out after %ss: %s' % (timeout, ' '.join(argv[:3])),
                        kind='timeout', path=argv[0], timeout=timeout)
    except OSError as err:
        # Windows 把不能执行的文件（或者指向目录的路径）报成 OSError/WinError 193，
        # 而不是 FileNotFoundError。
        raise ToolError('Could not run %s: %s' % (argv[0], err),
                        kind='cannot_run', path=argv[0], detail=str(err))
    if proc.returncode != 0:
        stderr = proc.stderr or ''
        if not isinstance(stderr, str):
            stderr = stderr.decode('utf-8', 'replace')
        raise ToolError(
            '%s failed with code %d: %s' % (os.path.basename(argv[0]), proc.returncode, stderr.strip()[:2000]),
            returncode=proc.returncode,
            stderr=stderr,
            kind='failed',
            path=argv[0],
        )
    return proc.stdout
