#!/usr/bin/env python3
# 针对 make_samples.py 生成的真实文件无头测试 ffmeta。
# 需要 PATH 里有 ffmpeg/ffprobe（或用 FFMPEG/FFPROBE 环境变量指定）。

import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'common'))

import ffmeta  # noqa: E402
import ffutil  # noqa: E402

SAMPLES = os.path.join(HERE, 'out')
FAILURES = []


def check(name, condition, detail=''):
    if condition:
        print('  PASS  %s' % name)
    else:
        print('  FAIL  %s %s' % (name, detail))
        FAILURES.append(name)


def eq(name, actual, expected):
    check(name, actual == expected, '(got %r, want %r)' % (actual, expected))


FFMPEG = os.environ.get('FFMPEG') or ffutil.find_binary('ffmpeg')
FFPROBE = os.environ.get('FFPROBE') or ffutil.find_binary('ffprobe')


def sample(name):
    path = os.path.join(SAMPLES, name)
    if not os.path.isfile(path):
        raise SystemExit('missing sample %s - run tests/make_samples.sh first' % path)
    return path


def read(name):
    return ffmeta.read_media(sample(name), FFMPEG, FFPROBE)


def scratch(source, dest=None):
    """把样本拷进临时工作目录，写入永远不碰原始文件。"""
    path = os.path.join(WORK, dest or source)
    shutil.copyfile(sample(source), path)
    return path


def count_attached_pics(path):
    out = subprocess.run(
        [FFPROBE, '-v', 'quiet', '-print_format', 'json', '-show_streams', path],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE).stdout.decode('utf-8', 'replace')
    streams = json.loads(out).get('streams') or ()
    return sum(1 for s in streams if (s.get('disposition') or {}).get('attached_pic') == 1)


def leftover_temp_files(directory):
    return [n for n in os.listdir(directory) if n.startswith('.')]


# ------------------------------------------------------------ 纯值

def test_normalisers():
    print('value normalisation')
    eq('track 3/12 -> 3.0', ffmeta.canonical_index('3/12'), 3.0)
    eq('track 7 -> 7.0', ffmeta.canonical_index('7'), 7.0)
    eq('blank index -> None', ffmeta.canonical_index(''), None)
    eq('index renders integral without decimals', ffmeta.render_index(3.0), '3')
    eq('index keeps fraction', ffmeta.render_index(2.5), '2.5')

    eq('authors split on ampersand', sorted(ffmeta.canonical_authors('A & B')), ['a', 'b'])
    eq('authors split on semicolon', sorted(ffmeta.canonical_authors('A; B')), ['a', 'b'])
    eq('comma is NOT an author separator', ffmeta.canonical_authors('Smith, John'),
       frozenset({'smith, john'}))
    eq('author list canonicalises', sorted(ffmeta.canonical_authors(['A', 'B'])), ['a', 'b'])
    eq('empty authors -> empty set', ffmeta.canonical_authors([]), frozenset())
    eq('authors render joins with ampersand', ffmeta.render_authors(['A', 'B']), 'A & B')
    eq('author render/read round-trips',
       ffmeta.canonical_authors(ffmeta.render_authors(['A', 'B'])),
       ffmeta.canonical_authors(['A', 'B']))

    eq('genre split on semicolon', sorted(ffmeta.canonical_tags('Audiobook; Fiction')),
       ['audiobook', 'fiction'])
    eq('genre split on comma', sorted(ffmeta.canonical_tags('Audiobook, Fiction')),
       ['audiobook', 'fiction'])
    eq('tag list canonicalises', sorted(ffmeta.canonical_tags(['Audiobook', 'Fiction'])),
       ['audiobook', 'fiction'])
    eq('tags render/read round-trips',
       ffmeta.canonical_tags('; '.join(sorted(ffmeta.canonical_tags(['A', 'B'])))),
       ffmeta.canonical_tags(['A', 'B']))

    eq('full date precision 3', ffmeta.canonical_date('2011-05-03'), (2011, 5, 3, 3))
    eq('year only precision 1', ffmeta.canonical_date('2020'), (2020, None, None, 1))
    eq('year-month precision 2', ffmeta.canonical_date('2011-05'), (2011, 5, None, 2))
    eq('textual month parsed', ffmeta.canonical_date('May 2011'), (2011, 5, None, 2))
    eq('empty date -> None', ffmeta.canonical_date(''), None)

    class FakeDate:
        year, month, day = 2001, 2, 3

    eq('datetime object accepted', ffmeta.canonical_date(FakeDate()), (2001, 2, 3, 3))
    eq('tag date keeps only year', ffmeta.render_date_for_tag('2020'), '2020')
    eq('tag date keeps full date', ffmeta.render_date_for_tag('2011-05-03'), '2011-05-03')

    eq('duration formats with hours', ffmeta._format_duration(3725), '1:02:05')
    eq('duration formats without hours', ffmeta._format_duration(125), '2:05')


# ------------------------------------------------------------ 读取

