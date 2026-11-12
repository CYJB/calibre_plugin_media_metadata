# 语义层：把 ffprobe 的输出变成 calibre 的元数据字段，和书库里的值对比，
# 再把选中的字段用 ffmpeg 写回文件。
#
# 和 ffutil 一样，这个模块不 import calibre，所以两者都能被系统解释器无头跑测试。

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import tempfile

try:
    from . import ffutil
except ImportError:
    # 无头测试直接加载这个文件，没有父包。
    import ffutil

# calibre 的标签只对应 `keywords`：读写都只用这一个键。
# `genre`（流派）是另一回事，`grouping` 语义也偏，两个都不读也不写——用户在 calibre
# 里放的「已听」「Audible」之类不该跑到流派字段里。
# 实测（mp3/m4a/flac/mkv 四种容器）keywords 能原样存取；自定义键（例如 calibre-tags）
# 在 m4a 会被 ffmpeg 悄悄丢掉，所以不提供任意键。
TAG_KEYS = ('keywords',)

# calibre 字段 -> ffprobe 标签键，优先级从高到低。第一个键同时也是写回时用的键。
# collect_tags() 会把键转小写，所以这里的别名同时覆盖 ID3/MP4 的写法，以及
# ffmpeg 给 flac/ogg/opus 报的 Vorbis 注释写法（TRACKNUMBER、ALBUMARTIST、LABEL）。
FIELD_TO_TAGS = (
    ('title', ('title',)),
    ('authors', ('artist', 'album_artist', 'albumartist', 'composer')),
    ('series', ('album',)),
    ('series_index', ('track', 'tracknumber')),
    ('tags', TAG_KEYS),
    ('comments', ('comment', 'description', 'synopsis')),
    ('publisher', ('publisher', 'label')),
    ('pubdate', ('date', 'year')),
    ('identifiers', ('isbn',)),
)

# 对比表里会显示、但永远不写的字段：ffmpeg 没有 rating 的标准标签，
# 另外几个是流的固有属性。
UNSUPPORTED_FIELDS = ('rating',)

IMAGE_CODECS = frozenset({'mjpeg', 'png', 'bmp', 'gif'})

# ffmpeg 能存封面图的容器。
COVER_WRITABLE = frozenset({
    'mp3', 'm4a', 'm4b', 'mp4', 'm4v', 'mov', 'mkv', 'flac', 'ogg', 'opus',
})

MP4_FAMILY = frozenset({'mp4', 'm4a', 'm4b', 'm4v', 'mov'})

STATUS_SAME = 'same'
STATUS_DIFFERENT = 'different'
STATUS_MISSING_IN_FILE = 'missing_in_file'
STATUS_EMPTY_IN_LIB = 'empty_in_lib'
STATUS_UNSUPPORTED = 'unsupported'
# 只能写回书库的行（时长）：媒体文件里的时长由媒体流决定，不存在可写的标签。
STATUS_LIB_ONLY = 'lib_only'
STATUS_INFO = 'info'

# 默认勾选的行：文件与书库不一致、而且书库那一侧确实有内容的那些。
DEFAULT_CHECKED = frozenset({STATUS_DIFFERENT, STATUS_MISSING_IN_FILE})


class FieldRow:
    """对比表的一行。

    不含任何面向用户的文案：`label` 是稳定的键，`tooltip_key` / `tooltip_args`
    标识一条消息。这个模块刻意不 import calibre，所以什么都翻译不了——界面用的是自己的
    catalog。英文回退文本在 ffmeta.TOOLTIPS 里，由 FieldRow.tooltip 渲染。
    """

    def __init__(self, field, label, lib_display, file_display, status,
                 writable=True, tooltip_key=None, tooltip_args=(),
                 tag_key=None, write_value=None, lib_count=None, file_count=None,
                 default_checked=None):
        self.field = field
        self.label = label
        self.lib_display = lib_display
        self.file_display = file_display
        self.status = status
        self.writable = writable
        self.tooltip_key = tooltip_key
        self.tooltip_args = tuple(tooltip_args)
        # 这一行读写用的标签键（标签行固定是 keywords）
        self.tag_key = tag_key
        # 交给 ffmpeg 的值。None 表示“保持既有标签不变”，'' 表示“删除这个标签”。
        self.write_value = write_value
        # 仅封面行使用：原始字节数，界面按自己的语言渲染，而不是用上面的英文文案。
        self.lib_count = lib_count
        self.file_count = file_count
        # 只写回书库的行（时长）自己决定默认勾选；None 表示按 DEFAULT_CHECKED 判断。
        self.default_checked = default_checked

    @property
    def tooltip(self):
        """英文渲染，供日志、测试和任何非 GUI 调用方使用。"""
        if not self.tooltip_key:
            return ''
        return TOOLTIPS[self.tooltip_key] % self.tooltip_args

    @property
    def checked_by_default(self):
        if not self.writable:
            return False
        if self.default_checked is not None:
            return bool(self.default_checked)
        return self.status in DEFAULT_CHECKED

    def as_dict(self):
        return {
            'field': self.field, 'label': self.label,
            'lib': self.lib_display, 'file': self.file_display,
            'status': self.status, 'writable': self.writable,
            'tooltip': self.tooltip, 'tag_key': self.tag_key,
            'write_value': self.write_value,
        }


