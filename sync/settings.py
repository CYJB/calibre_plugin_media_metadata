# sync 插件共用的配置。单独放在 action.py 之外，这样配置界面不必 import GUI 动作模块。
#
# ffmpeg/ffprobe 路径不在这里：它们在 binaries.binary_prefs 里，与 reader 插件共用，
# 只配一次就够。

from calibre.utils.config import JSONConfig

prefs = JSONConfig('plugins/MediaMetadataSync')
prefs.defaults['id3v2_version'] = 3
prefs.defaults['write_cover'] = True
prefs.defaults['register_context_menu'] = True
prefs.defaults['custom_columns'] = []


def configured_columns():
    """要同步的自定义列的 (lookup_name, label, tag_key) 三元组。"""
    out = []
    for item in prefs['custom_columns'] or ():
        lookup = (item.get('lookup') or '').strip()
        tag_key = (item.get('tag') or '').strip()
        if not lookup or not tag_key:
            continue
        if not lookup.startswith('#'):
            lookup = '#' + lookup
        out.append((lookup, item.get('label') or lookup, tag_key))
    return out