def test_read_tagged_audio():
    print('reading tagged audio')
    for name in ('tagged.mp3', 'tagged.m4a', 'tagged.flac'):
        info = read(name)
        tags = info['tags']
        eq('%s title' % name, tags.get('title'), 'The Original Title')
        eq('%s artist' % name, tags.get('artist'), 'Original Author')
        eq('%s album' % name, tags.get('album'), 'Original Series')
        eq('%s track' % name, tags.get('track'), '3/12')
        eq('%s keywords' % name, tags.get('keywords'), 'Audiobook; Fiction')
        eq('%s genre is read raw but ignored for tags' % name, tags.get('genre'),
           'Legacy Genre')
        eq('%s media_tags only looks at keywords' % name, ffmeta.media_tags(tags),
           ['Audiobook', 'Fiction'])
        eq('%s date' % name, tags.get('date'), '2011-05-03')
        check('%s has cover art' % name, info['cover'] is not None and len(info['cover']) > 0)
        check('%s cover is jpeg' % name, (info['cover'] or b'').startswith(b'\xff\xd8'),
              repr((info['cover'] or b'')[:4]))
        eq('%s cover stream index found' % name, info['cover_stream_index'], 1)
        eq('%s duration parsed' % name, round(info['tech']['duration'], 1), 2.0)
        check('%s stream summary is available' % name,
              any(s.get('type') == 'audio' for s in info['streams']), info['streams'])
        check('%s not flagged as video' % name, info['tech']['is_video'] is False)


def test_read_video_is_not_cover():
    print('video streams must not be treated as cover art')
    info = read('tagged_video.mp4')
    eq('mp4 video title', info['tags'].get('title'), 'A Video Lecture')
    eq('mp4 video artist', info['tags'].get('artist'), 'Some Presenter')
    eq('mp4 video cover is None', info['cover'], None)
    eq('mp4 video cover_stream_index is None', info['cover_stream_index'], None)
    check('mp4 flagged as video', info['tech']['is_video'] is True)
    eq('mp4 width', info['tech']['width'], 320)
    eq('mp4 height', info['tech']['height'], 240)
    eq('mp4 video codec', info['tech']['video_codec'], 'h264')
    eq('mp4 audio codec', info['tech']['audio_codec'], 'aac')

    mkv = read('tagged_video.mkv')
    eq('mkv title', mkv['tags'].get('title'), 'Matroska Talk')
    # Matroska 报的 tag 键是大写，collect_tags 必须转小写。
    eq('mkv uppercase ARTIST normalised to artist', mkv['tags'].get('artist'), 'Another Presenter')
    eq('mkv cover is None', mkv['cover'], None)
    check('mkv flagged as video', mkv['tech']['is_video'] is True)


def test_read_untagged():
    print('reading untagged files')
    for name in ('untagged.mp3', 'untagged.m4a'):
        info = read(name)
        check('%s has no title tag' % name, 'title' not in info['tags'], info['tags'])
        eq('%s has no cover' % name, info['cover'], None)
        check('%s still reports duration' % name, info['tech']['duration'] is not None)


# ------------------------------------------------------------ 对比

def lib(**overrides):
    base = {
        'title': 'The Original Title',
        'authors': ['Original Author'],
        'series': 'Original Series',
        'series_index': 3.0,
        'tags': ['Audiobook', 'Fiction'],
        'comments': 'Original description text.',
        'publisher': 'Original Publisher',
        'pubdate': None,
        'identifiers': {},
        'cover': None,
    }
    base.update(overrides)
    return base


def rows_by_field(rows):
    return {r.field: r for r in rows}


def test_compare_identical():
    print('comparison: identical values')
    info = read('tagged.mp3')
    rows = rows_by_field(ffmeta.compare(lib(), info))
    for field in ('title', 'authors', 'series', 'series_index', 'tags', 'comments', 'publisher'):
        eq('%s reports same' % field, rows[field].status, ffmeta.STATUS_SAME)
        check('%s not checked by default' % field, rows[field].checked_by_default is False)


def test_compare_different():
    print('comparison: differing values default to checked')
    info = read('tagged.mp3')
    rows = rows_by_field(ffmeta.compare(
        lib(title='Renamed Book', authors=['New Author'], series_index=7.0,
            tags=['Nonfiction']),
        info))
    eq('title different', rows['title'].status, ffmeta.STATUS_DIFFERENT)
    check('title checked by default', rows['title'].checked_by_default is True)
    eq('title write value', rows['title'].write_value, 'Renamed Book')
    eq('authors different', rows['authors'].status, ffmeta.STATUS_DIFFERENT)
    eq('authors write value', rows['authors'].write_value, 'New Author')
    eq('series_index different', rows['series_index'].status, ffmeta.STATUS_DIFFERENT)
    eq('series_index write value', rows['series_index'].write_value, '7')
    eq('tags different', rows['tags'].status, ffmeta.STATUS_DIFFERENT)
    check('untouched field stays same', rows['publisher'].status == ffmeta.STATUS_SAME)