# ---------------------------------------------------------------- 读取

def probe(path, ffprobe):
    out = ffutil.run_tool([
        ffprobe, '-v', 'quiet', '-print_format', 'json',
        '-show_format', '-show_streams', '-show_chapters', path,
    ])
    return json.loads(out)


def collect_tags(info):
    """把标签合并成小写键的字典。format 级标签优先于流级标签，因为有些容器只在音频流上填标签。"""
    tags = {}
    for stream in info.get('streams') or ():
        if stream.get('codec_type') == 'audio':
            for key, value in (stream.get('tags') or {}).items():
                tags.setdefault(key.lower(), str(value))
    for key, value in ((info.get('format') or {}).get('tags') or {}).items():
        tags[key.lower()] = str(value)
    return tags


def cover_stream(info):
    """返回内嵌封面图的 (流序号, codec 名)，没有则返回 None。

    真实视频流绝不能被当成封面，所以只有 ffmpeg 标了 attached_pic、且 codec 属于
    静态图编解码器的流才算。
    """
    for stream in info.get('streams') or ():
        disposition = stream.get('disposition') or {}
        if disposition.get('attached_pic') == 1 and stream.get('codec_name') in IMAGE_CODECS:
            return stream.get('index'), stream.get('codec_name')
    return None


def read_cover(path, stream_index, ffmpeg):
    data = ffutil.run_tool(
        [ffmpeg, '-v', 'error', '-i', path, '-map', '0:%s' % stream_index,
         '-c', 'copy', '-f', 'image2pipe', 'pipe:1'],
        text=False,
    )
    return bytes(data) if data else None


def _major_brand(tags):
    """取 mp4 的 major_brand。ffprobe 有时给 'M4B '（带尾空格），有时给裸的 'isom'。"""
    brand = tags.get('major_brand') or tags.get('major-brand') or ''
    brand = str(brand)
    if not brand.strip():
        return None
    return brand


def technical_info(info):
    video = audio = None
    for stream in info.get('streams') or ():
        kind = stream.get('codec_type')
        if kind == 'video' and video is None and (stream.get('disposition') or {}).get('attached_pic') != 1:
            video = stream
        elif kind == 'audio' and audio is None:
            audio = stream
    fmt = info.get('format') or {}
    tags = fmt.get('tags') or {}
    result = {
        'duration': _to_float(fmt.get('duration')),
        'bit_rate': fmt.get('bit_rate'),
        'container': fmt.get('format_name'),
        'is_video': video is not None,
    }
    # major_brand 是 muxer 选项，不是普通标签：重封装时不显式指定的话会退回默认值，
    # m4b 会从 "M4B " 变成 "M4A "（Apple Books 就不再当有声书）。所以读出来带下去。
    brand = _major_brand(tags)
    if brand:
        result['brand'] = brand
    if video is not None:
        result['video_codec'] = video.get('codec_name')
        result['width'] = video.get('width')
        result['height'] = video.get('height')
    if audio is not None:
        result['audio_codec'] = audio.get('codec_name')
        result['sample_rate'] = audio.get('sample_rate')
        result['channels'] = audio.get('channels')
    return result


