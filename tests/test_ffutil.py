#!/usr/bin/env python3
# 无头测试 ffutil 的二进制定位。不需要装 ffmpeg：每个平台分支都用注入的
# platform/env/isfile/glob 驱动，所以在 macOS 或 Linux 上也能测试 Windows 路径。

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'common'))

import ffutil  # noqa: E402

FAILURES = []


def check(name, condition, detail=''):
    if condition:
        print('  PASS  %s' % name)
    else:
        print('  FAIL  %s %s' % (name, detail))
        FAILURES.append(name)


def eq(name, actual, expected):
    check(name, actual == expected, '(got %r, want %r)' % (actual, expected))


def fake_isfile(existing):
    return lambda path: path in existing


WIN_ENV = {
    'LOCALAPPDATA': r'C:\Users\bob\AppData\Local',
    'USERPROFILE': r'C:\Users\bob',
    'ChocolateyInstall': r'C:\tools\chocolatey',
}


def test_natural_key():
    print('natural key ordering')
    key = ffutil._natural_key
    paths = ['ffmpeg-7.1', 'ffmpeg-10.0', 'ffmpeg-6.0']
    eq('sorts numeric segments numerically', sorted(paths, key=key),
       ['ffmpeg-6.0', 'ffmpeg-7.1', 'ffmpeg-10.0'])
    eq('handles mixed text', sorted(['b1', 'a2', 'a10'], key=key), ['a2', 'a10', 'b1'])


def test_expand():
    print('pattern expansion')
    eq('expands windows env var',
       ffutil._expand(r'%LOCALAPPDATA%\bin', WIN_ENV, '/home/bob'),
       r'C:\Users\bob\AppData\Local\bin')
    eq('case-insensitive env lookup',
       ffutil._expand(r'%localappdata%\bin', WIN_ENV, '/home/bob'),
       r'C:\Users\bob\AppData\Local\bin')
    eq('skips candidate when var is unset',
       ffutil._expand(r'%NOT_SET%\bin', WIN_ENV, '/home/bob'), None)
    eq('expands home', ffutil._expand('~/.local/bin/{name}', {}, '/home/bob'),
       '/home/bob/.local/bin/{name}')
    eq('leaves plain path alone', ffutil._expand('/usr/bin/{name}', {}, '/home/bob'),
       '/usr/bin/{name}')


def test_windows_candidates():
    print('windows candidate list')
    seen = list(ffutil.candidate_paths('ffmpeg', 'win32', WIN_ENV, r'C:\Users\bob'))
    check('all end with .exe', all(p.endswith('.exe') for p in seen), seen)
    check('C:\\ffmpeg\\bin probed first', seen[0] == r'C:\ffmpeg\bin\ffmpeg.exe', seen[0])
    check('Program Files probed', r'C:\Program Files\ffmpeg\bin\ffmpeg.exe' in seen)
    check('Program Files (x86) probed', r'C:\Program Files (x86)\ffmpeg\bin\ffmpeg.exe' in seen)
    check('WinGet Links probed',
          r'C:\Users\bob\AppData\Local\Microsoft\WinGet\Links\ffmpeg.exe' in seen)
    check('ChocolateyInstall honoured', r'C:\tools\chocolatey\bin\ffmpeg.exe' in seen)
    check('ProgramData chocolatey fallback kept',
          r'C:\ProgramData\chocolatey\bin\ffmpeg.exe' in seen)
    check('user scoop shims probed', r'C:\Users\bob\scoop\shims\ffmpeg.exe' in seen)
    check('system scoop shims probed', r'C:\ProgramData\scoop\shims\ffmpeg.exe' in seen)
    check('a hand-unzipped copy in the profile is probed',
          r'C:\Users\bob\ffmpeg\bin\ffmpeg.exe' in seen)
    check('a hand-unzipped copy in Local Programs is probed',
          r'C:\Users\bob\AppData\Local\Programs\ffmpeg\bin\ffmpeg.exe' in seen)
    # ChocolateyInstall 未设置 -> 该候选消失，兜底项仍在
    env = {k: v for k, v in WIN_ENV.items() if k != 'ChocolateyInstall'}
    seen2 = list(ffutil.candidate_paths('ffmpeg', 'win32', env, r'C:\Users\bob'))
    check('unset ChocolateyInstall drops its candidate',
          r'C:\tools\chocolatey\bin\ffmpeg.exe' not in seen2)
    check('chocolatey fallback still present',
          r'C:\ProgramData\chocolatey\bin\ffmpeg.exe' in seen2)
    # ffprobe 用自己的名字，不是 ffmpeg 的
    probe = list(ffutil.candidate_paths('ffprobe', 'win32', WIN_ENV, r'C:\Users\bob'))
    check('ffprobe candidates named ffprobe.exe',
          probe[0] == r'C:\ffmpeg\bin\ffprobe.exe', probe[0])


def test_winget_glob():
    print('winget glob picks newest version')
    pattern_hits = {
        r'C:\Users\bob\AppData\Local\Microsoft\WinGet\Packages\Gyan.FFmpeg*\ffmpeg-*\bin\ffmpeg.exe': [
            r'C:\Users\bob\AppData\Local\Microsoft\WinGet\Packages\Gyan.FFmpeg_abc\ffmpeg-6.0\bin\ffmpeg.exe',
            r'C:\Users\bob\AppData\Local\Microsoft\WinGet\Packages\Gyan.FFmpeg_abc\ffmpeg-10.1\bin\ffmpeg.exe',
            r'C:\Users\bob\AppData\Local\Microsoft\WinGet\Packages\Gyan.FFmpeg_abc\ffmpeg-7.1\bin\ffmpeg.exe',
        ],
    }
    seen = list(ffutil.candidate_paths(
        'ffmpeg', 'win32', WIN_ENV, r'C:\Users\bob',
        globber=lambda pattern: pattern_hits.get(pattern, [])))
    newest = r'C:\Users\bob\AppData\Local\Microsoft\WinGet\Packages\Gyan.FFmpeg_abc\ffmpeg-10.1\bin\ffmpeg.exe'
    check('newest winget build ranked above 7.1 and 6.0',
          seen.index(newest) < seen.index(
              r'C:\Users\bob\AppData\Local\Microsoft\WinGet\Packages\Gyan.FFmpeg_abc\ffmpeg-7.1\bin\ffmpeg.exe'),
          seen)