def test_compare_missing_and_empty():
    print('comparison: missing in file / empty in library')
    tagged = read('tagged.mp3')
    values = lib(identifiers={'isbn': '9780000000000'})
    values['#narrator'] = 'Some Narrator'
    rows = rows_by_field(ffmeta.compare(
        values, tagged, custom_columns=(('#narrator', 'Narrator', 'album_artist'),)))
    # tagged.mp3 没有 isbn 标签
    isbn = rows['identifiers']
    eq('isbn missing in file', isbn.status, ffmeta.STATUS_MISSING_IN_FILE)
    check('isbn checked by default', isbn.checked_by_default is True)
    eq('isbn write value', isbn.write_value, '9780000000000')
    narrator = rows['#narrator']
    eq('custom column missing in file', narrator.status, ffmeta.STATUS_MISSING_IN_FILE)
    eq('custom column tag key', narrator.tag_key, 'album_artist')
    eq('custom column write value', narrator.write_value, 'Some Narrator')

    # 书库里为空而文件里有 -> 不勾选，并带警告。
    empty = rows_by_field(ffmeta.compare(lib(title='', publisher=''), tagged))
    eq('empty title vs filled file', empty['title'].status, ffmeta.STATUS_EMPTY_IN_LIB)
    check('empty title NOT checked by default', empty['title'].checked_by_default is False)
    check('empty title warns about clearing', 'clear' in empty['title'].tooltip.lower(),
          empty['title'].tooltip)


def test_compare_date_precision():
    print('comparison: date granularity')
    video = read('tagged_video.mp4')  # date tag is year-only "2020"

    class Date:
        year, month, day = 2020, 9, 17

    rows = rows_by_field(ffmeta.compare(lib(title='A Video Lecture', pubdate=Date()), video))
    eq('year-only file tag matches full library date', rows['pubdate'].status, ffmeta.STATUS_SAME)

    class OtherDate:
        year, month, day = 2019, 1, 1

    rows2 = rows_by_field(ffmeta.compare(lib(title='A Video Lecture', pubdate=OtherDate()), video))
    eq('mismatched year reports different', rows2['pubdate'].status, ffmeta.STATUS_DIFFERENT)


def test_compare_rating_unsupported():
    print('comparison: rating is reported unsupported')
    info = read('tagged.mp3')
    rows = rows_by_field(ffmeta.compare(lib(rating=4.0), info))
    eq('rating status', rows['rating'].status, ffmeta.STATUS_UNSUPPORTED)
    check('rating not writable', rows['rating'].writable is False)
    check('rating not checked', rows['rating'].checked_by_default is False)


def test_tags_case_preserved():
    print('comparison: tag casing is preserved when writing')
    info = read('tagged.mp3')
    rows = rows_by_field(ffmeta.compare(lib(tags=['Nonfiction', 'Audio Book']), info))
    eq('write value keeps original case', rows['tags'].write_value, 'Audio Book; Nonfiction')
    eq('display keeps original case', rows['tags'].lib_display, 'Audio Book, Nonfiction')
    # 往返：写下去的值下次必须和书库值比较为相等。
    written = rows['tags'].write_value
    eq('written tags read back equal', ffmeta.canonical_tags(written),
       ffmeta.canonical_tags(['Nonfiction', 'Audio Book']))


def test_authors_case_preserved():
    print('comparison: author casing is preserved when writing')
    info = read('tagged.mp3')
    rows = rows_by_field(ffmeta.compare(lib(authors=['Ursula K. Le Guin']), info))
    eq('write value keeps author case', rows['authors'].write_value, 'Ursula K. Le Guin')
    eq('written authors read back equal',
       ffmeta.canonical_authors(rows['authors'].write_value),
       ffmeta.canonical_authors(['Ursula K. Le Guin']))


def test_info_rows_are_read_only():
    print('technical rows are read-only')
    info = read('tagged_video.mp4')
    rows = rows_by_field(ffmeta.compare(lib(title='A Video Lecture'), info))
    # lib 里没有 'duration'（书库没配时长列）时，时长仍然是只读信息行。
    for field in ('duration', 'container', 'video', 'audio', 'bit_rate'):
        if field in rows:
            eq('%s is info' % field, rows[field].status, ffmeta.STATUS_INFO)
            check('%s not writable' % field, rows[field].writable is False)
            check('%s not checked' % field, rows[field].checked_by_default is False)
    check('video row shows resolution', '320x240' in rows['video'].file_display,
          rows['video'].file_display)