# ffmpeg 自己都认不出编码的流（codec_name 是 unknown / none / bin_data）。这种流没法复用：
# mov 系列复用器写头时会以「Tag ... incompatible with output codec id」整次失败。
# 真实案例：带章节的 m4b —— ffmpeg 写章节时会额外写一条 QuickTime 章节文字轨
# （`mov_create_chapter_track()`，tag 为 'text'），再读回来就是一条 codec 为 `bin_data`
# 的 data 流。它只能由解复用器读，复用器写不了，所以 `-map 0 -c copy` 必然失败。
UNCOPYABLE_CODECS = frozenset({'', 'unknown', 'none', 'bin_data'})


def stream_summary(info):
    """每条流的骨架信息，写回失败时用来判断哪些流复制不了、并告诉用户跳过了什么。"""
    result = []
    for stream in info.get('streams') or ():
        result.append({
            'index': stream.get('index'),
            'type': stream.get('codec_type') or '',
            'codec': (stream.get('codec_name') or '').lower(),
        })
    return result


def uncopyable_streams(streams):
    """ffmpeg 认不出编码的流序号；写入前可以把它们排除掉。"""
    return [s['index'] for s in streams
            if s.get('index') is not None and s.get('codec', '') in UNCOPYABLE_CODECS]


def read_media(path, ffmpeg, ffprobe, want_cover=True):
    """媒体文件的完整描述：标签、封面字节、技术信息。"""
    info = probe(path, ffprobe)
    tags = collect_tags(info)
    tech = technical_info(info)
    ext = os.path.splitext(path)[1][1:].lower()
    cover = None
    cover_ext = None
    found = cover_stream(info)
    if want_cover and ffmpeg and found is not None:
        index, codec = found
        try:
            cover = read_cover(path, index, ffmpeg)
            cover_ext = 'jpg' if codec == 'mjpeg' else codec
        except ffutil.ToolError:
            cover = None
    return {
        'path': path,
        'ext': ext,
        'tags': tags,
        'cover': cover,
        'cover_ext': cover_ext,
        'cover_stream_index': found[0] if found else None,
        'tech': tech,
        'streams': stream_summary(info),
    }


def _to_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


# ------------------------------------------------------ 取值归一化

def split_authors(value):
    """把 artist 标签拆成多个人。

    逗号刻意不作为分隔符："Smith, John" 是姓在前的一个作者，按逗号拆会把他拆坏。
    """
    if not value:
        return []
    parts = re.split(r'[;&]', str(value))
    return [p.strip() for p in parts if p.strip()]


def split_tags(value):
    """把标签值拆成多个条目。这里逗号算分隔符。"""
    if not value:
        return []
    parts = re.split(r'[;&,]', str(value))
    return [p.strip() for p in parts if p.strip()]


def media_tags(tags):
    """从 ffprobe 的标签字典里取 calibre 标签，只看 `keywords`。

    `genre` / `grouping` 一律不读：流派不是标签，混在一起会让书库塞满无关的东西。
    """
    return split_tags(tags.get(TAG_KEYS[0]))


def canonical_text(value):
    return re.sub(r'\s+', ' ', str(value or '')).strip()


def canonical_authors(value):
    # value 是作者字符串的可迭代对象，或者一个原始标签字符串
    if isinstance(value, (list, tuple)):
        parts = []
        for item in value:
            parts.extend(split_authors(item) or [canonical_text(item)])
    else:
        parts = split_authors(value)
    return frozenset(p.casefold() for p in parts if p)


def render_authors(authors):
    """用 split_authors() 能读回来的分隔符连接作者，这样写进去的标签下次对比时与书库值相等。"""
    return ' & '.join(canonical_text(a) for a in (authors or ()) if canonical_text(a))


def canonical_tags(value):
    if isinstance(value, (list, tuple)):
        parts = []
        for item in value:
            parts.extend(split_tags(item) or [canonical_text(item)])
    else:
        parts = split_tags(value)
    return frozenset(p.casefold() for p in parts if p)


def canonical_index(value):
    number = _to_float(value)
    if number is None:
        match = re.search(r'\d+(?:\.\d+)?', str(value or ''))
        if not match:
            return None
        number = float(match.group())
    return round(number, 2)


def render_index(value):
    number = canonical_index(value)
    if number is None:
        return None
    return str(int(number)) if number == int(number) else ('%.2f' % number).rstrip('0')