def test_posix_candidates():
    print('posix candidate lists')
    mac = list(ffutil.candidate_paths('ffmpeg', 'darwin', {}, '/Users/bob'))
    eq('macos homebrew (apple silicon) first', mac[0], '/opt/homebrew/bin/ffmpeg')
    check('macos intel homebrew probed', '/usr/local/bin/ffmpeg' in mac)
    check('macos macports probed', '/opt/local/bin/ffmpeg' in mac)
    check('no .exe suffix on macos', all(not p.endswith('.exe') for p in mac), mac)
    linux = list(ffutil.candidate_paths('ffprobe', 'linux', {}, '/home/bob'))
    eq('linux /usr/bin first', linux[0], '/usr/bin/ffprobe')
    check('linux snap probed', '/snap/bin/ffprobe' in linux)
    check('linux user local bin expanded', '/home/bob/.local/bin/ffprobe' in linux)


def test_find_binary():
    print('find_binary precedence')
    existing = {r'C:\ffmpeg\bin\ffmpeg.exe', '/opt/homebrew/bin/ffmpeg'}

    eq('explicit path wins',
       ffutil.find_binary('ffmpeg', platform='win32', env=WIN_ENV, home=r'C:\Users\bob',
                          explicit=r'C:\ffmpeg\bin\ffmpeg.exe',
                          path_lookup=lambda n: None,
                          isfile=fake_isfile(existing)),
       r'C:\ffmpeg\bin\ffmpeg.exe')

    eq('explicit path that does not exist returns None (no silent fallback)',
       ffutil.find_binary('ffmpeg', platform='win32', env=WIN_ENV, home=r'C:\Users\bob',
                          explicit=r'C:\custom\nope.exe',
                          path_lookup=lambda n: r'C:\ffmpeg\bin\ffmpeg.exe',
                          isfile=fake_isfile(existing)),
       None)

    eq('PATH lookup used before candidate dirs',
       ffutil.find_binary('ffmpeg', platform='darwin', env={}, home='/Users/bob',
                          path_lookup=lambda n: '/opt/homebrew/bin/ffmpeg',
                          isfile=fake_isfile(existing)),
       '/opt/homebrew/bin/ffmpeg')

    eq('falls back to candidate dirs when PATH lookup fails',
       ffutil.find_binary('ffmpeg', platform='win32', env=WIN_ENV, home=r'C:\Users\bob',
                          path_lookup=lambda n: None,
                          isfile=fake_isfile(existing)),
       r'C:\ffmpeg\bin\ffmpeg.exe')

    eq('returns None when nothing is found',
       ffutil.find_binary('ffmpeg', platform='win32', env=WIN_ENV, home=r'C:\Users\bob',
                          path_lookup=lambda n: None, isfile=fake_isfile(set())),
       None)

    eq('ffprobe located independently of ffmpeg',
       ffutil.find_binary('ffprobe', platform='darwin', env={}, home='/Users/bob',
                          path_lookup=lambda n: None,
                          isfile=fake_isfile({'/usr/local/bin/ffprobe'})),
       '/usr/local/bin/ffprobe')


def test_path_normalisation():
    print('windows long path prefix')
    eq('strips \\\\?\\ prefix',
       ffutil.strip_windows_long_path_prefix(r'\\?\C:\Books\song.mp3'), r'C:\Books\song.mp3')
    eq('leaves normal path alone',
       ffutil.strip_windows_long_path_prefix(r'C:\Books\song.mp3'), r'C:\Books\song.mp3')
    eq('leaves posix path alone',
       ffutil.strip_windows_long_path_prefix('/Books/song.mp3'), '/Books/song.mp3')
    eq('handles None', ffutil.strip_windows_long_path_prefix(None), None)


def test_media_extensions():
    print('supported extensions')
    for ext in ('mp3', 'm4a', 'm4b', 'mp4', 'm4v', 'mov', 'mkv', 'flac',
                'ogg', 'opus', 'wav', 'aiff', 'aif', 'wma', 'webm', 'avi'):
        check('supports .%s' % ext, ext in ffutil.MEDIA_EXTENSIONS)
    for ext in ('epub', 'mobi', 'pdf', 'azw3'):
        check('does not claim .%s' % ext, ext not in ffutil.MEDIA_EXTENSIONS)
    # .ts 和 TypeScript 撞车，所以刻意不认
    check('does not claim .ts', 'ts' not in ffutil.MEDIA_EXTENSIONS)


def main():
    for func in (test_natural_key, test_expand, test_windows_candidates,
                 test_winget_glob, test_posix_candidates, test_find_binary,
                 test_path_normalisation, test_media_extensions):
        func()
    print()
    if FAILURES:
        print('%d FAILED: %s' % (len(FAILURES), ', '.join(FAILURES)))
        return 1
    print('all ffutil tests passed')
    return 0


if __name__ == '__main__':
    sys.exit(main())