def test_duration_row_with_library_column():
    print('duration row when the library has a duration column')
    info = read('tagged.mp3')  # 文件 2.0 秒
    values = lib()
    values['duration'] = 5.0
    rows = rows_by_field(ffmeta.compare(values, info))
    row = rows['duration']
    eq('duration row only syncs to the library', row.status, ffmeta.STATUS_LIB_ONLY)
    check('duration row is checkable', row.writable is True)
    check('duration row is checked when the values differ', row.checked_by_default is True)
    eq('duration row shows the library value', row.lib_display, '0:05')
    eq('duration row shows the file value', row.file_display, '0:02')
    eq('duration row has no tag key', row.tag_key, None)
    eq('duration row is never written to the file', row.write_value, None)
    updates, cover = ffmeta.rows_to_updates([row], ffmeta.collect_cover([row]))
    eq('rows_to_updates ignores the duration row', updates, {})
    eq('duration row keeps the cover decision', cover, ffmeta.COVER_KEEP)
    check('tooltip explains it is library-only', 'library' in row.tooltip.lower(),
          row.tooltip)

    # 两边一致时状态就是普通的「一致」，和其他行一样默认不勾。
    values['duration'] = 2.0
    same_row = rows_by_field(ffmeta.compare(values, info))['duration']
    eq('equal duration shows as same', same_row.status, ffmeta.STATUS_SAME)
    check('equal duration is not checked', same_row.checked_by_default is False)

    # 书库为空、文件有时长：不一致，勾上（需要补）。
    values['duration'] = None
    empty = rows_by_field(ffmeta.compare(values, info))['duration']
    eq('empty library duration is library-only', empty.status, ffmeta.STATUS_LIB_ONLY)
    check('empty library duration is checked', empty.checked_by_default is True)
    eq('empty library value renders blank', empty.lib_display, '')

    # 文件里没有时长、只有书库有：算不一致，但没有东西可写，所以不勾。
    values['duration'] = 2.0
    no_duration_media = dict(info)
    no_duration_media['tech'] = dict(info['tech'])
    no_duration_media['tech'].pop('duration', None)
    missing = rows_by_field(ffmeta.compare(values, no_duration_media))['duration']
    eq('file without duration is not the same', missing.status, ffmeta.STATUS_LIB_ONLY)
    check('file without duration is not checked', missing.checked_by_default is False)
    eq('file without duration renders blank', missing.file_display, '')

    # 没有时长列时退回只读信息行，不出可勾选的行。
    no_column = rows_by_field(ffmeta.compare(lib(), info))['duration']
    eq('without a column the duration row stays info', no_column.status, ffmeta.STATUS_INFO)
    check('without a column it is not checkable', no_column.writable is False)


def test_cover_row_states():
    print('cover row states')
    info = read('tagged.mp3')
    with open(sample('cover.jpg'), 'rb') as fp:
        same_cover = fp.read()
    with open(sample('cover2.jpg'), 'rb') as fp:
        other_cover = fp.read()

    rows = rows_by_field(ffmeta.compare(lib(cover=same_cover), info))
    eq('same cover bytes -> same', rows['cover'].status, ffmeta.STATUS_SAME)
    eq('same cover -> keep', rows['cover'].write_value, None)

    rows = rows_by_field(ffmeta.compare(lib(cover=other_cover), info))
    eq('different cover -> different', rows['cover'].status, ffmeta.STATUS_DIFFERENT)
    eq('different cover -> set', rows['cover'].write_value, True)

    rows = rows_by_field(ffmeta.compare(lib(cover=None), info))
    eq('no lib cover -> empty_in_lib', rows['cover'].status, ffmeta.STATUS_EMPTY_IN_LIB)
    eq('no lib cover -> remove', rows['cover'].write_value, False)
    check('no lib cover NOT checked by default', rows['cover'].checked_by_default is False)

    info_nocov = read('untagged.mp3')
    rows = rows_by_field(ffmeta.compare(lib(cover=same_cover), info_nocov))
    eq('lib cover, none in file -> missing', rows['cover'].status, ffmeta.STATUS_MISSING_IN_FILE)
    eq('lib cover, none in file -> set', rows['cover'].write_value, True)

    video = read('tagged_video.mp4')
    rows = rows_by_field(ffmeta.compare(lib(title='A Video Lecture', cover=same_cover), video))
    check('mp4 video cover writable', rows['cover'].writable is True, rows['cover'].tooltip)


# ------------------------------------------------------------ 写回

def test_write_tags_and_preserve_others():
    print('write: update tags, preserve untouched ones')
    path = scratch('tagged.mp3', 'write_tags.mp3')
    before = ffmeta.read_media(path, FFMPEG, FFPROBE)
    check('encoder tag present before write', 'encoder' in before['tags'])

    updates = {
        'title': 'Updated Title',
        'artist': 'Updated Author',
        'album': 'Updated Series',
        'track': '7',
        'genre': 'Nonfiction',
        'date': '2020',
    }
    ffmeta.write_media(path, updates, ffmeta.COVER_KEEP, None, FFMPEG,
                       existing_cover_index=before['cover_stream_index'])
    after = ffmeta.read_media(path, FFMPEG, FFPROBE)
    for key, value in updates.items():
        eq('%s written' % key, after['tags'].get(key), value)
    check('untouched publisher tag preserved', after['tags'].get('publisher') == 'Original Publisher',
          after['tags'].get('publisher'))
    check('untouched comment tag preserved',
          after['tags'].get('comment') == 'Original description text.', after['tags'].get('comment'))
    eq('cover left alone', ffmeta.cover_hash(after['cover']), ffmeta.cover_hash(before['cover']))
    eq('still exactly one attached pic', count_attached_pics(path), 1)
    eq('audio stream intact', after['tech']['audio_codec'], 'mp3')
    eq('duration intact', round(after['tech']['duration'], 1), 2.0)


