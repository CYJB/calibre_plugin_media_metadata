#!/usr/bin/env python3
# 在 offscreen Qt 平台下驱动真实的 CompareDialog 组件，这样表格填充、默认勾选、
# 选择按钮和 plan() 都能被跑到，不需要可见窗口。
#
#     QT_QPA_PLATFORM=offscreen calibre-debug tests/test_dialog.py /tmp/media-lib-test

import os
import sys

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

LIB = sys.argv[1] if len(sys.argv) > 1 else '/tmp/media-lib-test'

from qt.core import QApplication, QColor, QDialogButtonBox, Qt      # noqa: E402

app = QApplication.instance() or QApplication([])  # noqa: F841

from calibre.db.legacy import LibraryDatabase      # noqa: E402

from calibre_plugins.MediaMetadataSync import ffmeta           # noqa: E402
from calibre_plugins.MediaMetadataSync.action import SyncAction  # noqa: E402
from calibre_plugins.MediaMetadataSync import dialog as dialog_mod  # noqa: E402
from calibre_plugins.MediaMetadataSync.dialog import CompareDialog  # noqa: E402
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
    pass


def build_entries(db):
    stub = Stub()

    class Holder:
        new_api = db

    class FakeGui:
        current_db = Holder()

    stub.gui = FakeGui()
    ffmpeg, ffprobe = locate_binaries()
    entries = []
    for book_id in sorted(db.all_book_ids()):
        for bid, fmt, path in SyncAction.collect_targets(stub, [book_id]):
            lib = SyncAction.lib_values(stub, bid)
            media = ffmeta.read_media(path, ffmpeg, ffprobe)
            entries.append({
                'book_id': bid, 'fmt': fmt, 'path': path,
                'title': lib['title'] or os.path.basename(path),
                'media': media, 'lib': lib,
                'rows': ffmeta.compare(lib, media, custom_columns=configured_columns()),
            })
    return entries


def checked_fields(dialog, index):
    return {f for f, on in dialog.checked[index].items() if on}


def test_context_menu_layout():
    """这个 action 是靠改保存的菜单布局注册的，calibre 启动时用 populate_menu() 按它构建
    菜单。运行时没有插入动作，所以唯一要测的逻辑就是布局更新。
    """
    from calibre_plugins.MediaMetadataSync.action import ACTION_NAME, SyncAction, updated_layout

    print('\nright-click menu layout registration')
    default = ('Edit Metadata', None, 'Remove Books')
    once = updated_layout(default, ACTION_NAME)
    eq('appends a separator and the action', once,
       ('Edit Metadata', None, 'Remove Books', None, ACTION_NAME))
    eq('running again changes nothing', updated_layout(once, ACTION_NAME), once)
    eq('idempotent over many runs',
       updated_layout(updated_layout(updated_layout(default, ACTION_NAME), ACTION_NAME), ACTION_NAME),
       once)
    eq('handles an empty layout', updated_layout((), ACTION_NAME), (None, ACTION_NAME))
    eq('handles a missing pref', updated_layout(None, ACTION_NAME), (None, ACTION_NAME))
    check('returns a tuple, as gprefs expects', isinstance(once, tuple))
    eq('does not disturb other actions', once[:3], default)

    print('\naction enable/disable per location')
    from qt.core import QAction

    stub = Stub()
    stub.qaction = QAction('Compare and write media metadata…')
    SyncAction.location_selected(stub, 'card_a')
    check('disabled on device views', stub.qaction.isEnabled() is False)
    SyncAction.location_selected(stub, 'library')
    check('enabled in the library', stub.qaction.isEnabled() is True)

    check('no runtime menu insertion is left behind',
          not hasattr(SyncAction, 'add_to_context_menu'))


