# 把 ffprobe 读到的标签摊平成 calibre 的字段值，以及反过来构造成 MetaInformation。
#
# reader 插件（添加书籍时读取）和 sync 插件（对话框里的「用文件更新书库」）都用它，
# build.py 打包时会复制进两个 zip。和 binaries.py 一样，这里可以自由 import calibre。

from datetime import datetime

from calibre.ebooks.metadata import MetaInformation
from calibre.utils.config import JSONConfig

try:
    from . import ffmeta
except ImportError:
    # 无头测试直接加载这个文件，没有父包。
    import ffmeta

# 时长列的配置与 reader 插件共用同一个 JSONConfig 文件，两个插件看到的是同一套设置。
prefs = JSONConfig('plugins/MediaMetadataReader')
prefs.defaults['duration_column'] = '#duration'
prefs.defaults['duration_datatype'] = 'float'

DURATION_DATATYPES = ('float', 'int', 'text')


def duration_column():
    """配置里的时长列（带 # 前缀）；没配置时返回 None。"""
    column = prefs['duration_column']
    if not column:
        return None
    return column if column.startswith('#') else '#' + column


def duration_value(seconds):
    """按配置算出要写进时长列的值，返回 (列名, 值)；没配置或没有时长时返回 (None, None)。

    时长列的 datatype 必须和真实列一致，calibre 才不会静默丢弃（db/cache.py:2656）。
    """
    column = duration_column()
    if not column or seconds is None:
        return None, None
    datatype = prefs['duration_datatype']
    if datatype == 'int':
        return column, int(round(seconds))
    if datatype == 'text':
        return column, ffmeta._format_duration(seconds)
    return column, round(float(seconds), 2)


def apply_duration(mi, seconds):
    """把时长写进配置好的自定义列。

    只有元数据对象声明的 datatype 与真实列的 datatype 完全一致时，calibre 才会应用
    自定义列的值（db/cache.py:2656）；而 metadata reader 跑在拿不到书库结构的 worker
    进程里，所以 datatype 只能来自插件自己的配置。
    """
    column, value = duration_value(seconds)
    if not column:
        return
    try:
        mi.set_user_metadata(column, {'datatype': prefs['duration_datatype'],
                                      'is_multiple': {}})
        mi.set(column, value)
    except Exception:
        pass


def _pubdate(tags):
    """把 date / year 标签解析成 datetime；解析不出来时返回 None。"""
    date_text = ffmeta.render_date(tags.get('date') or tags.get('year'))
    if not date_text:
        return None
    for pattern in ('%Y-%m-%d', '%Y-%m', '%Y'):
        try:
            return datetime.strptime(date_text, pattern)
        except ValueError:
            continue
    return None


def library_values(media):
    """从媒体标签里取出能写进 calibre 书库的字段，只包含文件里确实有值的字段。

    返回纯 Python 值（文本 / 列表 / datetime / 字典）。「文件 -> 书库」和 reader 的
    MetaInformation 都由它派生，标签到字段的映射只有这一处。
    """
    tags = media['tags']
    values = {}

    title = ffmeta.canonical_text(tags.get('title'))
    if title:
        values['title'] = title

    authors = ffmeta.split_authors(tags.get('artist') or tags.get('album_artist')
                                   or tags.get('albumartist') or tags.get('composer'))
    if authors:
        values['authors'] = authors

    series = ffmeta.canonical_text(tags.get('album'))
    if series:
        values['series'] = series
        # 没有系列时 track 只是个序号，写进 series_index 没有意义。
        index = ffmeta.canonical_index(tags.get('track') or tags.get('tracknumber'))
        if index is not None:
            values['series_index'] = index

    file_tags = ffmeta.media_tags(tags)
    if file_tags:
        values['tags'] = file_tags

    comments = ffmeta.canonical_text(tags.get('comment') or tags.get('description')
                                     or tags.get('synopsis'))
    if comments:
        values['comments'] = comments

    publisher = ffmeta.canonical_text(tags.get('publisher') or tags.get('label'))
    if publisher:
        values['publisher'] = publisher

    pubdate = _pubdate(tags)
    if pubdate is not None:
        values['pubdate'] = pubdate

    isbn = ffmeta.canonical_text(tags.get('isbn'))
    if isbn:
        values['identifiers'] = {'isbn': isbn}

    return values


def build_metadata(media):
    """把 ffmeta.read_media() 的结果变成 calibre 的 MetaInformation 对象。"""
    values = library_values(media)
    mi = MetaInformation(None, None)

    if 'title' in values:
        mi.title = values['title']
    if 'authors' in values:
        mi.authors = values['authors']
    if 'series' in values:
        mi.series = values['series']
    if 'series_index' in values:
        mi.series_index = values['series_index']
    if 'tags' in values:
        mi.tags = values['tags']
    if 'comments' in values:
        mi.comments = values['comments']
    if 'publisher' in values:
        mi.publisher = values['publisher']
    if 'pubdate' in values:
        mi.pubdate = values['pubdate']
    if 'identifiers' in values:
        mi.set_identifiers(values['identifiers'])
    if media.get('cover'):
        mi.cover_data = (media.get('cover_ext') or 'jpg', media['cover'])

    apply_duration(mi, media['tech'].get('duration'))
    return mi