def canonical_date(value):
    """返回 (年, 月, 日, 精度)，精度为 1/2/3，取决于来源到底写明了多少日期信息。"""
    if value is None or value == '':
        return None
    if hasattr(value, 'year'):  # datetime/date from calibre
        return (value.year, value.month, value.day, 3)
    text = str(value).strip()
    month_names = {
        'jan': 1, 'feb': 2, 'mar': 3, 'apr': 4, 'may': 5, 'jun': 6, 'jul': 7,
        'aug': 8, 'sep': 9, 'oct': 10, 'nov': 11, 'dec': 12,
    }
    for name, number in month_names.items():
        match = re.search(r'\b%s\w*\b\D+(\d{4})' % name, text, re.IGNORECASE)
        if match:
            return (int(match.group(1)), number, None, 2)
    digits = re.findall(r'\d+', text)
    if not digits:
        return None
    year = int(digits[0][:4])
    if len(digits) >= 3 or (len(digits) >= 2 and len(digits[1]) <= 2 and '-' in text):
        month = int(digits[1]) if len(digits) >= 2 else None
        day = int(digits[2]) if len(digits) >= 3 else None
        return (year, month, day, 3 if day else 2)
    if len(digits) == 2 and len(digits[1]) <= 2:
        return (year, int(digits[1]), None, 2)
    return (year, None, None, 1)


def render_date(value):
    parsed = canonical_date(value)
    if not parsed:
        return ''
    year, month, day, precision = parsed
    if precision >= 3 and day:
        return '%04d-%02d-%02d' % (year, month, day)
    if precision >= 2 and month:
        return '%04d-%02d' % (year, month)
    return '%04d' % year


def render_date_for_tag(value):
    """要写入的标签值：只保留容器习惯存储的粒度。"""
    parsed = canonical_date(value)
    if not parsed:
        return ''
    year, month, day, _ = parsed
    if month and day:
        return '%04d-%02d-%02d' % (year, month, day)
    if month:
        return '%04d-%02d' % (year, month)
    return '%04d' % year


def cover_hash(data):
    return hashlib.sha1(data).hexdigest() if data else None


# ------------------------------------------------------------- 对比

def compare(lib, media, custom_columns=()):
    """构造对比行。

    :param lib: calibre 一侧的值字典，已降维成纯 Python 类型：
        {'title': str, 'authors': [..], 'series': str, 'series_index': float,
         'tags': [..], 'comments': str, 'publisher': str, 'pubdate': date|None,
         'identifiers': {'isbn': ..}, 'cover': bytes|None, '#col': value}
    :param media: read_media() 返回的字典
    :param custom_columns: (lookup_name, label, tag_key) 的可迭代对象
    """
    tags = media['tags']
    rows = []

    for field, tag_keys in FIELD_TO_TAGS:
        lib_raw = lib.get(field)
        tag_key = _first_present_tag(tags, tag_keys) or tag_keys[0]
        file_raw = tags.get(tag_key) if tag_key in tags else None

        lib_display, lib_canon, write_value = _render_lib(field, lib_raw)
        file_display, file_canon = _render_file(field, file_raw)

        status, tooltip_key, tooltip_args = _judge(field, lib_canon, file_canon, tags, tag_keys)
        # 已经一致的行绝不产生写入：空字符串的 write_value 意思是“删除这个标签”，用在这里就错了。
        if status == STATUS_SAME:
            write_value = None
        rows.append(FieldRow(
            field, field, lib_display, file_display, status,
            writable=status != STATUS_UNSUPPORTED,
            tooltip_key=tooltip_key, tooltip_args=tooltip_args, tag_key=tag_key,
            write_value=write_value if status != STATUS_UNSUPPORTED else None,
        ))

    for field in UNSUPPORTED_FIELDS:
        rows.append(FieldRow(
            field, field,
            _render_rating(lib.get('rating')), '', STATUS_UNSUPPORTED,
            writable=False, tooltip_key='unsupported_field',
        ))

    for lookup, label, tag_key in custom_columns:
        lib_raw = lib.get(lookup)
        lib_display = canonical_text(lib_raw)
        file_display = canonical_text(tags.get((tag_key or '').lower()))
        status = _status_for_text(lib_display, file_display)
        if status == STATUS_SAME:
            write_value = None
        else:
            write_value = lib_display
        rows.append(FieldRow(
            lookup, label, lib_display, file_display, status,
            tag_key=tag_key, write_value=write_value,
        ))

    rows.append(_cover_row(lib.get('cover'), media))
    # 书库配了时长列时，时长是一行「仅同步回书库」的可勾选行（能看到书库当前的值）；
    # 没配时长列时退回只读的信息行，和以前一样。
    has_duration_column = 'duration' in lib
    if has_duration_column:
        rounded = _duration_row(lib, media)
        if rounded is not None:
            rows.append(rounded)
    rows.extend(_info_rows(media, with_duration=not has_duration_column))
    return rows


