#!/usr/bin/env python3
# 检查 calibre 自己怎么看这两个插件：注册、分发，以及——曾经悄悄坏掉过的——
# Customize 按钮是否被提供。
#
#     QT_QPA_PLATFORM=offscreen calibre-debug tests/test_plugins.py

import os
import sys

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from qt.core import QApplication          # noqa: E402

app = QApplication.instance() or QApplication([])  # noqa: F841

from calibre.customize import InterfaceActionBase, MetadataReaderPlugin  # noqa: E402
from calibre.customize.ui import _metadata_readers, initialized_plugins  # noqa: E402

FAILURES = []


def check(name, condition, detail=''):
    if condition:
        print('  PASS  %s' % name)
    else:
        print('  FAIL  %s %s' % (name, detail))
        FAILURES.append(name)


def eq(name, actual, expected):
    check(name, actual == expected, '(got %r, want %r)' % (actual, expected))


def find(name):
    for plugin in initialized_plugins():
        if plugin.name == name:
            return plugin
    return None


READER = 'Read media metadata'
SYNC = 'Sync media metadata'

MEDIA_EXTENSIONS = {
    'mp3', 'm4a', 'm4b', 'mp4', 'm4v', 'mov', 'mkv', 'flac',
    'ogg', 'opus', 'wav', 'aiff', 'aif', 'wma', 'webm', 'avi',
}


