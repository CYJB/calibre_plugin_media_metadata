#!/usr/bin/env python3
# 平台管道测试：那些在 Windows 上行为不同的部分。
#
#     python3 tests/test_platform.py
#
# 既不需要 calibre 也不需要 ffmpeg。子进程调用被替换在 subprocess.run 这一层，
# 所以 run_tool() 的真实管道（argv 列表、长路径前缀剥离、Windows 窗口标志、UTF-8 解码）
# 和 ffmeta.write_media() 的真实文件系统操作都被跑到，但不用装任何编码器。

import os
import shutil
import subprocess
import sys
import tempfile
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'common'))

# 这些测试会打印真实文件路径，里面可能有中文。Windows 控制台通常是旧的代码页，
# 打印这些字符会抛 UnicodeEncodeError，把真正要看的结果盖掉。
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='backslashreplace')
except (AttributeError, OSError):
    pass

import ffmeta  # noqa: E402
import ffutil  # noqa: E402

IS_WINDOWS = sys.platform == 'win32'
FAILURES = []


def check(name, condition, detail=''):
    if condition:
        print('  PASS  %s' % name)
    else:
        print('  FAIL  %s %s' % (name, detail))
        FAILURES.append(name)


def eq(name, actual, expected):
    check(name, actual == expected, '(got %r, want %r)' % (actual, expected))


def skip(name, reason):
    print('  SKIP  %s (%s)' % (name, reason))


def workdir():
    """一个普通目录：刻意不用 tempfile.mkdtemp()，它建出来的 0o700 目录在某些 Windows
    沙箱里对其它进程不可读。
    """
    path = os.path.join(tempfile.gettempdir(), 'mm-platform-%s' % uuid.uuid4().hex[:8])
    os.makedirs(path)
    return path


def leftover_temp_files(directory):
    return sorted(n for n in os.listdir(directory) if n.startswith('.'))


# ------------------------------------------------------------ 路径前缀

def test_long_path_prefix():
    print('windows long path prefix')
    eq('strips the extended prefix',
       ffutil.strip_windows_long_path_prefix(r'\\?\C:\Books\song.mp3'), r'C:\Books\song.mp3')
    eq('strips the extended UNC prefix',
       ffutil.strip_windows_long_path_prefix(r'\\?\UNC\server\share\song.mp3'),
       r'\\server\share\song.mp3')
    eq('leaves an ordinary UNC path alone',
       ffutil.strip_windows_long_path_prefix(r'\\server\share\song.mp3'),
       r'\\server\share\song.mp3')
    eq('leaves a plain path alone',
       ffutil.strip_windows_long_path_prefix('C:\\Books\\song.mp3'), 'C:\\Books\\song.mp3')
    eq('leaves a POSIX path alone', ffutil.strip_windows_long_path_prefix('/Books/song.mp3'),
       '/Books/song.mp3')
    eq('handles None', ffutil.strip_windows_long_path_prefix(None), None)
    eq('handles an empty string', ffutil.strip_windows_long_path_prefix(''), '')

    # 按 calibre 的 make_long_path_useable() 方式加前缀的真实路径。
    prefixed = '\\\\?\\' + os.path.abspath(os.path.join(HERE, 'out', 'tagged.mp3'))
    eq('round-trips a fully qualified path', ffutil.strip_windows_long_path_prefix(prefixed),
       os.path.abspath(os.path.join(HERE, 'out', 'tagged.mp3')))


