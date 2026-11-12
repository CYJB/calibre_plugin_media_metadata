import os
import traceback

from qt.core import QApplication, QProgressDialog, Qt, QTimer

from calibre.gui2 import error_dialog, gprefs, info_dialog
from calibre.gui2.actions import InterfaceAction
from calibre.utils.date import is_date_undefined
from calibre.utils.localization import _, ngettext

try:
    from . import ffmeta, ffutil
    from .binaries import locate_binaries, tool_error_text
    from .metadata import duration_column
    from .settings import configured_columns, prefs
except ImportError:
    from calibre_plugins.MediaMetadataSync import ffmeta, ffutil
    from calibre_plugins.MediaMetadataSync.binaries import locate_binaries, tool_error_text
    from calibre_plugins.MediaMetadataSync.metadata import duration_column
    from calibre_plugins.MediaMetadataSync.settings import configured_columns, prefs

# calibre 给 zip 里每个模块都注入 load_translations()
# （customize/zipplugin.py:239-247），必须在下面模块级 _() 之前执行；
# 它同时替换 `_` 和 `ngettext`。
try:
    load_translations()
except NameError:
    pass

ACTION_NAME = 'Media Metadata Sync'
CONTEXT_MENU_PREF = 'action-layout-context-menu'


def describe_error(err):
    """把异常变成对话框里的中文描述。

    ffutil.ToolError 的消息是英文技术描述，交给 binaries.tool_error_text() 重新组织；
    其它异常（例如 os.replace 抛的 OSError）保留系统原文，至少能看出发生了什么。
    """
    if isinstance(err, ffutil.ToolError):
        return tool_error_text(err)
    return str(err)


def updated_layout(layout, action_name):
    """返回把 action_name 追加进去的右键菜单布局。

    action 已经在列表里时原样返回，所以每次启动都调也是幂等的。`None` 是 calibre 的
    分隔符。
    """
    items = tuple(layout or ())
    if action_name in items:
        return items
    return items + (None, action_name)


