from qt.core import (
    QAbstractItemView, QCheckBox, QColor, QDialogButtonBox, QFont, QHBoxLayout,
    QHeaderView, QIcon, QImage, QLabel, QPalette, QPixmap, QPushButton,
    QSplitter, QTableWidget, QTableWidgetItem, QTreeWidget, QTreeWidgetItem,
    QVBoxLayout, QWidget, Qt,
)

from calibre.gui2 import error_dialog, info_dialog, question_dialog
from calibre.gui2.widgets2 import Dialog
from calibre.utils.localization import _, ngettext

try:
    from . import ffmeta, metadata
except ImportError:
    from calibre_plugins.MediaMetadataSync import ffmeta, metadata

# 见 common/binaries.py：必须在下面这些模块级 _() 之前执行。
try:
    load_translations()
except NameError:
    pass

STATUS_TEXT = {
    ffmeta.STATUS_SAME: _('Same'),
    ffmeta.STATUS_DIFFERENT: _('Different'),
    ffmeta.STATUS_MISSING_IN_FILE: _('Not in file'),
    ffmeta.STATUS_EMPTY_IN_LIB: _('Empty in library'),
    ffmeta.STATUS_UNSUPPORTED: _('Not writable'),
    ffmeta.STATUS_LIB_ONLY: _('Sync to library only'),
    ffmeta.STATUS_INFO: _('Info'),
}

# ffmeta 不 import calibre 才能无头跑单测，代价是它什么都不能翻译。
# 它只产出稳定的字段键和 tooltip 键，本地化文案都在这里。
FIELD_LABELS = {
    'title': _('Title'),
    'authors': _('Authors'),
    'series': _('Series'),
    'series_index': _('Series index'),
    'tags': _('Tags'),
    'comments': _('Comments'),
    'publisher': _('Publisher'),
    'pubdate': _('Published'),
    'identifiers': _('ISBN'),
    'rating': _('Rating'),
    'cover': _('Cover'),
    'duration': _('Duration'),
    'container': _('Container'),
    'video': _('Video stream'),
    'audio': _('Audio stream'),
    'bit_rate': _('Bit rate'),
}

TOOLTIP_TEXT = {
    'clears_date': _('Writing this clears the existing date tag.'),
    'clears_tag': _('Writing this clears the existing tag.'),
    'alias_tags': _('The file also carries %s; only %s will be written.'),
    'library_only': _('Only written back to the library: the duration comes from the media stream, so there is no tag to write into the file.'),
    'unsupported_field': _('ffmpeg has no standard tag for this field; it cannot be written to media files.'),
    'cover_unsupported': _('ffmpeg cannot store cover art in .%s containers.'),
    'cover_removes': _('Writing this removes the existing cover art.'),
    'derived_info': _('Derived from the stream; not a writable tag.'),
}


def row_label(row):
    """自定义列带用户自己起的标签，标准字段用字段键。"""
    return FIELD_LABELS.get(row.field, row.label)


def row_tooltip(row):
    if not row.tooltip_key:
        return ''
    template = TOOLTIP_TEXT.get(row.tooltip_key, row.tooltip)
    try:
        return template % row.tooltip_args
    except TypeError:
        return template


def cover_text(count):
    if not count:
        return _('No cover')
    return _('Cover, {} bytes').format(count)

EMPHASISED = frozenset({
    ffmeta.STATUS_DIFFERENT, ffmeta.STATUS_MISSING_IN_FILE, ffmeta.STATUS_EMPTY_IN_LIB,
})

# 状态列配色，明暗主题各一套。绿 = 两边一致，橙 = 不一致（包括“文件中缺失”和
# “书库中为空”，它们同样是差异），蓝 = 只能写回书库，灰 = 这一行没有什么可写。
STATUS_COLORS = {
    'light': {
        ffmeta.STATUS_SAME: '#1b7f37',
        ffmeta.STATUS_DIFFERENT: '#b25e09',
        ffmeta.STATUS_MISSING_IN_FILE: '#b25e09',
        ffmeta.STATUS_EMPTY_IN_LIB: '#b25e09',
        ffmeta.STATUS_LIB_ONLY: '#1f6fb2',
        ffmeta.STATUS_UNSUPPORTED: '#8a8a8a',
        ffmeta.STATUS_INFO: '#8a8a8a',
    },
    'dark': {
        ffmeta.STATUS_SAME: '#4ac26b',
        ffmeta.STATUS_DIFFERENT: '#e8963c',
        ffmeta.STATUS_MISSING_IN_FILE: '#e8963c',
        ffmeta.STATUS_EMPTY_IN_LIB: '#e8963c',
        ffmeta.STATUS_LIB_ONLY: '#5aa9e6',
        ffmeta.STATUS_UNSUPPORTED: '#9a9a9a',
        ffmeta.STATUS_INFO: '#9a9a9a',
    },
}


