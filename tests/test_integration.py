#!/usr/bin/env python3
# sync 路径的集成测试，走真实的插件代码。
# 用 calibre 自己的解释器跑，这样 calibre_plugins.* 能解析：
#
#     calibre-debug tests/test_integration.py /tmp/media-lib-test
#
# 需要的书库由 tests/setup_library.py 建（书里的 MP3 带原始样本标签）。

import os
import sys

LIB = sys.argv[1] if len(sys.argv) > 1 else '/tmp/media-lib-test'

from calibre.db.legacy import LibraryDatabase   # noqa: E402

from calibre_plugins.MediaMetadataSync import ffmeta, ffutil   # noqa: E402
from calibre_plugins.MediaMetadataSync.action import SyncAction  # noqa: E402
from calibre_plugins.MediaMetadataSync.binaries import locate_binaries  # noqa: E402
from calibre_plugins.MediaMetadataSync.settings import configured_columns  # noqa: E402

FAILURES = []


def check(name, condition, detail=''):
    if condition:
        print('  PASS  %s' % name)
    else:
        print('  FAIL  %s %s' % (name, detail))
        FAILURES.append(name)


def eq(name, actual, expected):
    check(name, actual == expected, '(got %r, want %r)' % (actual, expected))


class Stub:
    """代替 SyncAction 实例，这样不构造 QObject 或主窗口也能调真实方法。"""


def make_stub(db):
    class FakeNewApiHolder:
        new_api = db

    class FakeGui:
        current_db = FakeNewApiHolder()

    stub = Stub()
    stub.gui = FakeGui()
    return stub


