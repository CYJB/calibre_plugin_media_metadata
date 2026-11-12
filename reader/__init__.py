import os
import shutil
import tempfile

from calibre.customize import MetadataReaderPlugin
from calibre.utils.localization import _

try:
    from . import ffmeta, ffutil
    from .binaries import locate_binaries
    # build_metadata / prefs / DURATION_DATATYPES 都在 metadata.py 里，与 sync 插件共用；
    # 这里重新导出 prefs 和 DURATION_DATATYPES，readers/config.py 和测试都用这个名字。
    from .metadata import DURATION_DATATYPES, build_metadata, prefs
except ImportError:
    from calibre_plugins.MediaMetadataReader import ffmeta, ffutil
    from calibre_plugins.MediaMetadataReader.binaries import locate_binaries
    from calibre_plugins.MediaMetadataReader.metadata import (
        DURATION_DATATYPES, build_metadata, prefs)

# calibre 给 zip 里每个模块都注入 load_translations()
# （customize/zipplugin.py:239-247），必须在下面模块级 _() 之前执行。
try:
    load_translations()
except NameError:
    pass


def is_real_file(path):
    try:
        return bool(path) and os.path.isfile(path)
    except (ValueError, OSError):
        return False


def stream_to_path(stream, tmpdir):
    """ffmpeg 需要一个可 seek 的真实路径，而不是文件对象。

    返回 (路径, 需要清理的临时路径)。流本来就来自磁盘上的文件时，临时路径为 None。
    """
    name = getattr(stream, 'name', None)
    if isinstance(name, bytes):
        name = name.decode('utf-8', 'replace')
    if is_real_file(name):
        return name, None

    handle, path = tempfile.mkstemp(suffix='.media', dir=tmpdir)
    with os.fdopen(handle, 'wb') as fp:
        pos = stream.tell() if hasattr(stream, 'tell') else 0
        stream.seek(0)
        while True:
            chunk = stream.read(1024 * 1024)
            if not chunk:
                break
            fp.write(chunk)
    if hasattr(stream, 'seek'):
        stream.seek(pos)
    return path, path


class MediaMetadataReader(MetadataReaderPlugin):
    # 这里不能有斜杠：calibre 用 "<name>.zip" 作为插件目录里的安装文件名，名字里有斜杠会把目标路径撑成子目录。
    name = 'Read media metadata'
    description = _(
        'Read title, authors, series, tags, comments, publisher, date, cover art and '
        'duration from audio and video files (mp3, m4a, m4b, mp4, mkv, flac, ogg, opus, '
        'mov, webm and more) using ffprobe. Requires ffmpeg/ffprobe to be installed.')
    supported_platforms = ['windows', 'osx', 'linux']
    author = 'CYJB'
    version = (1, 0, 0)
    minimum_calibre_version = (7, 0, 0)
    file_types = set(ffutil.MEDIA_EXTENSIONS)

    # calibre 判断插件是否可配置的方式是调 customization_help()，把
    # NotImplementedError 当成“不需要配置”
    # （customize/__init__.py:319-324，门禁在 gui2/preferences/plugins.py:385）。
    # 没有这个方法时，即使 config_widget() 实现完整，Customize 按钮也会提示“不需要自定义”。
    def customization_help(self, gui=False):
        return _(
            'Paths to ffmpeg and ffprobe (leave blank to auto-detect), plus the '
            'custom column that should hold the media duration and its datatype.')

    def config_widget(self):
        from .config import ConfigWidget
        return ConfigWidget()

    def save_settings(self, config_widget):
        config_widget.save_settings()

    def get_metadata(self, stream, stream_type):
        ffmpeg, ffprobe = locate_binaries()
        if not ffprobe:
            # 没有 ffprobe 时保持沉默，让 calibre 回退到按文件名解析，而不是把每次添加都变成错误对话框。
            return None

        tmpdir = tempfile.mkdtemp(prefix='calibre-media-meta-')
        try:
            path, temp_path = stream_to_path(stream, tmpdir)
            try:
                media = ffmeta.read_media(
                    path, ffmpeg, ffprobe,
                    want_cover=not getattr(self, 'quick', False) and bool(ffmpeg))
            finally:
                if temp_path and os.path.exists(temp_path):
                    try:
                        os.remove(temp_path)
                    except OSError:
                        pass
            return build_metadata(media)
        except Exception:
            return None
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)