# FieldRow 可能携带的 tooltip 键的英文回退文本。界面会用同样的键渲染自己的本地化
# 版本；这里保留是为了日志、测试和任何非 GUI 调用方仍能读到可读的说明。
TOOLTIPS = {
    'clears_date': 'Writing this clears the existing date tag.',
    'clears_tag': 'Writing this clears the existing tag.',
    'alias_tags': 'The file also carries %s; only %s will be written.',
    'library_only': 'Only written back to the library: the duration comes from the media stream, so there is no tag to write into the file.',
    'unsupported_field': 'ffmpeg has no standard tag for this field; it cannot be written to media files.',
    'cover_unsupported': 'ffmpeg cannot store cover art in .%s containers.',
    'cover_removes': 'Writing this removes the existing cover art.',
    'derived_info': 'Derived from the stream; not a writable tag.',
}


def _first_present_tag(tags, tag_keys):
    for key in tag_keys:
        if key.lower() in tags:
            return key.lower()
    return None


def _render_lib(field, value):
    """返回 calibre 一侧值的 (显示文本, 可比较值, 要写入的值)。"""
    if field == 'authors':
        display = render_authors(value)
        return display, canonical_authors(value), display
    if field == 'tags':
        # 文件里保留书库的原始大小写；canonical_tags() 只用于比较，它刻意 casefold。
        cleaned = [canonical_text(t) for t in (value or ()) if canonical_text(t)]
        items = sorted(cleaned, key=str.casefold)
        return ', '.join(items), canonical_tags(value), '; '.join(items)
    if field == 'series_index':
        number = canonical_index(value)
        if number is None:
            return '', None, ''
        return render_index(value), number, render_index(value)
    if field == 'pubdate':
        parsed = canonical_date(value)
        if not parsed:
            return '', None, ''
        return render_date(value), parsed, render_date_for_tag(value)
    if field == 'identifiers':
        isbn = canonical_text((value or {}).get('isbn', ''))
        return isbn, isbn.casefold() if isbn else None, isbn
    display = canonical_text(value)
    return display, display.casefold() if display else None, display


def _render_file(field, value):
    if value is None:
        return '', None
    if field == 'authors':
        return canonical_text(value), canonical_authors(value)
    if field == 'tags':
        return canonical_text(value), canonical_tags(value)
    if field == 'series_index':
        return canonical_text(value), canonical_index(value)
    if field == 'pubdate':
        return canonical_text(value), canonical_date(value)
    text = canonical_text(value)
    return text, text.casefold() if text else None


def _status_for_text(lib_display, file_display):
    if not lib_display and not file_display:
        return STATUS_SAME
    if not lib_display:
        return STATUS_EMPTY_IN_LIB
    if not file_display:
        return STATUS_MISSING_IN_FILE
    return STATUS_SAME if lib_display.casefold() == file_display.casefold() else STATUS_DIFFERENT


def _judge(field, lib_canon, file_canon, tags, tag_keys):
    """判定一行字段的状态，外加一个给界面渲染的 tooltip 键。

    只有当所有备选标签键都不存在时，才算是“文件中缺失”；否则写主键会在既有含义旁边
    再造一个重复含义。
    """
    if field == 'pubdate':
        if lib_canon is None and file_canon is None:
            return STATUS_SAME, None, ()
        if lib_canon is None:
            return STATUS_EMPTY_IN_LIB, 'clears_date', ()
        if file_canon is None:
            return STATUS_MISSING_IN_FILE, None, ()
        # 按文件实际存储的精度比较，这样只有年份的标签不会和一个完整的书库日期显示为不同。
        _, _, _, precision = file_canon
        if lib_canon[:precision] == file_canon[:precision]:
            return STATUS_SAME, None, ()
        return STATUS_DIFFERENT, None, ()

    if lib_canon is None and file_canon is None:
        return STATUS_SAME, None, ()
    if lib_canon is None:
        return STATUS_EMPTY_IN_LIB, 'clears_tag', ()
    if file_canon is None:
        return STATUS_MISSING_IN_FILE, None, ()
    if lib_canon == file_canon:
        return STATUS_SAME, None, ()
    primary = _first_present_tag(tags, tag_keys) or tag_keys[0]
    aliases = [k for k in tag_keys if k.lower() in tags and k.lower() != primary]
    if aliases:
        return STATUS_DIFFERENT, 'alias_tags', (', '.join(aliases), tag_keys[0])
    return STATUS_DIFFERENT, None, ()