def test_write_deletes_tag_with_empty_value():
    print('write: empty value deletes the tag')
    path = scratch('tagged.mp3', 'write_delete.mp3')
    ffmeta.write_media(path, {'publisher': ''}, ffmeta.COVER_KEEP, None, FFMPEG)
    after = ffmeta.read_media(path, FFMPEG, FFPROBE)
    check('publisher tag removed', after['tags'].get('publisher') in (None, ''),
          after['tags'].get('publisher'))
    check('other tags untouched', after['tags'].get('title') == 'The Original Title')


def test_write_replaces_cover_without_duplicating():
    print('write: cover replacement does not duplicate the stream')
    path = scratch('tagged.mp3', 'write_cover.mp3')
    with open(sample('cover2.jpg'), 'rb') as fp:
        new_cover = fp.read()
    before = ffmeta.read_media(path, FFMPEG, FFPROBE)
    eq('one attached pic before', count_attached_pics(path), 1)

    ffmeta.write_media(path, {'title': 'Cover Swap'}, ffmeta.COVER_SET, new_cover, FFMPEG,
                       cover_ext='jpg', existing_cover_index=before['cover_stream_index'])
    after = ffmeta.read_media(path, FFMPEG, FFPROBE)
    eq('still exactly one attached pic', count_attached_pics(path), 1)
    eq('cover bytes replaced', ffmeta.cover_hash(after['cover']), ffmeta.cover_hash(new_cover))
    eq('title written alongside cover', after['tags'].get('title'), 'Cover Swap')


def test_write_removes_cover():
    print('write: cover removal')
    path = scratch('tagged.mp3', 'write_nocover.mp3')
    before = ffmeta.read_media(path, FFMPEG, FFPROBE)
    ffmeta.write_media(path, {}, ffmeta.COVER_REMOVE, None, FFMPEG,
                       existing_cover_index=before['cover_stream_index'])
    after = ffmeta.read_media(path, FFMPEG, FFPROBE)
    eq('attached pic removed', count_attached_pics(path), 0)
    eq('cover now None', after['cover'], None)
    check('audio stream survives cover removal', after['tech']['audio_codec'] == 'mp3')
    check('tags survive cover removal', after['tags'].get('title') == 'The Original Title')


def test_write_adds_cover_where_absent():
    print('write: adding a cover to a file that has none')
    path = scratch('untagged.mp3', 'write_addcover.mp3')
    eq('no attached pic before', count_attached_pics(path), 0)
    with open(sample('cover.jpg'), 'rb') as fp:
        cover = fp.read()
    ffmeta.write_media(path, {'title': 'Now With Art'}, ffmeta.COVER_SET, cover, FFMPEG,
                       cover_ext='jpg', existing_cover_index=None)
    after = ffmeta.read_media(path, FFMPEG, FFPROBE)
    eq('one attached pic added', count_attached_pics(path), 1)
    eq('cover bytes stored', ffmeta.cover_hash(after['cover']), ffmeta.cover_hash(cover))


def test_write_mp4_family_and_mkv():
    print('write: mp4 / m4a / mkv containers')
    cases = (('write_v.mp4', 'tagged_video.mp4', 'mp4'),
             ('write_a.m4a', 'tagged.m4a', 'm4a'),
             ('write_k.mkv', 'tagged_video.mkv', 'mkv'))
    for name, source, ext in cases:
        path = scratch(source, name)
        before = ffmeta.read_media(path, FFMPEG, FFPROBE)
        ffmeta.write_media(path, {'title': 'Retitled %s' % ext.upper()}, ffmeta.COVER_KEEP, None,
                           FFMPEG, existing_cover_index=before['cover_stream_index'])
        after = ffmeta.read_media(path, FFMPEG, FFPROBE)
        eq('%s title rewritten' % ext, after['tags'].get('title'), 'Retitled %s' % ext.upper())
        eq('%s container preserved' % ext, after['ext'], ext)
        eq('%s duration preserved' % ext, round(after['tech']['duration'], 1), 2.0)
        if ext in ('mp4', 'mkv'):
            check('%s video stream preserved' % ext, after['tech']['is_video'] is True)
            eq('%s resolution preserved' % ext,
               (after['tech']['width'], after['tech']['height']), (320, 240))