def main():
    ffmpeg, ffprobe = locate_binaries()
    check('ffmpeg located', bool(ffmpeg), ffmpeg)
    check('ffprobe located', bool(ffprobe), ffprobe)
    if not (ffmpeg and ffprobe):
        return 1
    print('  using %s / %s' % (ffmpeg, ffprobe))

    library = LibraryDatabase(LIB)
    db = library.new_api
    stub = make_stub(db)
    book_id = min(db.all_book_ids())
    print('  testing against book id %s in %s' % (book_id, LIB))

    # --- 用户在 calibre 里改元数据 -------------------------------
    print('\nlibrary edit -> file write-back')
    db.set_field('title', {book_id: 'Renamed Inside Calibre'})
    db.set_field('authors', {book_id: ['Someone Else']})
    db.set_field('tags', {book_id: ['Nonfiction', 'Retagged']})
    db.set_field('publisher', {book_id: ''})          # cleared on purpose
    db.set_field('series', {book_id: 'A New Series'})
    db.set_field('series_index', {book_id: 9.0})

    targets = SyncAction.collect_targets(stub, [book_id])
    check('found a media target', len(targets) >= 1, targets)
    if not targets:
        return 1
    bid, fmt, path = targets[0]
    eq('format is MP3', fmt, 'MP3')
    check('file exists', os.path.isfile(path), path)

    before = ffmeta.read_media(path, ffmpeg, ffprobe)
    eq('file still has the old title', before['tags'].get('title'), 'The Original Title')

    lib = SyncAction.lib_values(stub, book_id)
    eq('lib title read from db', lib['title'], 'Renamed Inside Calibre')
    eq('lib authors read from db', lib['authors'], ['Someone Else'])
    eq('series_index only reported when a series exists', lib['series_index'], 9.0)

    rows = ffmeta.compare(lib, before, custom_columns=configured_columns())
    by_field = {r.field: r for r in rows}
    eq('title differs', by_field['title'].status, ffmeta.STATUS_DIFFERENT)
    eq('authors differ', by_field['authors'].status, ffmeta.STATUS_DIFFERENT)
    eq('tags differ', by_field['tags'].status, ffmeta.STATUS_DIFFERENT)
    eq('series differs', by_field['series'].status, ffmeta.STATUS_DIFFERENT)
    eq('series_index differs', by_field['series_index'].status, ffmeta.STATUS_DIFFERENT)
    eq('cleared publisher is empty_in_lib', by_field['publisher'].status,
       ffmeta.STATUS_EMPTY_IN_LIB)
    check('cleared publisher NOT ticked by default',
          by_field['publisher'].checked_by_default is False)
    check('title ticked by default', by_field['title'].checked_by_default is True)

    checked = [r for r in rows if r.checked_by_default]
    updates, cover_action = ffmeta.rows_to_updates(checked, ffmeta.collect_cover(checked))
    check('publisher excluded from updates', 'publisher' not in updates, updates)
    eq('title in updates', updates.get('title'), 'Renamed Inside Calibre')
    eq('artist in updates', updates.get('artist'), 'Someone Else')
    eq('album in updates', updates.get('album'), 'A New Series')
    eq('track in updates', updates.get('track'), '9')
    eq('cover left alone', cover_action, ffmeta.COVER_KEEP)

    size_before = os.path.getsize(path)
    ffmeta.write_media(path, updates, cover_action, None, ffmpeg,
                       existing_cover_index=before['cover_stream_index'])

    after = ffmeta.read_media(path, ffmpeg, ffprobe)
    eq('file title updated', after['tags'].get('title'), 'Renamed Inside Calibre')
    eq('file artist updated', after['tags'].get('artist'), 'Someone Else')
    eq('file album updated', after['tags'].get('album'), 'A New Series')
    eq('file track updated', after['tags'].get('track'), '9')
    eq('unticked publisher preserved', after['tags'].get('publisher'), 'Original Publisher')
    eq('untouched comment preserved', after['tags'].get('comment'),
       'Original description text.')
    eq('cover preserved when unticked',
       ffmeta.cover_hash(after['cover']), ffmeta.cover_hash(before['cover']))
    check('audio stream intact', after['tech']['audio_codec'] == 'mp3',
          after['tech']['audio_codec'])

    # --- calibre 记录的文件大小必须刷新 --------------------------
    print('\nstale size refresh')
    size_after = os.path.getsize(path)
    stored = db.format_db_size(book_id, 'MP3')
    print('  on-disk %d, stored %s' % (size_after, stored))
    changed = db.refresh_format_sizes({(book_id, 'MP3'): size_after})
    eq('refresh_format_sizes reports the book', changed, {book_id})
    eq('stored size now matches disk', db.format_db_size(book_id, 'MP3'), size_after)
    check('file size actually changed', size_after != size_before,
          '%d -> %d' % (size_before, size_after))

    # --- 第二次运行必须报告所有字段已一致 ------------------------
    print('\nidempotence')
    lib2 = SyncAction.lib_values(stub, book_id)
    media2 = ffmeta.read_media(path, ffmpeg, ffprobe)
    rows2 = ffmeta.compare(lib2, media2, custom_columns=configured_columns())
    by_field2 = {r.field: r for r in rows2}
    differing = [r.field for r in rows2 if r.status in ffmeta.DEFAULT_CHECKED]
    eq('a second run proposes no automatic writes', differing, [])
    eq('written fields now agree', by_field2['title'].status, ffmeta.STATUS_SAME)
    eq('written authors now agree', by_field2['authors'].status, ffmeta.STATUS_SAME)
    eq('written tags now agree', by_field2['tags'].status, ffmeta.STATUS_SAME)
    # publisher 在书库里被清空、但在文件里刻意保留，所以它必须仍然被报告——报成
    # empty_in_lib，这个状态永远不会被自动勾选。
    eq('cleared publisher still reported', by_field2['publisher'].status,
       ffmeta.STATUS_EMPTY_IN_LIB)
    check('cleared publisher still not auto-ticked',
          by_field2['publisher'].checked_by_default is False)

    # --- 非媒体格式被忽略，不是报错 ------------------------------
    print('\nformat filtering')
    eq('no targets for a book without media', SyncAction.collect_targets(stub, []), [])
    check('epub is not a media extension', 'epub' not in ffutil.MEDIA_EXTENSIONS)

    library.close()
    print()
    if FAILURES:
        print('%d FAILED: %s' % (len(FAILURES), ', '.join(FAILURES)))
        return 1
    print('all integration tests passed')
    return 0


if __name__ == '__main__':
    sys.exit(main())