class SyncAction(InterfaceAction):
    name = ACTION_NAME
    action_spec = (
        _('Compare and write media metadata…'), None,
        _('Compare the calibre metadata with the tags inside audio/video files, '
          'then write the fields you tick back into the files'),
        (),
    )
    action_type = 'current'
    action_add_menu = False

    def genesis(self):
        self.qaction.triggered.connect(self.run_sync)
        self.job_data = None
        self.pd_timer = QTimer()
        self.pd_timer.timeout.connect(self.write_one)
        self.register_in_context_menu()

    def location_selected(self, loc):
        self.qaction.setEnabled(loc == 'library')

    # -------------------------------------------------------- 右键菜单

    def register_in_context_menu(self):
        """把这个 action 加进保存下来的书籍列表右键菜单布局。

        calibre 在启动时用这个 pref 构建那个菜单（gui2/init.py:108），所以菜单项下次启动
        才出现，用户可以在 首选项 → 工具栏 →「calibre 书库中书籍的右键菜单」里调整或移除。
        刻意不在运行时往活菜单里插：populate_menu() 会把 action 包成 CloneAction，还可能
        克隆 `shortcut_action_for_context_menu` 而不是 `qaction`
        （gui2/bars.py:434-437），判重很脆，曾经产生过重复菜单项。
        """
        if not prefs['register_context_menu']:
            return
        try:
            current = tuple(gprefs[CONTEXT_MENU_PREF] or ())
            updated = updated_layout(current, ACTION_NAME)
            if updated != current:
                gprefs[CONTEXT_MENU_PREF] = updated
        except Exception:
            traceback.print_exc()

    # ------------------------------------------------------------ 收集目标

    def collect_targets(self, book_ids):
        db = self.gui.current_db.new_api
        targets = []
        for book_id in book_ids:
            try:
                fmts = db.formats(book_id) or ()
            except Exception:
                continue
            for fmt in fmts:
                fmt = str(fmt).strip()
                if fmt.lower() not in ffutil.MEDIA_EXTENSIONS:
                    continue
                try:
                    path = db.format_abspath(book_id, fmt)
                except Exception:
                    path = None
                if path and os.path.isfile(path):
                    targets.append((book_id, fmt.upper(), path))
        return targets

    def lib_values(self, book_id):
        """把 calibre 的 Metadata 对象降维成 ffmeta 用来对比的纯 Python 值。"""
        db = self.gui.current_db.new_api
        mi = db.get_metadata(book_id, get_cover=True, cover_as_data=True)
        pubdate = mi.pubdate
        if pubdate is not None and is_date_undefined(pubdate):
            pubdate = None
        values = {
            'title': mi.title or '',
            'authors': list(mi.authors or ()),
            'series': mi.series or '',
            # 没有系列时索引只是 calibre 默认的 1.0，否则会被当成文件里缺一个值得写的 “track” 标签。
            'series_index': mi.series_index if mi.series else None,
            'tags': list(mi.tags or ()),
            'comments': mi.comments or '',
            'publisher': mi.publisher or '',
            'pubdate': pubdate,
            'identifiers': dict(mi.identifiers or {}),
            'cover': mi.cover_data[1] if mi.cover_data else None,
        }
        for lookup, _label, _tag in configured_columns():
            values[lookup] = mi.get(lookup)
        # 时长列用稳定的键 'duration' 放进 lib：对比表的时长行、反向同步都用它，
        # 这样 ffmeta 不需要知道用户把列命名成了 #duration 还是 #length。
        # 列不存在时不要塞进去，否则会算出一个永远写不进去的差异。
        duration = duration_column()
        if duration and duration in db.field_metadata:
            try:
                values['duration'] = mi.get(duration)
            except Exception:
                pass
        return values

    def read_entries(self, targets, ffmpeg, ffprobe):
        columns = configured_columns()
        entries, errors = [], []
        # 一本有声书可能对应几十个文件；书库元数据（含封面）按书读一次，而不是按文件读。
        lib_cache = {}
        pd = QProgressDialog(_('Reading media metadata…'), _('Cancel'), 0, len(targets), self.gui)
        pd.setWindowModality(Qt.WindowModality.WindowModal)
        try:
            for index, (book_id, fmt, path) in enumerate(targets):
                if pd.wasCanceled():
                    break
                pd.setValue(index)
                pd.setLabelText('%s/%s — %s' % (index + 1, len(targets), os.path.basename(path)))
                QApplication.processEvents()
                try:
                    media = ffmeta.read_media(path, ffmpeg, ffprobe)
                    if book_id not in lib_cache:
                        lib_cache[book_id] = self.lib_values(book_id)
                    lib = lib_cache[book_id]
                    entries.append({
                        'book_id': book_id,
                        'fmt': fmt,
                        'path': path,
                        'title': lib['title'] or os.path.basename(path),
                        'media': media,
                        'lib': lib,
                        'rows': ffmeta.compare(lib, media, custom_columns=columns),
                    })
                except Exception as err:
                    errors.append('%s：%s' % (os.path.basename(path), describe_error(err)))
        finally:
            pd.setValue(len(targets))
            pd.close()
            pd.deleteLater()
        return entries, errors

    # --------------------------------------------------------------- 入口

    def run_sync(self):
        book_ids = self.gui.library_view.get_selected_ids()
        if not book_ids:
            error_dialog(
                self.gui, _('No books selected'),
                _('Select one or more books first.'), show=True)
            return

        ffmpeg, ffprobe = locate_binaries()
        if not ffmpeg or not ffprobe:
            error_dialog(
                self.gui, _('ffmpeg not found'),
                _('This action needs both ffmpeg and ffprobe.\n\n'
                  'Install them, or set their paths in Preferences → Plugins → '
                  '“Sync media metadata” → Customize.'),
                show=True)
            return

        targets = self.collect_targets(book_ids)
        if not targets:
            error_dialog(
                self.gui, _('No media files'),
                _('None of the selected books have an audio or video format.\n\n'
                  'Supported: %s') % ', '.join(sorted(ffutil.MEDIA_EXTENSIONS)),
                show=True)
            return

        entries, read_errors = self.read_entries(targets, ffmpeg, ffprobe)
        if not entries:
            error_dialog(
                self.gui, _('Could not read media metadata'),
                '\n'.join(read_errors) or _('Unknown error'), show=True)
            return

        from .dialog import CompareDialog
        dialog = CompareDialog(self.gui, entries, db=self.gui.current_db.new_api)
        if not dialog.exec():
            if dialog.library_updated:
                # 用户已经在对话框里用文件更新了书库，就不再往文件里写。
                return
            if read_errors:
                self.report(_('Some files could not be read'), read_errors)
            return

        jobs = dialog.plan()
        if not jobs:
            info_dialog(
                self.gui, _('Nothing to write'),
                _('No fields were ticked, so no files were changed.'), show=True)
            return
        self.start_writing(jobs, ffmpeg, read_errors)

    # --------------------------------------------------------------- 写回

    def start_writing(self, jobs, ffmpeg, read_errors):
        opts = ffmeta.WriteOptions(
            id3v2_version=int(prefs['id3v2_version'] or 3),
            write_cover=bool(prefs['write_cover']))
        pd = QProgressDialog(_('Writing media metadata…'), _('Cancel'), 0, len(jobs), self.gui)
        pd.setWindowModality(Qt.WindowModality.WindowModal)
        self.job_data = {'index': 0, 'jobs': jobs, 'results': [], 'errors': list(read_errors),
                         'skipped': [], 'pd': pd, 'ffmpeg': ffmpeg, 'opts': opts}
        # 每次定时器只处理一个文件，进度条能重绘、文件之间可取消，而不是整批卡住界面。
        self.pd_timer.start(0)

    def write_one(self):
        data = self.job_data
        if data is None:
            return
        pd = data['pd']
        if pd.wasCanceled():
            self.finish_writing(cancelled=True)
            return
        if data['index'] >= len(data['jobs']):
            self.finish_writing()
            return

        job = data['jobs'][data['index']]
        pd.setValue(data['index'])
        pd.setLabelText('%s/%s — %s [%s]' % (
            data['index'] + 1, len(data['jobs']), job['title'], job['fmt']))
        # 这里不调 processEvents()：0 间隔的定时器会因此重入。每次 tick 返回事件循环
        # 已经够让对话框重绘并保持可取消。

        try:
            outcome = ffmeta.write_media(
                job['path'], job['updates'], job['cover_action'], job['cover_data'],
                data['ffmpeg'], opts=data['opts'], cover_ext=job['cover_ext'],
                existing_cover_index=job['existing_cover_index'],
                streams=job.get('streams') or (),
                brand=job.get('brand'))
            # 有些文件里的轨道目标容器放不下（认不出编码的 text 流、mp4 里的 subrip 字幕），
            # ffmeta 会跳过它们重试一次；这里记下来，最后告诉用户跳过了什么。
            if getattr(outcome, 'skipped', None):
                data['skipped'].append((job['title'], job['fmt'], outcome.skipped))
            data['results'].append(job)
        except Exception as err:
            data['errors'].append('%s [%s]：%s' % (job['title'], job['fmt'], describe_error(err)))
        data['index'] += 1

    def finish_writing(self, cancelled=False):
        data = self.job_data
        if data is None:
            return
        self.job_data = None
        self.pd_timer.stop()
        pd = data['pd']
        pd.setValue(pd.maximum())
        pd.close()
        pd.deleteLater()

        written = data['results']
        errors = data['errors']
        if written:
            self.refresh_sizes(written)

        if cancelled:
            summary = ngettext(
                'Cancelled after {} file.', 'Cancelled after {} files.', len(written)
            ).format(len(written))
        else:
            summary = ngettext(
                'Wrote metadata into {} file.', 'Wrote metadata into {} files.', len(written)
            ).format(len(written))
        skipped = data.get('skipped') or []
        details = ''
        if skipped:
            summary += '\n' + ngettext(
                '{} file had tracks that the new file cannot hold; they were skipped.',
                '{} files had tracks that the new file cannot hold; they were skipped.',
                len(skipped)).format(len(skipped))
            details = self.skipped_details(skipped)
        if errors:
            self.report(summary, errors, details)
        elif written:
            info_dialog(self.gui, _('Media metadata'), summary,
                        det_msg=details, show=True)

    @staticmethod
    def skipped_details(skipped):
        """列出被跳过的轨道，放在对话框的「详细信息」里。"""
        lines = [_('Skipped tracks:')]
        for title, fmt, dropped in skipped[:20]:
            names = sorted({('%s/%s' % (d.get('type') or '?', d.get('codec') or '?'))
                            for d in dropped})
            lines.append('%s [%s]：%s' % (title, fmt, ', '.join(names)))
        return '\n'.join(lines)

    def refresh_sizes(self, written):
        """重写过的文件大小变了；告诉 calibre，免得它记录的尺寸和书库列表过期。"""
        db = self.gui.current_db.new_api
        size_map = {}
        for job in written:
            try:
                size_map[(job['book_id'], job['fmt'])] = os.path.getsize(job['path'])
            except OSError:
                continue
        if not size_map:
            return
        try:
            changed = db.refresh_format_sizes(size_map)
        except Exception:
            return
        if changed:
            self.gui.library_view.model().refresh_ids(changed)

    def report(self, summary, errors, details=''):
        message = summary + '\n\n' + _('Problems:') + '\n' + '\n'.join(errors[:50])
        if details:
            message += '\n\n' + details
        error_dialog(self.gui, _('Media metadata'), message, show=True)
