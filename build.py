#!/usr/bin/env python3
"""打包两个插件 zip 到 ./dist。

    python3 build.py

calibre 一个 zip 只注册一个插件类（src/calibre/customize/zipplugin.py 只挑一个），
所以 reader 和 GUI 动作必须分成两个 zip；common/ 里的共用模块会复制进两个 zip，
保持一份权威副本，而不是两份会走样的拷贝。

安装刻意不放在这个脚本里：zip 由 calibre 自己加载（首选项 → 插件 → 从文件加载插件），
和装其它插件一样，装完要重启 calibre。
"""

import os
import shutil
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
DIST = os.path.join(HERE, 'dist')

SHARED = ('ffutil.py', 'ffmeta.py', 'binaries.py', 'metadata.py')

# 源码目录 -> (插件 zip 名, calibre 里的 import 名)
# import 名必须写成 zip 里的 `plugin-import-name-<name>.txt`；没有这个标记文件时
# calibre 会给模块分配一个临时名字（例如 calibre_plugins.dummy1，
# zipplugin.py:377-390），相对 import 和 actual_plugin 路径都会失效。
PLUGINS = (
    ('reader', 'MediaMetadataReader'),
    ('sync', 'MediaMetadataSync'),
)

MARKER = 'plugin-import-name-%s.txt'

# 中英文写法相同的字符串，不期待有翻译条目。
NO_TRANSLATION = frozenset({'ffmpeg', 'ffprobe'})

TRANSLATIONS = os.path.join(HERE, 'translations')


def collect(source_dir):
    """返回一个插件的 [(绝对路径, zip 内名字)]。"""
    entries = []
    for name in sorted(os.listdir(source_dir)):
        if name.endswith('.py'):
            entries.append((os.path.join(source_dir, name), name))
    for name in SHARED:
        path = os.path.join(HERE, 'common', name)
        if os.path.isfile(path):
            entries.append((path, name))
    return entries


def compile_translations():
    """把 translations/*.po 逐个编译成同目录的 .mo，返回 .mo 路径列表。

    calibre 的插件加载器直接从 zip 里读 translations/<lang>.mo
    （customize/zipplugin.py:131-160），所以 catalog 必须一起打包。
    """
    if not os.path.isdir(TRANSLATIONS):
        return []
    sys.path.insert(0, os.path.join(HERE, 'tools'))
    import po2mo

    out = []
    for name in sorted(os.listdir(TRANSLATIONS)):
        if not name.endswith('.po'):
            continue
        source = os.path.join(TRANSLATIONS, name)
        entries = po2mo.parse_po(source)
        data = po2mo.build_mo(entries)
        dest = os.path.splitext(source)[0] + '.mo'
        with open(dest, 'wb') as fp:
            fp.write(data)
        print('compiled %s (%d entries, %d bytes)' % (name, len(entries), len(data)))
        out.append(dest)
    return out


def check_translation_coverage():
    """对源码里没有 catalog 覆盖的字符串给出警告。"""
    sys.path.insert(0, os.path.join(HERE, 'tools'))
    import extract_po
    import po2mo

    source_ids = set(extract_po.collect()) - NO_TRANSLATION
    if not os.path.isdir(TRANSLATIONS):
        return
    for name in sorted(os.listdir(TRANSLATIONS)):
        if not name.endswith('.po'):
            continue
        entries = po2mo.parse_po(os.path.join(TRANSLATIONS, name))
        covered = {m for m, _p, _f in entries if m} | {p for _m, p, _f in entries if p}
        missing = sorted(source_ids - covered)
        if missing:
            print('\nWARNING: %d string(s) in the sources are not in %s:' % (len(missing), name))
            for msgid in missing:
                print('    %s' % (msgid if len(msgid) < 90 else msgid[:87] + '...'))
        else:
            print('%s covers all %d source strings' % (name, len(source_ids)))


def build(source_dir, zip_name, translations):
    entries = collect(source_dir)
    if not any(arc == '__init__.py' for _, arc in entries):
        return None
    out = os.path.join(DIST, zip_name + '.zip')
    names = sorted(arc for _, arc in entries) + [MARKER % zip_name]
    with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as zf:
        for path, arc in entries:
            zf.write(path, arc)
        for path in translations:
            arc = 'translations/' + os.path.basename(path)
            zf.write(path, arc)
            names.append(arc)
        zf.writestr(MARKER % zip_name, zip_name)
    return out, names


def main():
    if len(sys.argv) > 1:
        # build.py 只负责打包；安装由 calibre 用这些 zip 完成。
        print('build.py takes no arguments.')
        print('It only writes dist/MediaMetadataReader.zip and dist/MediaMetadataSync.zip;')
        print('install both from calibre: Preferences -> Plugins -> Load plugin from file.')
        return 2

    shutil.rmtree(DIST, ignore_errors=True)
    os.makedirs(DIST, exist_ok=True)

    translations = compile_translations()
    check_translation_coverage()
    print()

    built = []
    for source_name, zip_name in PLUGINS:
        source_dir = os.path.join(HERE, source_name)
        if not os.path.isdir(source_dir):
            print('skip %s (no %s/ directory yet)' % (zip_name, source_name))
            continue
        result = build(source_dir, zip_name, translations)
        if result is None:
            print('skip %s (no __init__.py)' % zip_name)
            continue
        path, names = result
        built.append(path)
        print('built %s' % path)
        print('      contents: %s' % ', '.join(names))

    if not built:
        print('nothing built')
        return 1

    print('\nInstall both zips from calibre: Preferences -> Plugins ->')
    print('Load plugin from file, then restart calibre.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