def test_write_drops_unmappable_tracks():
    """mov 复用器放不下的轨道（subrip 字幕、认不出编码的 text 流）要能被跳过并写成功。

    真实案例：一台 Windows 机器上的 m4b 带一条 codec 未知的 'text' 流，
    ffmpeg 写头时报 `Tag text incompatible with output codec id '98314'`，整次写回失败。
    """
    print('write: tracks the container cannot hold')
    # mkv 里塞一条 subrip 字幕，再把文件当成 .mp4 交给 write_media：
    # 输入的内容还是 mkv，输出容器按扩展名是 mp4，mov 不接受 subrip。
    source = os.path.join(WORK, 'with_subs.mkv')
    subprocess.run(
        [FFMPEG, '-v', 'error', '-y', '-i', sample('tagged.mp3'), '-c', 'copy', source],
        check=True)
    srt = os.path.join(WORK, 'subs.srt')
    with open(srt, 'w', encoding='utf-8') as fp:
        fp.write('1\n00:00:00,000 --> 00:00:01,000\nHello\n')
    subprocess.run(
        [FFMPEG, '-v', 'error', '-y', '-i', source, '-i', srt,
         '-map', '0', '-map', '1', '-c', 'copy', '-c:s', 'srt', source + '.mkv'],
        check=True)
    os.replace(source + '.mkv', source + '.mp4')
    path = source + '.mp4'

    before = ffmeta.read_media(path, FFMPEG, FFPROBE)
    check('the sample really has a subtitle track',
          any(s.get('type') == 'subtitle' for s in before['streams']), before['streams'])
    outcome = ffmeta.write_media(
        path, {'title': 'Subtitle Survivor'}, ffmeta.COVER_KEEP, None, FFMPEG,
        streams=before['streams'])
    after = ffmeta.read_media(path, FFMPEG, FFPROBE)
    eq('the write succeeded by skipping the subtitle', after['tags'].get('title'),
       'Subtitle Survivor')
    check('the skipped subtitle is reported',
          any(s.get('type') == 'subtitle' for s in outcome.skipped), outcome.skipped)
    check('audio survived', any(s.get('type') == 'audio' for s in after['streams']),
          after['streams'])
    eq('no temp files left behind', leftover_temp_files(WORK), [])


def test_write_keeps_brand_and_chapters():
    """带章节的 m4b：ffmpeg 会多写一条 QuickTime 章节文字轨（bin_data / 'text'）。

    那条轨没法用 -map 0 复制，所以写回要么整次失败、要么丢掉章节；同时 major_brand
    不显式传就会从 "M4B " 退回 "M4A "（Apple Books 不再当有声书）。两个都得保住。
    """
    print('write: brand and chapters survive a chaptered m4b')
    meta = os.path.join(WORK, 'chapters.txt')
    with open(meta, 'w', encoding='utf-8', newline='\n') as fp:
        fp.write(';FFMETADATA1\n'
                 '[CHAPTER]\nTIMEBASE=1/1000\nSTART=0\nEND=1000\ntitle=One\n'
                 '[CHAPTER]\nTIMEBASE=1/1000\nSTART=1000\nEND=2000\ntitle=Two\n')
    src = os.path.join(WORK, 'chaptered.m4b')
    subprocess.run(
        [FFMPEG, '-v', 'error', '-y', '-i', sample('tagged.m4a'), '-i', meta,
         '-map', '0:a', '-map_chapters', '1', '-map_metadata', '1', '-c:a', 'copy',
         '-metadata', 'media_type=2', '-brand', 'M4B ', '-movflags', '+faststart',
         '-f', 'mp4', src],
        check=True)

    before = ffmeta.read_media(src, FFMPEG, FFPROBE)
    check('ffmpeg wrote the chapter text track',
          any(s.get('codec') == 'bin_data' for s in before['streams']), before['streams'])
    eq('major brand read from the file', before['tech'].get('brand'), 'M4B ')

    outcome = ffmeta.write_media(
        src, {'title': 'Brand Kept'}, ffmeta.COVER_KEEP, None, FFMPEG,
        streams=before['streams'], brand=before['tech'].get('brand'))
    after = ffmeta.read_media(src, FFMPEG, FFPROBE)
    eq('the title was written', after['tags'].get('title'), 'Brand Kept')
    eq('major brand preserved', after['tech'].get('brand'), 'M4B ')
    check('the chapter track was skipped and re-created',
          any(s.get('codec') == 'bin_data' for s in after['streams']), after['streams'])
    check('the skipped chapter track is reported',
          any(s.get('codec') == 'bin_data' for s in outcome.skipped), outcome.skipped)
    chapters = ffmeta.probe(src, FFPROBE).get('chapters') or []
    eq('both chapters are still there', len(chapters), 2)
    eq('chapter titles survived',
       [(c.get('tags') or {}).get('title') for c in chapters], ['One', 'Two'])
    eq('no temp files left behind', leftover_temp_files(WORK), [])


def test_write_leaves_no_temp_files():
    print('write: no temporary files left behind')
    path = scratch('tagged.mp3', 'write_clean.mp3')
    ffmeta.write_media(path, {'title': 'Clean'}, ffmeta.COVER_KEEP, None, FFMPEG)
    eq('no dotfiles left in workdir', leftover_temp_files(WORK), [])


def test_write_failure_leaves_original_intact():
    print('write: a failed write leaves the original untouched')
    path = scratch('tagged.mp3', 'write_fail.mp3')
    before = open(path, 'rb').read()
    try:
        ffmeta.write_media(path, {'title': 'Boom'}, ffmeta.COVER_SET, b'not-a-real-image',
                           FFMPEG, cover_ext='jpg')
        failed = False
    except ffutil.ToolError:
        failed = True
    check('ffmpeg rejected the bogus image', failed)
    eq('original bytes unchanged', open(path, 'rb').read(), before)
    eq('no temp files left after failure', leftover_temp_files(WORK), [])


