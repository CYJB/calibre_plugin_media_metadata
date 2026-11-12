# 定位 ffmpeg/ffprobe 的共用配置控件。build.py 打包时会复制进两个插件 zip，
# 所以它可以自由 import calibre。

from __future__ import annotations

import os

from qt.core import (
    QFileDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout, QWidget,
)

from calibre.utils.config import JSONConfig
from calibre.utils.localization import _

try:
    from . import ffutil
except ImportError:
    import ffutil

# calibre 给 zip 里每个模块都注入 load_translations()
# （customize/zipplugin.py:239-247）。调用它会把本模块的 `_` 换成当前界面语言的
# catalog，所以必须在任何模块级 _() 之前执行。
try:
    load_translations()
except NameError:
    pass

# 两个插件共用一份配置文件，ffmpeg/ffprobe 路径只填一次。JSONConfig 是内存缓存、
# 只在构造时 refresh（utils/config.py:441-450），而 reader 插件跑在 calibre 的 worker
# 进程里、设置却是 GUI 进程保存的，所以每次查找前都先 refresh。
binary_prefs = JSONConfig('plugins/MediaMetadata')
binary_prefs.defaults['ffmpeg_path'] = ''
binary_prefs.defaults['ffprobe_path'] = ''


def locate_binaries():
    """解析 ffmpeg 与 ffprobe。返回 (ffmpeg, ffprobe)，两者都可能是 None。"""
    binary_prefs.refresh()
    ffutil.clear_cache()
    ffmpeg = ffutil.get_binary('ffmpeg', explicit=binary_prefs['ffmpeg_path'] or None)
    ffprobe = ffutil.get_binary('ffprobe', explicit=binary_prefs['ffprobe_path'] or None)
    return ffmpeg, ffprobe


def tool_error_text(err):
    """把 ffutil.ToolError 变成给用户看的中文文案。

    ffutil 不能 import calibre，所以它的异常消息是英文技术描述；这里按 err.kind 重新
    组织成中文。ffmpeg 自己输出到 stderr 的内容保持原样——那是它的英文日志，翻译没有意义。
    """
    name = os.path.basename(err.path or '') or 'ffmpeg'
    if err.kind == 'not_found':
        return _('Cannot find %s. Install it, or set the full path in the plugin '
                 'settings.') % (err.path or name)
    if err.kind == 'cannot_run':
        return _('%s cannot be run: %s') % (err.path or name, err.detail)
    if err.kind == 'timeout':
        return _('%s did not respond within %s seconds.') % (err.path or name, err.timeout)
    text = _('%s exited with code %s.') % (name, err.returncode)
    stderr = (err.stderr or '').strip()
    if stderr:
        text += '\n\n' + _('Its output was:') + '\n' + stderr[:2000]
    return text


class BinaryPathEditor(QWidget):
    """一行带标签的路径输入框，配 Browse 和 Test 两个按钮。"""

    def __init__(self, label, binary_name, parent=None):
        QWidget.__init__(self, parent)
        self.binary_name = binary_name
        layout = QHBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        self.setLayout(layout)

        self.edit = QLineEdit()
        self.edit.setPlaceholderText(_('Auto-detect'))
        layout.addWidget(self.edit, stretch=1)

        browse = QPushButton(_('Browse…'))
        browse.clicked.connect(self.browse)
        layout.addWidget(browse)

        self.test_button = QPushButton(_('Test'))
        self.test_button.clicked.connect(self.test)
        layout.addWidget(self.test_button)

        self.form_label = label

    def browse(self):
        if os.name == 'nt':
            pattern = '%s (*.exe);;%s' % (self.binary_name, _('All files'))
        else:
            pattern = _('All files') + ' (*)'
        start = self.edit.text() or os.path.expanduser('~')
        path = QFileDialog.getOpenFileName(self, _('Choose %s') % self.binary_name, start, pattern)[0]
        if path:
            self.edit.setText(path)

    def test(self):
        from qt.core import QMessageBox

        explicit = self.edit.text().strip() or None
        found = ffutil.find_binary(self.binary_name, explicit=explicit)
        if not found:
            QMessageBox.warning(
                self, _('Not found'),
                _('%s could not be located. Install it, or type the full path above.')
                % self.binary_name)
            return
        try:
            version = ffutil.run_tool([found, '-version'], timeout=20).splitlines()[0]
        except ffutil.ToolError as err:
            QMessageBox.warning(self, _('Failed'), tool_error_text(err))
            return
        QMessageBox.information(self, _('Found'), '%s\n%s' % (found, version))

    def set_path(self, value):
        self.edit.setText(value or '')

    def path(self):
        return self.edit.text().strip()


class BinaryConfigWidget(QWidget):
    """ffmpeg + ffprobe 路径输入框，加一行实时状态。"""

    def __init__(self, parent=None):
        QWidget.__init__(self, parent)
        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        self.setLayout(layout)

        form = QFormLayout()
        layout.addLayout(form)
        self.ffmpeg = BinaryPathEditor(_('ffmpeg'), 'ffmpeg')
        self.ffprobe = BinaryPathEditor(_('ffprobe'), 'ffprobe')
        form.addRow(_('ffmpeg path:'), self.ffmpeg)
        form.addRow(_('ffprobe path:'), self.ffprobe)

        self.status = QLabel()
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

        note = QLabel(_(
            'These paths are shared by both media metadata plugins, so they only '
            'have to be set once. Leave blank to auto-detect. Detection checks your '
            'PATH and the usual install locations (Homebrew on macOS; C:\\ffmpeg, '
            'Program Files, WinGet, Chocolatey and Scoop on Windows). A GUI calibre '
            'does not inherit your shell PATH, so setting the path explicitly is the '
            'most reliable option.'))
        note.setWordWrap(True)
        note.setStyleSheet('color: gray;')
        layout.addWidget(note)

        self.refresh_status()

    def refresh_status(self):
        ffutil.clear_cache()
        ffmpeg = ffutil.find_binary('ffmpeg', explicit=self.ffmpeg.path() or None)
        ffprobe = ffutil.find_binary('ffprobe', explicit=self.ffprobe.path() or None)
        parts = [
            'ffmpeg: %s' % (ffmpeg or _('not found')),
            'ffprobe: %s' % (ffprobe or _('not found')),
        ]
        self.status.setText('\n'.join(parts))
        self.status.setStyleSheet('' if (ffmpeg and ffprobe) else 'color: #b00020;')

    def load_settings(self):
        binary_prefs.refresh()
        self.ffmpeg.set_path(binary_prefs['ffmpeg_path'])
        self.ffprobe.set_path(binary_prefs['ffprobe_path'])
        self.refresh_status()

    def save_settings(self):
        binary_prefs['ffmpeg_path'] = self.ffmpeg.path()
        binary_prefs['ffprobe_path'] = self.ffprobe.path()

    def validate(self):
        self.refresh_status()
        return True