def _render_rating(value):
    if value is None:
        return ''
    try:
        return '%g' % float(value)
    except (TypeError, ValueError):
        return ''


def _cover_row(lib_cover, media):
    file_cover = media.get('cover')
    lib_hash = cover_hash(lib_cover)
    file_hash = cover_hash(file_cover)
    ext = media.get('ext', '')
    tooltip_key, tooltip_args = None, ()
    if ext not in COVER_WRITABLE:
        status = STATUS_UNSUPPORTED
        writable = False
        tooltip_key, tooltip_args = 'cover_unsupported', (ext,)
    elif lib_hash and file_hash and lib_hash == file_hash:
        status, writable = STATUS_SAME, True
    elif not lib_hash and not file_hash:
        status, writable = STATUS_SAME, True
    elif not lib_hash:
        status, writable = STATUS_EMPTY_IN_LIB, True
        tooltip_key = 'cover_removes'
    elif not file_hash:
        status, writable = STATUS_MISSING_IN_FILE, True
    else:
        status, writable = STATUS_DIFFERENT, True
    if status == STATUS_EMPTY_IN_LIB:
        write_value = False          # remove the file's existing art
    elif status in (STATUS_DIFFERENT, STATUS_MISSING_IN_FILE):
        write_value = True           # embed the library cover
    else:
        write_value = None           # leave alone
    return FieldRow(
        'cover', 'cover', '', '', status, writable=writable,
        tooltip_key=tooltip_key, tooltip_args=tooltip_args, tag_key=None,
        write_value=write_value,
        lib_count=len(lib_cover) if lib_cover else None,
        file_count=len(file_cover) if file_cover else None,
    )


def _duration_row(lib, media):
    """时长行：两侧都显示，但只能写回书库。

    文件里的时长来自媒体流，没有可写的标签，所以只有两边**不一样**时状态才是
    「仅同步回书库」（并且默认勾选）；一样时就是普通的「一致」，和其他行一致、默认不勾。
    书库为空、文件有时长时也算不一致（需要补上）。两边都是空时返回 None，不显示这一行。
    """
    seconds = _to_float(media['tech'].get('duration'))
    lib_seconds = _to_float(lib.get('duration'))
    if seconds is None and lib_seconds is None:
        return None
    if seconds is not None and lib_seconds is not None:
        same = abs(lib_seconds - seconds) < 0.005
    else:
        same = False
    status = STATUS_SAME if same else STATUS_LIB_ONLY
    return FieldRow(
        'duration', 'duration',
        _format_duration(lib_seconds) if lib_seconds is not None else '',
        _format_duration(seconds) if seconds is not None else '',
        status, writable=True,
        # 只有文件里真有时长、而且和书库不一样时才值得勾（勾了才有东西写）。
        default_checked=not same and seconds is not None,
        tooltip_key='library_only',
    )