def test_rows_to_updates():
    print('rows_to_updates reduction')
    info = read('tagged.mp3')
    rows = ffmeta.compare(lib(title='Changed', publisher=''), info)
    checked = [r for r in rows if r.checked_by_default]
    updates, cover = ffmeta.rows_to_updates(checked, ffmeta.collect_cover(checked))
    eq('only differing fields become updates', updates, {'title': 'Changed'})
    eq('cover decision is keep', cover, ffmeta.COVER_KEEP)

    all_rows = [r for r in rows if r.writable and r.status != ffmeta.STATUS_INFO]
    updates2, cover2 = ffmeta.rows_to_updates(all_rows, ffmeta.collect_cover(all_rows))
    check('empty publisher becomes a delete', updates2.get('publisher') == '', updates2)
    eq('cover removal requested', cover2, ffmeta.COVER_REMOVE)


def test_build_write_command():
    print('write command construction')
    opts = ffmeta.WriteOptions()
    argv, setting = ffmeta.build_write_command(
        '/in.mp3', '/out.mp3', {'title': 'X'}, ffmeta.COVER_KEEP, None, None,
        'ffmpeg', 'mp3', opts, existing_cover_index=1)
    eq('no cover input when keeping', setting, False)
    check('-map_metadata 0 present', '-map_metadata' in argv)
    check('stream copy, no re-encode', '-c' in argv and argv[argv.index('-c') + 1] == 'copy')
    check('id3v2 version pinned for mp3',
          argv[argv.index('-id3v2_version') + 1] == '3')
    check('no faststart for mp3', '-movflags' not in argv)

    argv4, setting4 = ffmeta.build_write_command(
        '/in.m4b', '/out.m4b', {}, ffmeta.COVER_SET, b'x', '/tmp/c.jpg',
        'ffmpeg', 'm4b', opts, existing_cover_index=1)
    eq('cover input added for m4b', setting4, True)
    check('faststart set for mp4 family', argv4[argv4.index('-movflags') + 1] == '+faststart')
    check('old cover stream excluded', '-0:1' in argv4, argv4)
    check('new cover mapped', '1:v' in argv4)
    check('attached_pic disposition set', 'attached_pic' in argv4)
    eq('output is last argument', argv4[-1], '/out.m4b')

    argv5, _ = ffmeta.build_write_command(
        '/in.mp3', '/out.mp3', {}, ffmeta.COVER_SET, b'x', '/tmp/c.jpg',
        'ffmpeg', 'mp3', ffmeta.WriteOptions(write_cover=False), existing_cover_index=1)
    check('write_cover=False suppresses cover', '1:v' not in argv5)

    argv6, setting6 = ffmeta.build_write_command(
        '/in.mp3', '/out.mp3', {}, ffmeta.COVER_REMOVE, None, None,
        'ffmpeg', 'mp3', opts, existing_cover_index=1)
    eq('remove requests no new cover', setting6, False)
    check('remove excludes the old cover stream', '-0:1' in argv6, argv6)
    check('remove maps no new video input', '1:v' not in argv6)

    argv7, _ = ffmeta.build_write_command(
        '/in.mp3', '/out.mp3', {}, ffmeta.COVER_KEEP, None, None,
        'ffmpeg', 'mp3', opts, existing_cover_index=1)
    check('keep leaves the old cover stream mapped', '-0:1' not in argv7, argv7)

    argv8, _ = ffmeta.build_write_command(
        '/in.m4b', '/out.m4b', {}, ffmeta.COVER_KEEP, None, None,
        'ffmpeg', 'm4b', opts, brand='M4B ')
    eq('major brand is preserved for m4b', argv8[argv8.index('-brand') + 1], 'M4B ')
    check('no brand flag for mp3',
          '-brand' not in ffmeta.build_write_command(
              '/in.mp3', '/out.mp3', {}, ffmeta.COVER_KEEP, None, None,
              'ffmpeg', 'mp3', opts, brand='M4B ')[0])


def test_write_command_stream_selection():
    print('write command: skipping tracks the container cannot hold')
    opts = ffmeta.WriteOptions()
    argv, _ = ffmeta.build_write_command(
        '/in.m4b', '/out.m4b', {}, ffmeta.COVER_KEEP, None, None,
        'ffmpeg', 'm4b', opts, skip_streams=(2,))
    check('a stream can be excluded, e.g. an unknown text track', '-0:2' in argv, argv)
    check('the rest is still mapped wholesale', '-map' in argv and '0' in argv, argv)

    argv2, _ = ffmeta.build_write_command(
        '/in.m4b', '/out.m4b', {}, ffmeta.COVER_KEEP, None, None,
        'ffmpeg', 'm4b', opts, only_audio_video=True)
    check('fallback maps audio streams', '0:a?' in argv2, argv2)
    check('fallback maps video streams (cover art)', '0:v?' in argv2, argv2)
    check('fallback drops the wholesale map', '-0:2' not in argv2, argv2)

    argv3, _ = ffmeta.build_write_command(
        '/in.m4b', '/out.m4b', {}, ffmeta.COVER_SET, b'x', '/tmp/c.jpg',
        'ffmpeg', 'm4b', opts, existing_cover_index=1, only_audio_video=True)
    check('the fallback still excludes the old cover', '-0:1' in argv3, argv3)
    check('the fallback still maps the new cover', '1:v' in argv3, argv3)

    eq('ffprobe stream summaries spot unknown codecs',
       ffmeta.uncopyable_streams([
           {'index': 0, 'type': 'audio', 'codec': 'aac'},
           {'index': 1, 'type': 'data', 'codec': 'unknown'},
           {'index': 2, 'type': 'subtitle', 'codec': ''},
       ]), [1, 2])
    eq('known codecs are never pre-excluded',
       ffmeta.uncopyable_streams([{'index': 0, 'type': 'subtitle', 'codec': 'subrip'}]), [])