def test_subprocess_kwargs():
    print('subprocess flags')
    kwargs = ffutil._subprocess_kwargs()
    eq('runs without a shell', kwargs.get('shell'), False)
    eq('stdin is closed for the tool', kwargs.get('stdin'), subprocess.DEVNULL)
    eq('stdout is captured', kwargs.get('stdout'), subprocess.PIPE)
    eq('stderr is captured', kwargs.get('stderr'), subprocess.PIPE)
    if IS_WINDOWS:
        eq('no console window is created', kwargs.get('creationflags'),
           getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    else:
        skip('no console window is created', 'Windows only')


# ------------------------------------------------------------ run_tool()

class _FakeProc:
    def __init__(self, returncode=0, stdout='', stderr=''):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def test_run_tool_real_subprocess():
    """用当前解释器驱动真实的子进程路径。"""
    print('run_tool with a real subprocess')
    python = sys.executable
    eq('captures stdout', ffutil.run_tool([python, '-c', 'print("hello")']), 'hello\n')

    directory = workdir()
    odd_dir = os.path.join(directory, 'Boek 作者 & more')
    os.makedirs(odd_dir)
    odd_path = os.path.join(odd_dir, 'tag ged.mp3')
    with open(odd_path, 'wb') as fp:
        fp.write(b'x')

    # 带空格和非 ASCII 字符的绝对路径，作为普通 argv 元素。
    # 子进程按 UTF-8 输出字节，和 ffprobe 一样；run_tool 不管控制台代码页是什么都按
    # UTF-8 解码。
    echo = 'import sys; sys.stdout.buffer.write(sys.argv[1].encode("utf-8"))'
    out = ffutil.run_tool([python, '-c', echo, odd_path])
    eq('argv keeps spaces and non-ASCII intact', out, odd_path)

    # 同一个路径，按 calibre 在 Windows 上会交过来的样子加前缀。
    prefixed = '\\\\?\\' + odd_path
    out = ffutil.run_tool([python, '-c', echo, prefixed])
    eq('argv items are stripped of the long path prefix', out, odd_path)

    eq('text=False returns bytes',
       ffutil.run_tool([python, '-c', 'import sys; sys.stdout.buffer.write(b"raw")'],
                       text=False), b'raw')

    try:
        ffutil.run_tool([python, '-c', 'import sys; sys.stderr.write("boom"); sys.exit(3)'])
    except ffutil.ToolError as err:
        eq('non-zero exit reports the code', err.returncode, 3)
        check('non-zero exit keeps the stderr', 'boom' in str(err), str(err))
    else:
        check('non-zero exit raises ToolError', False)

    missing = os.path.join(directory, 'no-such-tool')
    try:
        ffutil.run_tool([missing])
    except ffutil.ToolError as err:
        check('a missing executable raises ToolError', 'not found' in str(err).lower(), str(err))
    else:
        check('a missing executable raises ToolError', False)

    shutil.rmtree(directory, ignore_errors=True)


# ------------------------------------------------- write_media 落盘

class StubTool:
    """subprocess.run 的替身，行为像一次成功的 ffmpeg 拷贝。"""

    def __init__(self, payload=b'REWRITTEN', returncode=0, stderr=''):
        self.payload = payload
        self.returncode = returncode
        self.stderr = stderr
        self.calls = []

    def __call__(self, argv, **kwargs):
        self.calls.append((list(argv), kwargs))
        if self.returncode == 0:
            with open(argv[-1], 'wb') as fp:
                fp.write(self.payload)
        return _FakeProc(self.returncode, '', self.stderr)


def test_write_media_filesystem():
    """针对真实目录跑 write_media()：同目录临时文件、权限复制、
    原子 os.replace()、两条路径上的清理。
    """
    print('write_media temp file + atomic replace')
    directory = workdir()
    # 按 calibre 的规则造一个书籍目录：标题里有空格和中文，再加一个 id。
    book_dir = os.path.join(directory, 'Author 作者', 'A Book (12)')
    os.makedirs(book_dir)
    original = b'ORIGINAL AUDIO'
    ext = 'mp3'
    path = os.path.join(book_dir, 'A Book - Author.%s' % ext)
    with open(path, 'wb') as fp:
        fp.write(original)

    real_run = subprocess.run
    stub = StubTool()
    subprocess.run = stub
    try:
        media_path = '\\\\?\\' + path if IS_WINDOWS else path
        returned = ffmeta.write_media(
            media_path, {'title': 'New Title'}, ffmeta.COVER_KEEP, None,
            'ffmpeg-stub', opts=ffmeta.WriteOptions(id3v2_version=3))
        eq('returns the path it was given', returned, media_path)
        eq('exactly one tool call', len(stub.calls), 1)
        argv, kwargs = stub.calls[0]
        eq('no shell', kwargs.get('shell'), False)
        eq('ffprobe/ffmpeg output is decoded as UTF-8', kwargs.get('encoding'), 'utf-8')
        if IS_WINDOWS:
            eq('no console window', kwargs.get('creationflags'),
               getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        eq('input path is passed without the long path prefix', argv[5], path)
        check('output is a sibling temp file, not the original',
              argv[-1] != path and os.path.dirname(argv[-1]) == book_dir, argv[-1])
        check('output path has no long path prefix', not argv[-1].startswith('\\\\?\\'),
              argv[-1])
        check('tag update present', 'title=New Title' in argv, argv)
        check('existing tags are preserved', '-map_metadata' in argv, argv)
        check('mp3 id3 version pinned', '3' in argv, argv)
        with open(path, 'rb') as fp:
            eq('file was atomically replaced', fp.read(), stub.payload)
        eq('no temp files left behind', leftover_temp_files(book_dir), [])
    finally:
        subprocess.run = real_run

    # --- 封面路径也会写同目录的临时图片 -----------------------------
    print('write_media cover handling')
    path2 = os.path.join(book_dir, 'Covered %s' % ('x' * 60 + '.m4b'))
    with open(path2, 'wb') as fp:
        fp.write(original)
    stub = StubTool(payload=b'WITH COVER')
    subprocess.run = stub
    try:
        ffmeta.write_media(
            '\\\\?\\' + path2 if IS_WINDOWS else path2, {'title': 'C'},
            ffmeta.COVER_SET, b'\xff\xd8\xff\xe0fake-jpeg', 'ffmpeg-stub',
            cover_ext='jpg', opts=ffmeta.WriteOptions())
        argv = stub.calls[0][0]
        check('cover input added', sum(1 for a in argv if a == '-i') == 2, argv)
        check('new cover stream mapped', '1:v' in argv, argv)
        check('long temp name stays bounded',
              len(os.path.basename(argv[-1])) < 60, os.path.basename(argv[-1]))
        with open(path2, 'rb') as fp:
            eq('file with cover replaced', fp.read(), stub.payload)
        eq('no temp files left after a cover write', leftover_temp_files(book_dir), [])
    finally:
        subprocess.run = real_run

    # --- 工具失败时不能动原文件 ------------------------------------
    print('write_media failure safety')
    path3 = os.path.join(book_dir, 'Broken.mp3')
    with open(path3, 'wb') as fp:
        fp.write(original)
    stub = StubTool(returncode=1, stderr='boom')
    subprocess.run = stub
    try:
        try:
            ffmeta.write_media(path3, {'title': 'Nope'}, ffmeta.COVER_KEEP, None,
                               'ffmpeg-stub')
        except ffutil.ToolError as err:
            check('failed write surfaces a ToolError', err.returncode == 1, err.returncode)
        else:
            check('failed write surfaces a ToolError', False)
        with open(path3, 'rb') as fp:
            eq('original bytes untouched', fp.read(), original)
        eq('no temp files left after a failure', leftover_temp_files(book_dir), [])
    finally:
        subprocess.run = real_run

    shutil.rmtree(directory, ignore_errors=True)


# ------------------------------------------------- write_media 的保守重试

class RejectingStubTool(StubTool):
    """前 N 次调用像「容器拒绝这条轨道」那样失败，之后成功。

    失败信息照抄真实案例（一台 Windows 机器上带未知 text 流的 m4b）。
    """

    def __init__(self, failures=1, payload=b'RETRIED'):
        super().__init__(payload=payload, returncode=0, stderr='')
        self.failures = failures

    def __call__(self, argv, **kwargs):
        argv = list(argv)
        self.calls.append((argv, kwargs))
        if len(self.calls) <= self.failures:
            return _FakeProc(1, '', (
                "Tag text incompatible with output codec id '98314' ([0][0][0][0])\n"
                '[out#0/ipod @ 0] Could not write header (incorrect codec parameters ?): '
                'Invalid data found when processing input\n'))
        with open(argv[-1], 'wb') as fp:
            fp.write(self.payload)
        return _FakeProc(0, '', '')


def test_write_media_retries_tracks_the_container_rejects():
    print('write_media retries by dropping tracks the container cannot hold')
    directory = workdir()
    path = os.path.join(directory, 'A Book.m4b')
    original = b'ORIGINAL AUDIO'
    with open(path, 'wb') as fp:
        fp.write(original)
    streams = [
        {'index': 0, 'type': 'audio', 'codec': 'aac'},
        {'index': 1, 'type': 'data', 'codec': 'unknown'},
    ]

    real_run = subprocess.run
    # 第一次（全部流）和第二次（跳过认不出的流）都失败，第三次（只音视频）成功。
    stub = RejectingStubTool(failures=2)
    subprocess.run = stub
    try:
        outcome = ffmeta.write_media(
            path, {'title': 'Retried'}, ffmeta.COVER_KEEP, None, 'ffmpeg-stub',
            streams=streams)
        eq('three attempts were made', len(stub.calls), 3)
        first, second, third = [call[0] for call in stub.calls]
        check('the first attempt copies every stream', '-0:1' not in first, first)
        check('the second attempt drops the uncopyable stream', '-0:1' in second, second)
        check('the third attempt keeps only audio and video',
              '0:a?' in third and '0:v?' in third, third)
        with open(path, 'rb') as fp:
            eq('the file was replaced after the retry', fp.read(), b'RETRIED')
        eq('the skipped stream is reported', [s.get('codec') for s in outcome.skipped],
           ['unknown'])
        eq('the outcome still behaves like the path', outcome, path)
        eq('no temp files left after a retried write', leftover_temp_files(directory), [])
    finally:
        subprocess.run = real_run

    # 不是容器问题的失败：不重试，原文件保持原样。
    print('write_media does not retry ordinary failures')
    path2 = os.path.join(directory, 'B Book.m4b')
    with open(path2, 'wb') as fp:
        fp.write(original)
    stub = StubTool(returncode=1, stderr='No space left on device')
    subprocess.run = stub
    try:
        try:
            ffmeta.write_media(path2, {'title': 'Nope'}, ffmeta.COVER_KEEP, None,
                               'ffmpeg-stub', streams=streams)
        except ffutil.ToolError:
            pass
        else:
            check('a disk error still raises', False)
        eq('a plain failure is not retried', len(stub.calls), 1)
        with open(path2, 'rb') as fp:
            eq('the original is untouched', fp.read(), original)
        eq('no temp files after a plain failure', leftover_temp_files(directory), [])
    finally:
        subprocess.run = real_run

    shutil.rmtree(directory, ignore_errors=True)


def test_prefix_roundtrip_on_disk():
    """calibre 交过来的带前缀路径，Python 自己的文件系统调用也必须能用，read_media()/write_media() 靠的就是这个。"""
    if not IS_WINDOWS:
        skip('extended prefix works with real files', 'Windows only')
        return
    print('extended prefix against the real filesystem')
    directory = workdir()
    path = os.path.join(directory, 'song 歌曲.mp3')
    with open(path, 'wb') as fp:
        fp.write(b'x')
    prefixed = '\\\\?\\' + path
    check('isfile accepts the prefixed path', os.path.isfile(prefixed))
    eq('dirname of the prefixed path keeps the prefix',
       os.path.dirname(prefixed), '\\\\?\\' + directory)
    tmp = os.path.join(os.path.dirname(prefixed), '.probe.tmp')
    with open(tmp, 'wb') as fp:
        fp.write(b'y')
    os.replace(tmp, prefixed)
    check('os.replace works across prefixed names', os.path.isfile(prefixed))
    eq('no leftovers', leftover_temp_files(directory), [])
    shutil.rmtree(directory, ignore_errors=True)


def main():
    for func in (test_long_path_prefix, test_subprocess_kwargs, test_run_tool_real_subprocess,
                 test_write_media_filesystem, test_write_media_retries_tracks_the_container_rejects,
                 test_prefix_roundtrip_on_disk):
        func()
    print()
    if FAILURES:
        print('%d FAILED: %s' % (len(FAILURES), ', '.join(FAILURES)))
        return 1
    print('all platform tests passed')
    return 0


if __name__ == '__main__':
    sys.exit(main())
