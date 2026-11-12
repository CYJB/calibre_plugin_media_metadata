from calibre.customize import InterfaceActionBase
from calibre.utils.localization import _

# calibre 给 zip 里每个模块都注入 load_translations()
# （customize/zipplugin.py:239-247），必须在下面模块级 _() 之前执行。
try:
    load_translations()
except NameError:
    pass


class MediaMetadataSyncBase(InterfaceActionBase):
    # 这里不能有斜杠：calibre 用 "<name>.zip" 作为插件目录里的安装文件名，名字里有斜杠会把目标路径撑成子目录。
    name = 'Sync media metadata'
    description = _(
        'Add a right-click menu entry that compares the metadata in the calibre '
        'library against the tags currently embedded in a book\'s audio/video '
        'files, and writes the fields you tick back into the files using ffmpeg.')
    supported_platforms = ['windows', 'osx', 'linux']
    author = 'CYJB'
    version = (1, 0, 0)
    minimum_calibre_version = (7, 0, 0)
    actual_plugin = 'calibre_plugins.MediaMetadataSync.action:SyncAction'

    # calibre 判断插件是否可配置的方式是调 customization_help()，把
    # NotImplementedError 当成“不需要配置”
    # （customize/__init__.py:319-324，门禁在 gui2/preferences/plugins.py:385）。
    # 没有这个方法时，即使 config_widget() 实现完整，Customize 按钮也会提示“不需要自定义”。
    def customization_help(self, gui=False):
        return _(
            'Paths to ffmpeg and ffprobe (leave blank to auto-detect), the ID3 '
            'version written to mp3 files, whether to write cover art, whether to '
            'add the action to the book list right-click menu, and the mapping from '
            'calibre custom columns to tag keys.')

    def config_widget(self):
        from .config import ConfigWidget
        return ConfigWidget()

    def save_settings(self, config_widget):
        config_widget.save_settings()