def test_tags_keywords_mapping():
    print('tags: only keywords is read and written')
    info = read('tagged.mp3')  # 样本同时写了 keywords 和 genre
    eq('tags come from keywords', ffmeta.media_tags(info['tags']), ['Audiobook', 'Fiction'])
    rows = rows_by_field(ffmeta.compare(lib(tags=['Nonfiction']), info))
    eq('tags row is different', rows['tags'].status, ffmeta.STATUS_DIFFERENT)
    eq('tags row uses the keywords key', rows['tags'].tag_key, 'keywords')
    updates, _cover = ffmeta.rows_to_updates([rows['tags']], None)
    eq('the update targets keywords', updates, {'keywords': 'Nonfiction'})

    # genre 不是标签：即使文件里只有 genre，也不读、也不写。
    eq('genre alone is not read as tags',
       ffmeta.media_tags({'genre': 'Audiobook; Fiction'}), [])
    eq('grouping alone is not read as tags', ffmeta.media_tags({'grouping': 'G'}), [])
    eq('no tags at all', ffmeta.media_tags({}), [])
    genre_only = {'tags': {'genre': 'Legacy Genre', 'title': 'T'}, 'ext': 'mp3',
                  'cover': None, 'cover_ext': None, 'cover_stream_index': None,
                  'tech': {'duration': 2.0}}
    rows_g = rows_by_field(ffmeta.compare(lib(tags=['Audiobook']), genre_only))
    eq('genre-only file reports tags missing', rows_g['tags'].status,
       ffmeta.STATUS_MISSING_IN_FILE)
    updates_g, _c = ffmeta.rows_to_updates([rows_g['tags']], None)
    eq('genre-only file still writes keywords', updates_g, {'keywords': 'Audiobook'})

    # 写进 keywords 之后：genre 原样保留，标签仍然是 keywords 里的值。
    path = scratch('tagged.mp3', 'write_keywords.mp3')
    ffmeta.write_media(path, {'keywords': 'Nonfiction; Essays'}, ffmeta.COVER_KEEP, None, FFMPEG)
    after = ffmeta.read_media(path, FFMPEG, FFPROBE)
    eq('keywords written', after['tags'].get('keywords'), 'Nonfiction; Essays')
    eq('genre left untouched', after['tags'].get('genre'), 'Legacy Genre')
    eq('tags read back from keywords', ffmeta.media_tags(after['tags']),
       ['Nonfiction', 'Essays'])

    # 已经用 keywords 的文件，第二次对比就是一致的，不会来回改。
    rows2 = rows_by_field(ffmeta.compare(lib(tags=['Nonfiction', 'Essays']), after))
    eq('written tags compare equal', rows2['tags'].status, ffmeta.STATUS_SAME)
    eq('nothing to write for tags', rows2['tags'].write_value, None)


def main():
    global WORK
    if not FFMPEG or not FFPROBE:
        raise SystemExit('ffmpeg/ffprobe not found; install them or set FFMPEG/FFPROBE')
    print('using %s and %s\n' % (FFMPEG, FFPROBE))
    WORK = tempfile.mkdtemp(prefix='ffmeta-test-')
    try:
        for func in (test_normalisers, test_read_tagged_audio, test_read_video_is_not_cover,
                     test_read_untagged, test_compare_identical, test_compare_different,
                     test_compare_missing_and_empty, test_compare_date_precision,
                     test_compare_rating_unsupported, test_tags_case_preserved,
                     test_authors_case_preserved, test_tags_keywords_mapping,
                     test_info_rows_are_read_only, test_duration_row_with_library_column,
                     test_cover_row_states, test_rows_to_updates,
                     test_build_write_command, test_write_command_stream_selection,
                     test_write_tags_and_preserve_others, test_write_deletes_tag_with_empty_value,
                     test_write_replaces_cover_without_duplicating, test_write_removes_cover,
                     test_write_adds_cover_where_absent, test_write_mp4_family_and_mkv,
                     test_write_drops_unmappable_tracks, test_write_keeps_brand_and_chapters,
                     test_write_leaves_no_temp_files, test_write_failure_leaves_original_intact):
            func()
    finally:
        shutil.rmtree(WORK, ignore_errors=True)
    print()
    if FAILURES:
        print('%d FAILED: %s' % (len(FAILURES), ', '.join(FAILURES)))
        return 1
    print('all ffmeta tests passed')
    return 0


if __name__ == '__main__':
    sys.exit(main())