def _info_rows(media, with_duration=True):
    tech = media['tech']
    rows = []
    duration = tech.get('duration')
    if with_duration and duration is not None:
        rows.append(FieldRow(
            'duration', 'duration', '', _format_duration(duration),
            STATUS_INFO, writable=False, tooltip_key='derived_info'))
    if tech.get('container'):
        rows.append(FieldRow('container', 'container', '', tech['container'],
                             STATUS_INFO, writable=False))
    if tech.get('is_video'):
        rows.append(FieldRow(
            'video', 'video', '',
            '%s %sx%s' % (tech.get('video_codec'), tech.get('width'), tech.get('height')),
            STATUS_INFO, writable=False))
    if tech.get('audio_codec'):
        audio = '%s' % tech['audio_codec']
        if tech.get('sample_rate'):
            audio += ' %sHz' % tech['sample_rate']
        if tech.get('channels'):
            audio += ' %sch' % tech['channels']
        rows.append(FieldRow('audio', 'audio', '', audio, STATUS_INFO, writable=False))
    if tech.get('bit_rate'):
        rows.append(FieldRow('bit_rate', 'bit_rate', '',
                             '%s kb/s' % (int(tech['bit_rate']) // 1000), STATUS_INFO, writable=False))
    return rows


def _format_duration(seconds):
    seconds = int(round(seconds))
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return '%d:%02d:%02d' % (hours, minutes, secs)
    return '%d:%02d' % (minutes, secs)


# ------------------------------------------------------------- 写回

class WriteOptions:
    def __init__(self, id3v2_version=3, write_cover=True):
        self.id3v2_version = id3v2_version
        self.write_cover = write_cover


# 封面的三态决定，这样用户没勾选的行会保留文件里原有的封面，而不是悄悄丢掉。
COVER_KEEP = 'keep'
COVER_SET = 'set'
COVER_REMOVE = 'remove'


def rows_to_updates(rows, cover=None):
    """把勾选的行归约成 ({tag_key: value}, cover_action)。

    write_value 为 '' 表示删除标签。用户没勾选的行这里根本不出现，配合
    `-map_metadata 0` 就让它们的标签保持原样。`cover` 是封面行的 write_value：
    True -> 设置，False -> 删除，None -> 保持。
    """
    updates = {}
    cover_action = COVER_KEEP
    if cover is True:
        cover_action = COVER_SET
    elif cover is False:
        cover_action = COVER_REMOVE
    for row in rows:
        if row.field == 'cover' or row.status in (STATUS_INFO, STATUS_UNSUPPORTED,
                                                  STATUS_LIB_ONLY):
            continue
        if not row.writable:
            continue
        if not row.tag_key or row.write_value is None:
            continue
        updates[row.tag_key] = row.write_value
    return updates, cover_action


def collect_cover(rows):
    """从行列表里取出封面行的决定。"""
    for row in rows:
        if row.field == 'cover':
            return row.write_value
    return None


def build_write_command(path, out_path, updates, cover_action, cover_data, cover_path,
                        ffmpeg, ext, opts, existing_cover_index=None,
                        skip_streams=(), only_audio_video=False, brand=None):
    """拼出 ffmpeg 的 argv。从 write_media 里拆出来，测试可以直接断言，不碰文件系统。

    `skip_streams` 是要额外排除的流序号；`only_audio_video` 只复制音视频流
    （写回头失败时的兜底：某些轨道目标容器根本放不下）；`brand` 是 mp4 系的
    major_brand（例如 "M4B "），不显式传的话 ffmpeg 会退回 "M4A "。
    """
    argv = [ffmpeg, '-v', 'error', '-y', '-i', path]

    setting_cover = bool(
        cover_action == COVER_SET and cover_data and opts.write_cover
        and ext in COVER_WRITABLE and cover_path
    )
    if setting_cover:
        argv += ['-i', cover_path]

    if only_audio_video:
        argv += ['-map', '0:a?', '-map', '0:v?']
    else:
        argv += ['-map', '0']
    # 替换或删除封面时要把旧的图片流排除掉，否则输出里还留着上一张封面（或者带着两张）。
    # 用户没勾选这一行时，原有封面必须原样保留。两种映射方式都要处理。
    dropping = (setting_cover or cover_action == COVER_REMOVE) and existing_cover_index is not None
    if dropping:
        argv += ['-map', '-0:%s' % existing_cover_index]
    if not only_audio_video:
        for index in skip_streams:
            argv += ['-map', '-0:%s' % index]
    if setting_cover:
        argv += ['-map', '1:v']

    argv += ['-map_metadata', '0', '-c', 'copy']

    for key, value in sorted(updates.items()):
        argv += ['-metadata', '%s=%s' % (key, value)]

    if ext == 'mp3':
        argv += ['-id3v2_version', str(opts.id3v2_version)]
    if ext in MP4_FAMILY:
        argv += ['-movflags', '+faststart']
        # major_brand 走 muxer 选项，不在 -map_metadata 的范围内；不传就变回 "M4A "。
        if brand:
            argv += ['-brand', brand]
    if setting_cover:
        argv += ['-disposition:v:0', 'attached_pic']

    argv += [out_path]
    return argv, setting_cover


# 这些失败信息说明是「容器/复用器不肯接受某条轨道」，而不是文件读写或参数错误，
# 值得换一种更保守的 -map 重试一次。
RETRYABLE_ERRORS = (
    'could not write header',
    'incompatible with output codec id',
    'codec not currently supported in container',
    'muxer supports only codec',
    'does not support',
)


def _retryable(err):
    text = ('%s %s' % (getattr(err, 'stderr', ''), err)).lower()
    return any(hint in text for hint in RETRYABLE_ERRORS)


class WriteOutcome(str):
    """写回结果：本身就是写好的路径（可以直接当路径用），额外带上被跳过的流。

    `skipped` 非空表示为了能写进去，放弃复制了一些轨道（字幕、数据流、认不出的流）。
    """

    def __new__(cls, path, skipped=()):
        self = super().__new__(cls, path)
        self.skipped = tuple(skipped or ())
        return self


def write_media(path, updates, cover_action, cover_data, ffmpeg, opts=None,
                cover_ext='jpg', existing_cover_index=None, streams=(), brand=None):
    """按给定的标签更新原地重写 `path`。

    先写同目录的临时文件，再 os.replace() 覆盖原文件，所以写入失败或被取消时，
    永远不会留下写了一半的媒体文件。

    有些文件带着目标容器放不下的轨道（例如 m4b 里 codec 认不出的 text 流、
    mp4 里的 subrip 字幕），ffmpeg 会以「Could not write header」整次失败。
    这种情况下退回更保守的 -map 重试：先跳过认不出编码的流，再只复制音视频流；
    丢弃了哪些流会记在返回值的 `skipped` 上，由上层提示用户。
    """
    opts = opts or WriteOptions()
    ext = os.path.splitext(path)[1][1:].lower()
    directory = os.path.dirname(os.path.abspath(path))
    base = os.path.splitext(os.path.basename(path))[0]
    tmp_path = None
    cover_path = None

    unknown = uncopyable_streams(streams)
    # (名字, 额外跳过的流, 是否只复制音视频)
    attempts = [('all', (), False)]
    if unknown:
        attempts.append(('skip_unknown', tuple(unknown), False))
    attempts.append(('audio_video', (), True))

    try:
        if cover_action == COVER_SET and cover_data and opts.write_cover:
            cover_handle, cover_path = tempfile.mkstemp(
                suffix='.' + (cover_ext or 'jpg'), dir=directory)
            with os.fdopen(cover_handle, 'wb') as fp:
                fp.write(cover_data)

        # 保留开头的点（残留文件好认）但截断书名：深层书库里媒体路径本来就逼近 MAX_PATH，
        # 书名一长，临时文件名就会越过它。
        handle, tmp_path = tempfile.mkstemp(
            prefix='.%s.' % base[:24], suffix='.' + (ext or 'tmp'), dir=directory)
        os.close(handle)

        skipped = ()
        for attempt_index, (name, skip, only_av) in enumerate(attempts):
            argv, _ = build_write_command(
                path, tmp_path, updates, cover_action, cover_data, cover_path,
                ffmpeg, ext, opts, existing_cover_index,
                skip_streams=skip, only_audio_video=only_av, brand=brand)
            try:
                ffutil.run_tool(argv)
            except ffutil.ToolError as err:
                # 还没写成功，可以换一种更保守的映射再来一次；
                # 但非「容器不接受这条轨道」的错误、以及已经是最后一次尝试，就直接报错。
                if _retryable(err) and attempt_index < len(attempts) - 1:
                    continue
                raise
            skipped = _dropped_streams(name, skip, streams)
            break

        try:
            os.chmod(tmp_path, stat.S_IMODE(os.stat(path).st_mode))
        except OSError:
            pass
        os.replace(tmp_path, path)
        tmp_path = None
    finally:
        for leftover in (tmp_path, cover_path):
            if leftover and os.path.exists(leftover):
                try:
                    os.remove(leftover)
                except OSError:
                    pass
    return WriteOutcome(path, skipped)


def _dropped_streams(name, skip, streams):
    """算出这次重试放弃了哪些流，用于提示用户。"""
    if name == 'audio_video':
        return [s for s in streams if s.get('type') not in ('audio', 'video')]
    if name == 'skip_unknown':
        wanted = set(skip)
        return [s for s in streams if s.get('index') in wanted]
    return []


def read_and_compare(path, lib, ffmpeg, ffprobe, custom_columns=()):
    """便捷函数：一次调用读完文件并生成它的对比行。"""
    media = read_media(path, ffmpeg, ffprobe)
    return media, compare(lib, media, custom_columns=custom_columns)