def main():
    print('registration')
    reader = find(READER)
    sync = find(SYNC)
    check('reader plugin is loaded', reader is not None,
          [p.name for p in initialized_plugins() if 'media' in p.name.lower()])
    check('sync plugin is loaded', sync is not None)
    if reader is None or sync is None:
        print('\ninstall dist/*.zip first (Preferences -> Plugins -> Load plugin from file),')
        print('then restart calibre. See AGENTS.md for the headless equivalent.')
        return 1
    check('reader is a MetadataReaderPlugin', isinstance(reader, MetadataReaderPlugin))
    check('sync is an InterfaceActionBase', isinstance(sync, InterfaceActionBase))

    print('\nreader dispatch')
    eq('reader claims every media extension', set(reader.file_types), MEDIA_EXTENSIONS)
    for ext in ('mp3', 'm4b', 'mp4', 'mkv'):
        names = [p.name for p in _metadata_readers.get(ext, ())]
        check('%s dispatches to our reader' % ext, READER in names, names)
    check('epub still dispatches to the builtin reader only',
          READER not in [p.name for p in _metadata_readers.get('epub', ())])

    print('\ncustomizability')
    # 除非 is_customizable() 为真，gui2/preferences/plugins.py:385 会拒绝打开配置对话框，
    # 而 customize/__init__.py:319-324 把它实现成“customization_help() 不抛
    # NotImplementedError”——光有 config_widget() 是不够的。
    for plugin in (reader, sync):
        check('%s: is_customizable()' % plugin.name, plugin.is_customizable() is True)
        help_text = plugin.customization_help()
        check('%s: customization_help is non-empty' % plugin.name, bool(help_text.strip()),
              repr(help_text))
        widget = plugin.config_widget()
        check('%s: config_widget() builds' % plugin.name, widget is not None)
        check('%s: config_widget has validate()' % plugin.name, callable(getattr(widget, 'validate', None)))
        check('%s: config_widget has save_settings()' % plugin.name,
              callable(getattr(plugin, 'save_settings', None)))
        widget.deleteLater()

    print('\nlocalization')
    from calibre.utils.localization import get_lang
    lang = get_lang()
    print('  calibre UI language: %s' % lang)
    if lang.startswith('zh'):
        help_text = reader.customization_help()
        check('reader help is Chinese', any('\u4e00' <= c <= '\u9fff' for c in help_text),
              help_text[:60])
        sync_widget = sync.config_widget()
        check('sync config checkbox is Chinese',
              any('\u4e00' <= c <= '\u9fff' for c in sync_widget.write_cover.text()),
              sync_widget.write_cover.text())
        sync_widget.deleteLater()

        # ffmpeg 缺失/出错时的提示必须是中文：ffutil 的异常消息是英文技术描述，
        # 界面文案由 binaries.tool_error_text() 按 ToolError.kind 组装。
        from calibre_plugins.MediaMetadataSync import ffutil as sync_ffutil
        from calibre_plugins.MediaMetadataSync.binaries import tool_error_text

        def is_chinese(text):
            return any('\u4e00' <= c <= '\u9fff' for c in text)

        not_found = sync_ffutil.ToolError(
            'Executable not found: /x/ffmpeg', kind='not_found', path='/x/ffmpeg')
        text = tool_error_text(not_found)
        check('missing ffmpeg message is Chinese', is_chinese(text), text)
        check('missing ffmpeg message names the path', '/x/ffmpeg' in text, text)

        probe_missing = sync_ffutil.ToolError(
            'Executable not found: /x/ffprobe', kind='not_found', path='/x/ffprobe')
        text = tool_error_text(probe_missing)
        check('missing ffprobe message names ffprobe', 'ffprobe' in text, text)

        cannot_run = sync_ffutil.ToolError(
            'Could not run /x/ffmpeg: WinError 193', kind='cannot_run',
            path='/x/ffmpeg', detail='WinError 193')
        check('cannot-run message is Chinese', is_chinese(tool_error_text(cannot_run)),
              tool_error_text(cannot_run))

        failed = sync_ffutil.ToolError(
            'ffmpeg failed with code 1: boom', kind='failed', returncode=1,
            stderr='boom', path='/x/ffmpeg')
        text = tool_error_text(failed)
        check('failed-run message is Chinese', is_chinese(text), text)
        check('failed-run message keeps ffmpeg output', 'boom' in text, text)

        # 配置界面的状态行：给一个肯定不存在的路径，状态行应当是中文的“未找到”。
        from calibre_plugins.MediaMetadataSync.binaries import BinaryConfigWidget

        status_widget = BinaryConfigWidget()
        status_widget.ffmpeg.set_path('/definitely/not/here/ffmpeg')
        status_widget.refresh_status()
        text = status_widget.status.text()
        check('config status is Chinese when ffmpeg is missing', is_chinese(text), text)
        status_widget.deleteLater()

    print('\nsync action wiring')
    eq('actual_plugin points at the action',
       sync.actual_plugin, 'calibre_plugins.MediaMetadataSync.action:SyncAction')
    from calibre_plugins.MediaMetadataSync.action import ACTION_NAME, SyncAction, updated_layout
    eq('action name matches the class', SyncAction.name, ACTION_NAME)
    check('action is a single menu item, not a submenu',
          SyncAction.action_add_menu is False)
    eq('layout registration is idempotent',
       updated_layout(updated_layout((), ACTION_NAME), ACTION_NAME), (None, ACTION_NAME))

    print('\nshared ffmpeg configuration')
    from calibre_plugins.MediaMetadataReader import config as reader_config
    from calibre_plugins.MediaMetadataReader import prefs as reader_own_prefs
    from calibre_plugins.MediaMetadataReader.binaries import binary_prefs as reader_prefs
    from calibre_plugins.MediaMetadataSync import config as sync_config
    from calibre_plugins.MediaMetadataSync.binaries import binary_prefs as sync_prefs
    from calibre_plugins.MediaMetadataSync.binaries import locate_binaries
    from calibre_plugins.MediaMetadataSync.settings import prefs as sync_own_prefs

    eq('both plugins read the same config file',
       reader_prefs.file_path, sync_prefs.file_path)
    check('the shared config owns the ffmpeg paths',
          'ffmpeg_path' in reader_prefs.defaults and 'ffprobe_path' in reader_prefs.defaults)
    check('reader keeps no private copy of them',
          'ffmpeg_path' not in reader_own_prefs.defaults, sorted(reader_own_prefs.defaults))
    check('sync keeps no private copy of them',
          'ffmpeg_path' not in sync_own_prefs.defaults, sorted(sync_own_prefs.defaults))

    original = (reader_prefs['ffmpeg_path'], reader_prefs['ffprobe_path'])
    try:
        widget = reader_config.ConfigWidget()
        widget.binaries.ffmpeg.set_path('/tmp/definitely-not-here/ffmpeg')
        widget.save_settings()
        widget.deleteLater()

        other = sync_config.ConfigWidget()
        eq('sync widget sees the path saved from the reader widget',
           other.binaries.ffmpeg.path(), '/tmp/definitely-not-here/ffmpeg')
        other.deleteLater()

        ffmpeg, _ = locate_binaries()
        check('a configured path that does not exist is not silently ignored',
              ffmpeg is None, ffmpeg)
    finally:
        reader_prefs['ffmpeg_path'] = original[0]
        reader_prefs['ffprobe_path'] = original[1]
    eq('original configuration restored', reader_prefs['ffmpeg_path'], original[0])

    print()
    if FAILURES:
        print('%d FAILED: %s' % (len(FAILURES), ', '.join(FAILURES)))
        return 1
    print('all plugin registration tests passed')
    return 0


if __name__ == '__main__':
    sys.exit(main())