def theme_is_dark(widget):
    """刻意不用 calibre.gui2.is_dark_theme()：那个依赖 QPalette.is_dark_theme()，需要
    Qt 6.5+，而本插件声明兼容到 calibre 7.0。亮度回退让它在任何情况下都能用。
    """
    try:
        from calibre.gui2 import qapplication_or_fail
        return bool(qapplication_or_fail().is_dark_theme)
    except Exception:
        pass
    try:
        text = widget.palette().color(QPalette.ColorRole.WindowText)
        luminance = (0.299 * text.red() + 0.587 * text.green() + 0.114 * text.blue()) / 255.0
        return luminance > 0.5
    except Exception:
        return False

ICON_SIZE = 48


def icon_from_bytes(data):
    if not data:
        return QIcon()
    image = QImage.fromData(data)
    if image.isNull():
        return QIcon()
    return QIcon(QPixmap.fromImage(image).scaled(
        ICON_SIZE, ICON_SIZE,
        Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation))


class CompareDialog(Dialog):
    """并排显示书库元数据与每个媒体文件里的标签，每个字段一个勾选框。不一致的字段默认勾上。"""

    def __init__(self, gui, entries, db=None):
        # 在 Dialog.__init__ 之前设置，因为构造时会调 setup_ui()。
        self.entries = entries
        self.checked = [dict() for _ in entries]
        # 反向操作（文件 -> 书库）需要写库；测试里可以不传，那个按钮就会禁用。
        self.db = db
        self.library_updated = False
        Dialog.__init__(
            self, _('Compare and write media metadata'), 'media-metadata-compare', gui)

    def setup_ui(self):
        layout = QVBoxLayout(self)
        self.setLayout(layout)
        self.status_colors = STATUS_COLORS['dark' if theme_is_dark(self) else 'light']

        self.splitter = QSplitter(self)
        layout.addWidget(self.splitter, stretch=1)

        left = QWidget(self.splitter)
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left.setLayout(left_layout)
        left_layout.addWidget(QLabel(_('Media files:')))
        self.file_tree = QTreeWidget()
        self.file_tree.setHeaderHidden(True)
        self.file_tree.setColumnCount(1)
        self.file_tree.currentItemChanged.connect(self.tree_selection_changed)
        left_layout.addWidget(self.file_tree, stretch=1)
        self.splitter.addWidget(left)

        right = QWidget(self.splitter)
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right.setLayout(right_layout)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(
            [_('Field'), _('In calibre library'), _('In the file'), _('Status')])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self.table.itemChanged.connect(self.item_toggled)
        right_layout.addWidget(self.table, stretch=1)

        buttons = QHBoxLayout()
        right_layout.addLayout(buttons)
        for text, slot in (
            (_('Select all'), self.select_all),
            (_('Select none'), self.select_none),
            (_('Select differing'), self.select_differing),
        ):
            button = QPushButton(text)
            button.clicked.connect(slot)
            buttons.addWidget(button)
        buttons.addStretch(1)
        self.apply_all = QCheckBox(_('Apply this selection to all files'))
        buttons.addWidget(self.apply_all)

        self.splitter.setSizes([260, 700])

        # 底部一行：左边「用文件更新书库」，右边标准按钮（确定 / 取消）。
        # 反方向按钮必须真的排在「确定」左边，所以自己放进这一行的布局，
        # 不用 bb.addButton()——Qt 会把 ActionRole 排到 Ok 后面。
        self.button_row = QHBoxLayout()
        layout.addLayout(self.button_row)
        self.update_button = QPushButton(_('Update library from the files'))
        self.update_button.setToolTip(_(
            'Read the tags in these media files and write the checked fields into the '
            'calibre metadata of the same books.'))
        self.update_button.clicked.connect(self.update_library)
        self.update_button.setEnabled(self.db is not None)
        # 紧贴在「更新文件元数据」左边：两个方向并排但顺序分明，不会点到一半找不到。
        self.button_row.addStretch(1)
        self.button_row.addWidget(self.update_button)
        self.button_row.addWidget(self.bb)

        ok_button = self.bb.button(QDialogButtonBox.StandardButton.Ok)
        if ok_button is not None:
            # 「确定」实际是把右侧勾选的字段写进媒体文件，改个名字才不歧义。
            ok_button.setText(_('Update file metadata'))

        for index, entry in enumerate(self.entries):
            for row in entry['rows']:
                self.checked[index][row.field] = row.checked_by_default
        self.populate_tree()

    def populate_tree(self):
        """把媒体文件按书分组，这样一本有多个章节、或同时有音频和视频格式的书看起来是一个整体。"""
        groups = {}
        for index, entry in enumerate(self.entries):
            groups.setdefault(entry['book_id'], [entry['title'], []])[1].append(index)

        bold = QFont(self.file_tree.font())
        bold.setBold(True)
        for book_id, (title, indices) in groups.items():
            parent = QTreeWidgetItem(self.file_tree, [title])
            # 启用但不可选中：这个标题只是给分组起名，不是写入目标。
            parent.setFlags(Qt.ItemFlag.ItemIsEnabled)
            parent.setFont(0, bold)
            parent.setToolTip(0, title)
            for index in indices:
                entry = self.entries[index]
                # 只显示格式：calibre 的文件名模板本来就是“书名 - 作者.扩展名”，显示文件名会和分组
                # 标题重复。一本书每种格式最多一个文件，所以格式能唯一标识它，完整路径放在 tooltip 里。
                child = QTreeWidgetItem(parent, [entry['fmt']])
                child.setToolTip(0, entry['path'])
                child.setData(0, Qt.ItemDataRole.UserRole, index)
            parent.setExpanded(True)

        first = self.file_tree.topLevelItem(0)
        if first is not None and first.childCount():
            self.file_tree.setCurrentItem(first.child(0))

    def current_entry_index(self):
        item = self.file_tree.currentItem()
        if item is None:
            return None
        value = item.data(0, Qt.ItemDataRole.UserRole)
        return None if value is None else int(value)

    def tree_selection_changed(self, current, previous):
        index = self.current_entry_index()
        if index is not None:
            self.show_entry(index)

    # -------------------------------------------------------------- 渲染

    def show_entry(self, index):
        if index < 0 or index >= len(self.entries):
            return
        entry = self.entries[index]
        state = self.checked[index]

        self.table.blockSignals(True)
        self.table.setRowCount(0)
        self.table.setRowCount(len(entry['rows']))
        for row_index, row in enumerate(entry['rows']):
            self.fill_row(row_index, row, state.get(row.field, False), entry)
        self.table.blockSignals(False)

    def fill_row(self, row_index, row, is_checked, entry):
        tip = row_tooltip(row)
        label = QTableWidgetItem(row_label(row))
        if row.writable and row.status != ffmeta.STATUS_INFO:
            label.setFlags(label.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            label.setCheckState(
                Qt.CheckState.Checked if is_checked else Qt.CheckState.Unchecked)
        else:
            label.setFlags(label.flags() & ~Qt.ItemFlag.ItemIsUserCheckable)
        if tip:
            label.setToolTip(tip)
        self.table.setItem(row_index, 0, label)

        if row.field == 'cover':
            lib_item = QTableWidgetItem(cover_text(row.lib_count))
            file_item = QTableWidgetItem(cover_text(row.file_count))
            lib_item.setIcon(icon_from_bytes(entry['lib'].get('cover')))
            file_item.setIcon(icon_from_bytes(entry['media'].get('cover')))
        else:
            lib_item = QTableWidgetItem(row.lib_display)
            file_item = QTableWidgetItem(row.file_display)
        if tip:
            lib_item.setToolTip(tip)
            file_item.setToolTip(tip)
        self.table.setItem(row_index, 1, lib_item)
        self.table.setItem(row_index, 2, file_item)

        status_item = QTableWidgetItem(STATUS_TEXT.get(row.status, row.status))
        color = self.status_colors.get(row.status)
        if color:
            status_item.setForeground(QColor(color))
        # 加粗同时又着色，这样不靠颜色单一通道也能区分出不一致的状态。
        if row.status in EMPHASISED:
            font = status_item.font()
            font.setBold(True)
            status_item.setFont(font)
        if tip:
            status_item.setToolTip(tip)
        self.table.setItem(row_index, 3, status_item)

    def item_toggled(self, item):
        index = self.current_entry_index()
        if index is None or item.column() != 0:
            return
        rows = self.entries[index]['rows']
        if item.row() >= len(rows):
            return
        self.checked[index][rows[item.row()].field] = (
            item.checkState() == Qt.CheckState.Checked)

    # -------------------------------------------------------------- 选择

    def set_all(self, predicate):
        index = self.current_entry_index()
        if index is None:
            return
        for row in self.entries[index]['rows']:
            if row.writable and row.status != ffmeta.STATUS_INFO:
                self.checked[index][row.field] = predicate(row)
        self.show_entry(index)

    def select_all(self):
        self.set_all(lambda row: True)

    def select_none(self):
        self.set_all(lambda row: False)

    def select_differing(self):
        """只选不一致的行；「仅同步回书库」的时长行按它自己的默认值（不同才勾）。"""
        self.set_all(lambda row: row.status in ffmeta.DEFAULT_CHECKED
                     or (row.status == ffmeta.STATUS_LIB_ONLY
                         and row.checked_by_default))

    # ---------------------------------------------------------------- 计划

    def plan(self):
        """按当前勾选状态生成写入任务列表。"""
        shared = None
        if self.apply_all.isChecked():
            index = self.current_entry_index()
            if index is not None:
                shared = {f for f, on in self.checked[index].items() if on}

        jobs = []
        for index, entry in enumerate(self.entries):
            fields = shared if shared is not None else {
                f for f, on in self.checked[index].items() if on}
            chosen = [r for r in entry['rows'] if r.field in fields]
            updates, cover_action = ffmeta.rows_to_updates(
                chosen, ffmeta.collect_cover(chosen))
            cover_data = None
            cover_ext = 'jpg'
            if cover_action == ffmeta.COVER_SET:
                cover_data = entry['lib'].get('cover')
                if not cover_data:
                    cover_action = ffmeta.COVER_KEEP
            if not updates and cover_action == ffmeta.COVER_KEEP:
                continue
            jobs.append({
                'book_id': entry['book_id'],
                'fmt': entry['fmt'],
                'path': entry['path'],
                'title': entry['title'],
                'updates': updates,
                'cover_action': cover_action,
                'cover_data': cover_data,
                'cover_ext': cover_ext,
                'existing_cover_index': entry['media'].get('cover_stream_index'),
                # 写回时可能要把容器放不下的轨道剔掉（见 ffmeta.write_media 的重试），
                # 并且要保留 major_brand（m4b 的 "M4B " 不显式传就会变成 "M4A "）。
                'streams': entry['media'].get('streams') or (),
                'brand': entry['media'].get('tech', {}).get('brand'),
            })
        return jobs

    # ------------------------------------------------- 反向：文件 -> 书库

    def checked_fields(self, index):
        """这个文件在右侧表格里被勾选的字段。两个方向共用这套勾选。"""
        return {f for f, on in self.checked[index].items() if on}

    def library_plan(self):
        """每本书要从文件更新什么；没有任何差异的书不出现。

        只写用户勾选的行（和「更新文件元数据」共用同一套复选框）。
        一本书可能有多个媒体格式（MP3 + M4B），它们合并到同一条书库记录上。
        """
        plan = []
        by_book = {}
        for index, entry in enumerate(self.entries):
            chosen = self.checked_fields(index)
            updates = book_updates(entry, chosen)
            if not updates:
                continue
            job = by_book.get(entry['book_id'])
            if job is None:
                job = {'book_id': entry['book_id'], 'title': entry['title'],
                       'updates': {}, 'fields': set(), 'media': []}
                by_book[entry['book_id']] = job
                plan.append(job)
            job['updates'].update(updates)
            job['fields'] |= chosen
            job['media'].append(entry['media'])
        return plan

    def update_library(self):
        """把文件里的标签写回书库（与 plan() 的方向相反）。

        用 get_metadata() + set_metadata() 合并：文件里没有的字段保持书库原值，
        所以不会把书库里已有的元数据清掉。
        """
        if self.db is None:
            return
        plan = self.library_plan()
        if not plan:
            info_dialog(
                self, _('Nothing to update'),
                _('The media files do not carry any metadata that differs from the library.'),
                show=True)
            return

        fields = sum(len(job['updates']) for job in plan)
        message = ngettext(
            'Use the files to update {} book?',
            'Use the files to update {} books?', len(plan)).format(len(plan))
        message += '\n\n' + ngettext(
            '{} field will be overwritten by the value from the file.',
            '{} fields will be overwritten by the values from the files.', fields).format(fields)
        # question_dialog 本身就是模态的，签名里没有 show 参数（info_dialog / error_dialog 才有）。
        if not question_dialog(self, _('Update library from files'), message):
            return

        changed = set()
        errors = []
        for job in plan:
            book_id = job['book_id']
            try:
                merged = self.db.get_metadata(book_id, get_cover=True, cover_as_data=True)
                for media in job['media']:
                    merge_file_metadata(merged, media, job['fields'])
                self.db.set_metadata(book_id, merged)
                changed.add(book_id)
            except Exception as err:
                errors.append('%s：%s' % (job['title'], err))

        if changed:
            # 测试里构造对话框时没有 gui，只刷新界面这一步跳过。
            gui = getattr(self, 'gui', None)
            if gui is not None:
                try:
                    gui.library_view.model().refresh_ids(changed)
                except Exception:
                    pass

        self.library_updated = True
        summary = ngettext('Updated {} book.', 'Updated {} books.',
                           len(changed)).format(len(changed))
        if errors:
            error_dialog(
                self, _('Library updated'),
                summary + '\n\n' + _('Problems:') + '\n' + '\n'.join(errors[:50]),
                show=True)
        else:
            info_dialog(self, _('Library updated'), summary, show=True)
        self.reject()


# ------------------------------------------------------- 文件 -> 书库的纯逻辑

BIBLIO_FIELDS = ('title', 'authors', 'series', 'series_index', 'tags',
                 'comments', 'publisher', 'pubdate')


def _same_value(field, current, new):
    """书库里的值和文件里的值是否等价（和对比表用同一套归一化规则）。"""
    if field == 'authors':
        return ffmeta.canonical_authors(current) == ffmeta.canonical_authors(new)
    if field == 'tags':
        return ffmeta.canonical_tags(current) == ffmeta.canonical_tags(new)
    if field == 'series_index':
        return ffmeta.canonical_index(current) == ffmeta.canonical_index(new)
    if field == 'pubdate':
        return ffmeta.canonical_date(current) == ffmeta.canonical_date(new)
    if field == 'identifiers':
        return dict(current or {}).get('isbn') == dict(new or {}).get('isbn')
    return (ffmeta.canonical_text(current).casefold()
            == ffmeta.canonical_text(new).casefold())


def _same_number(current, value):
    """时长是浮点数，比较时容忍四舍五入的误差。"""
    try:
        return abs(float(current) - float(value)) < 0.005
    except (TypeError, ValueError):
        return False


def book_updates(entry, fields=None):
    """算出一本书该从文件更新哪些字段，只返回勾选过、而且确实不一样的。

    `fields` 是用户在表格里勾选的行（None 表示不限，供测试/内部调用）。
    """
    values = metadata.library_values(entry['media'])
    if fields is not None:
        values = {f: v for f, v in values.items() if f in fields}
    updates = {field: value for field, value in values.items()
               if not _same_value(field, entry['lib'].get(field), value)}

    # 时长是自定义列，不在 library_values 里：按配置算出来，再和书库里已有的值比。
    # 只有该列真的存在于书库、并且这一行被勾选时才算差异。
    # 注意：别的字段都一致、只有时长不同时，也必须有这条更新，否则整本书会被 plan 跳过。
    if fields is None or 'duration' in fields:
        column, duration = metadata.duration_value(entry['media']['tech'].get('duration'))
        if column and 'duration' in entry['lib'] and duration is not None:
            if not _same_number(entry['lib'].get('duration'), duration):
                updates[column] = duration

    # 封面单独标记，写库时按勾选合并；计数用得上。
    if (fields is None or 'cover' in fields) and entry['media'].get('cover') \
            and not entry['lib'].get('cover'):
        updates['cover'] = True
    return updates


def merge_file_metadata(mi, media, fields=None):
    """把文件里的值合并进一本已有书的元数据对象。

    只覆盖勾选过的字段；文件里没有的字段、以及没勾选的字段都保持书库原值。
    `fields` 为 None 表示全部合并（内部/测试用）。
    """
    values = metadata.library_values(media)
    file_mi = metadata.build_metadata(media)

    def wanted(field):
        return fields is None or field in fields

    for field in BIBLIO_FIELDS:
        if field in values and wanted(field):
            setattr(mi, field, getattr(file_mi, field))
    if 'identifiers' in values and wanted('identifiers'):
        mi.set_identifiers(dict(file_mi.identifiers or {}))
    if media.get('cover') and wanted('cover'):
        mi.cover_data = file_mi.cover_data
    # 时长走自定义列：datatype 必须和真实列一致，所以用插件自己的配置去声明。
    if wanted('duration'):
        metadata.apply_duration(mi, media['tech'].get('duration'))
    return mi