def main():
    library = LibraryDatabase(LIB)
    db = library.new_api
    book_id = min(db.all_book_ids())

    # 让书库和文件不一致，面板上才有东西可看。
    db.set_field('title', {book_id: 'Dialog Test Title'})
    db.set_field('authors', {book_id: ['Dialog Author']})
    db.set_field('publisher', {book_id: ''})
    # 标签也弄脏，这样「用文件更新书库」才有东西可恢复到文件里的值。
    db.set_field('tags', {book_id: ['Stale', 'Tags']})
    # 时长列故意写错：反向同步必须把它改回文件里的 2.0（这里的条目快照因此是 5.0）。
    db.set_field('#duration', {book_id: 5.0})

    # 给第一本书加第二个媒体格式，用上分组逻辑。
    sample_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'out')
    second_format = os.path.join(sample_dir, 'tagged.m4a')
    if os.path.isfile(second_format):
        db.add_format(book_id, 'M4A', second_format)

    entries = build_entries(db)
    check('built at least one entry', bool(entries), entries)
    if not entries:
        return 1

    dialog = CompareDialog(None, entries, db=db)
    entry = entries[0]
    by_field = {r.field: r for r in entry['rows']}
    field_order = [r.field for r in entry['rows']]

    print('\ntable population')
    eq('table has one row per field', dialog.table.rowCount(), len(entry['rows']))
    eq('table has four columns', dialog.table.columnCount(), 4)
    check('right side no longer repeats the file name',
          not hasattr(dialog, 'summary'))

    print('\nleft panel grouping')
    tree = dialog.file_tree
    book_ids = []
    for candidate in entries:
        if candidate['book_id'] not in book_ids:
            book_ids.append(candidate['book_id'])
    eq('one group per book', tree.topLevelItemCount(), len(book_ids))
    group = tree.topLevelItem(0)
    eq('group header is the book title', group.text(0), entry['title'])
    check('group header is not selectable',
          not bool(group.flags() & Qt.ItemFlag.ItemIsSelectable))
    group_files = [e for e in entries if e['book_id'] == entry['book_id']]
    eq('group holds every media file of that book', group.childCount(), len(group_files))
    check('group is expanded', group.isExpanded())
    child = group.child(0)
    eq('child shows the format', child.text(0), group_files[0]['fmt'])
    check('child does not repeat the book title',
          entry['title'] not in child.text(0), child.text(0))
    check('child does not repeat the author',
          'Dialog Author' not in child.text(0), child.text(0))
    eq('child tooltip is the full path', child.toolTip(0), group_files[0]['path'])
    eq('each child in a group is distinct',
       len({group.child(i).text(0) for i in range(group.childCount())}),
       group.childCount())
    eq('a file is selected on open', dialog.current_entry_index(), 0)

    if group.childCount() > 1:
        print('\nswitching files within a book')
        other = group.child(1)
        dialog.file_tree.setCurrentItem(other)
        target = int(other.data(0, Qt.ItemDataRole.UserRole))
        eq('table follows the selection', dialog.current_entry_index(), target)
        eq('table rebuilt for that file',
           dialog.table.rowCount(), len(entries[target]['rows']))
        dialog.file_tree.setCurrentItem(child)
        eq('selection restored', dialog.current_entry_index(), 0)
    eq('first row label is rendered from the field key',
       dialog.table.item(0, 0).text(), dialog_mod.FIELD_LABELS['title'])
    check('field labels are localized, not the raw key',
          dialog.table.item(0, 0).text() != 'title', dialog.table.item(0, 0).text())
    eq('library column shows the library value',
       dialog.table.item(0, 1).text(), 'Dialog Test Title')
    eq('file column shows the tag value',
       dialog.table.item(0, 2).text(), 'The Original Title')
    check('status column is filled', bool(dialog.table.item(0, 3).text()))

    cover_index = field_order.index('cover')
    lib_cover = entry['lib'].get('cover')
    file_cover = entry['media'].get('cover')
    eq('cover row renders a localized library cell',
       dialog.table.item(cover_index, 1).text(),
       dialog_mod.cover_text(len(lib_cover) if lib_cover else None))
    eq('cover row renders a localized file cell',
       dialog.table.item(cover_index, 2).text(),
       dialog_mod.cover_text(len(file_cover) if file_cover else None))

    print('\ndefault tick state')
    fields = checked_fields(dialog, 0)
    check('differing title is ticked', 'title' in fields, sorted(fields))
    check('differing authors is ticked', 'authors' in fields)
    check('cleared publisher is NOT ticked', 'publisher' not in fields)
    check('identical series is NOT ticked', 'series' not in fields)
    # 时长行是「仅同步回书库」：书库里是 0.5、文件里是 2.0，默认勾上。
    check('library-only duration is ticked when it differs', 'duration' in fields,
          sorted(fields))
    checkable = Qt.ItemFlag.ItemIsUserCheckable
    check('writable row gets a checkbox',
          bool(dialog.table.item(field_order.index('title'), 0).flags() & checkable))
    check('library-only duration row gets a checkbox',
          bool(dialog.table.item(field_order.index('duration'), 0).flags() & checkable))
    check('read-only row gets no checkbox',
          not bool(dialog.table.item(field_order.index('container'), 0).flags() & checkable))
    check('unsupported rating row gets no checkbox',
          not bool(dialog.table.item(field_order.index('rating'), 0).flags() & checkable))
    eq('duration row shows the library value',
       dialog.table.item(field_order.index('duration'), 1).text(), '0:05')
    eq('duration row shows the file value',
       dialog.table.item(field_order.index('duration'), 2).text(), '0:02')
    eq('duration status is the library-only one',
       dialog.table.item(field_order.index('duration'), 3).text(),
       dialog_mod.STATUS_TEXT[ffmeta.STATUS_LIB_ONLY])

    print('\nstatus column colours')
    palette = dialog.status_colors

    def status_color(field):
        return dialog.table.item(field_order.index(field), 3).foreground().color().name()

    def cell_color(field, column):
        return dialog.table.item(field_order.index(field), column).foreground().color().name()

    def want(status):
        return QColor(palette[status]).name()

    eq('differing title is orange', status_color('title'), want(ffmeta.STATUS_DIFFERENT))
    eq('cleared publisher is orange too', status_color('publisher'),
       want(ffmeta.STATUS_EMPTY_IN_LIB))
    eq('identical series is green', status_color('series'), want(ffmeta.STATUS_SAME))
    eq('rating is grey', status_color('rating'), want(ffmeta.STATUS_UNSUPPORTED))
    eq('library-only duration has its own blue', status_color('duration'),
       want(ffmeta.STATUS_LIB_ONLY))
    eq('not-writable and info share one grey', status_color('rating'),
       status_color('container'))
    check('green and orange actually differ',
          want(ffmeta.STATUS_SAME) != want(ffmeta.STATUS_DIFFERENT))
    check('only the status column is coloured',
          cell_color('title', 3) != cell_color('title', 1),
          (cell_color('title', 3), cell_color('title', 1)))
    eq('the library column keeps the default colour',
       cell_color('title', 1), cell_color('series', 1))

    print('\nplan()')
    jobs = dialog.plan()
    eq('one job per differing file', len(jobs), len(entries))
    job = jobs[0]
    eq('job targets the selected file', job['path'], entry['path'])
    eq('job targets the right format', job['fmt'], entry['fmt'])
    eq('job updates title', job['updates'].get('title'), 'Dialog Test Title')
    eq('job updates artist', job['updates'].get('artist'), 'Dialog Author')
    check('job does not delete publisher', 'publisher' not in job['updates'], job['updates'])
    eq('job leaves the cover alone', job['cover_action'], ffmeta.COVER_KEEP)
    eq('job remembers the existing cover stream', job['existing_cover_index'],
       entry['media']['cover_stream_index'])
    eq('every entry produced a job',
       sorted(j['path'] for j in jobs), sorted(e['path'] for e in entries))

    print('\nselection buttons act on the current file only')
    dialog.select_none()
    eq('select none clears the current file', checked_fields(dialog, 0), set())
    check('other files keep their own ticks', bool(checked_fields(dialog, 1)))
    eq('without apply-to-all the other file still produces a job',
       len(dialog.plan()), len(entries) - 1)

    dialog.apply_all.setChecked(True)
    eq('apply-to-all propagates the empty selection', dialog.plan(), [])

    dialog.select_all()
    everything = checked_fields(dialog, 0)
    check('select all ticks publisher too', 'publisher' in everything)
    check('select all still skips read-only rows', 'container' not in everything)
    all_jobs = dialog.plan()
    eq('select all produces a job per file', len(all_jobs), len(entries))
    by_path = {e['path']: e for e in entries}
    for job in all_jobs:
        publisher_row = {r.field: r for r in by_path[job['path']]['rows']}['publisher']
        # mp4/m4a 容器根本没有 publisher 标签，书库值清空后两边都是空，也就没什么可删。
        if publisher_row.status == ffmeta.STATUS_EMPTY_IN_LIB:
            eq('select all deletes the cleared publisher in %s' % job['fmt'],
               job['updates'].get('publisher'), '')
        else:
            eq('publisher row for %s is %s, so nothing is written' % (
                job['fmt'], publisher_row.status),
               job['updates'].get('publisher'), None)

    dialog.select_differing()
    eq('select differing matches the defaults', checked_fields(dialog, 0), fields)
    dialog.apply_all.setChecked(False)

    print('\napply-to-all-files')
    dialog.select_none()
    dialog.apply_all.setChecked(True)
    dialog.checked[0]['title'] = True
    dialog.show_entry(0)
    shared_jobs = dialog.plan()
    eq('apply-to-all yields a job per file', len(shared_jobs), len(entries))
    for job in shared_jobs:
        eq('apply-to-all restricts %s to the shared selection' % job['fmt'],
           set(job['updates']), {'title'})

    print('\nupdate library from the files (文件 -> 书库)')
    # 上面的选择按钮测试改过勾选状态，这里恢复默认（不一致的勾上）再测反向同步。
    dialog.apply_all.setChecked(False)
    dialog.select_differing()
    check('the update button is enabled when the db api is available',
          dialog.update_button.isEnabled())

    buttons = dialog.bb.buttons()
    check('update-library button is not a standard button',
          dialog.update_button not in buttons, [b.text() for b in buttons])
    ok_button = dialog.bb.button(QDialogButtonBox.StandardButton.Ok)
    eq('the OK button now says what it does', ok_button.text(),
       dialog_mod._('Update file metadata'))
    check('update-library button sits left of OK in the bottom row',
          dialog.button_row.indexOf(dialog.update_button) < dialog.button_row.indexOf(dialog.bb),
          [dialog.button_row.itemAt(i).widget() for i in range(dialog.button_row.count())])

    plan = dialog.library_plan()
    eq('one library job per differing book', len(plan), 1)
    job = plan[0]
    eq('plan takes the title from the file', job['updates'].get('title'), 'The Original Title')
    eq('plan takes the authors from the file', job['updates'].get('authors'),
       ['Original Author'])
    check('plan leaves identical fields out', 'series' not in job['updates'], job['updates'])
    eq('plan includes the duration column', job['updates'].get('#duration'), 2.0)
    check('plan keeps every media file of the book', len(job['media']) >= 1, len(job['media']))

    # 勾选状态也作用于反方向：没勾的字段不会被写回书库。
    # 一本书有多个文件时，只要有一个文件勾了该字段就会被写回，所以这里逐个文件取消。
    book_entries = [i for i, e in enumerate(entries) if e['book_id'] == book_id]
    for i in book_entries:
        dialog.checked[i]['title'] = False
    plan_no_title = dialog.library_plan()
    check('unchecked title is not written back',
          'title' not in plan_no_title[0]['updates'], plan_no_title[0]['updates'])
    for i in book_entries:
        dialog.checked[i]['title'] = True
    for i in book_entries:
        dialog.checked[i]['duration'] = False
    plan_no_duration = dialog.library_plan()
    check('unchecked duration is not written back',
          '#duration' not in plan_no_duration[0]['updates'], plan_no_duration[0]['updates'])
    for i in book_entries:
        dialog.checked[i]['duration'] = True

    # 只有时长不同时，这本书也必须出现在计划里（否则会漏掉时长）。
    only_duration = dict(entries[0])
    stale_lib = dict(only_duration['lib'])
    stale_lib.update(dialog_mod.metadata.library_values(only_duration['media']))
    stale_lib['duration'] = 5.0
    only_duration['lib'] = stale_lib
    eq('duration alone still produces an update',
       dialog_mod.book_updates(only_duration, {'duration'}), {'#duration': 2.0})

    merged = db.get_metadata(book_id, get_cover=True, cover_as_data=True)
    dialog_mod.merge_file_metadata(merged, entries[0]['media'])
    eq('merge keeps untouched fields', merged.series, 'Original Series')
    eq('merge adds the duration custom column', round(merged.get('#duration'), 2), 2.0)

    # 点按钮会弹确认框；把三个对话框换成记录器，直接走完写入流程。
    # 桩函数照抄 calibre 9.17 的真实签名：question_dialog **没有** show 参数
    # （info_dialog / error_dialog 才有），传错关键字就会像线上那样抛 TypeError。
    captured = []

    def fake_question(parent, title, msg, det_msg='', show_copy_button=False,
                      default_yes=True, skip_dialog_name=None, skip_dialog_msg='',
                      skip_dialog_skipped_value=True, skip_dialog_skip_precheck=True,
                      override_icon=None, yes_text=None, no_text=None,
                      yes_icon=None, no_icon=None, add_abort_button=False):
        captured.append(('question', title, msg))
        return True

    def fake_info(parent, title, msg, det_msg='', show=False, show_copy_button=True,
                  only_copy_details=False):
        captured.append(('info', title, msg))

    def fake_error(parent, title, msg, det_msg='', show=False, show_copy_button=True):
        captured.append(('error', title, msg))

    original = (dialog_mod.info_dialog, dialog_mod.question_dialog, dialog_mod.error_dialog)
    dialog_mod.info_dialog = fake_info
    dialog_mod.question_dialog = fake_question
    dialog_mod.error_dialog = fake_error
    try:
        dialog.update_library()
    finally:
        dialog_mod.info_dialog, dialog_mod.question_dialog, dialog_mod.error_dialog = original

    check('a confirmation was asked before writing',
          any(c[0] == 'question' for c in captured), captured)
    check('the dialog reports that it updated the library', dialog.library_updated is True)
    after = db.get_metadata(book_id, get_cover=True, cover_as_data=True)
    eq('library title now comes from the file', after.title, 'The Original Title')
    eq('library authors now come from the file', list(after.authors or ()), ['Original Author'])
    eq('library tags come from the file too', sorted(after.tags or ()),
       ['Audiobook', 'Fiction'])
    eq('duration column now comes from the file',
       round(db.field_for('#duration', book_id), 2), 2.0)
    # 更新之后书库时长和文件一致了：再对比时这一行就是「一致」，不再是「仅同步回书库」。
    same_values = dict(entry['lib'])
    same_values['duration'] = 2.0
    same_rows = {r.field: r for r in ffmeta.compare(same_values, entry['media'])}
    eq('equal duration now reads as same', same_rows['duration'].status, ffmeta.STATUS_SAME)
    eq('unchecked publisher was not imported', after.publisher, None)
    eq('formats untouched', sorted(db.formats(book_id)), ['M4A', 'MP3'])

    library.close()
    test_context_menu_layout()
    print()
    if FAILURES:
        print('%d FAILED: %s' % (len(FAILURES), ', '.join(FAILURES)))
        return 1
    print('all dialog tests passed')
    return 0


if __name__ == '__main__':
    sys.exit(main())
