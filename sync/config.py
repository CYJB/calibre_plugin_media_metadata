from qt.core import (
    QAbstractItemView, QCheckBox, QComboBox, QHBoxLayout, QHeaderView, QLabel,
    QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from calibre.utils.localization import _

try:
    from . import binaries
    from .settings import prefs
except ImportError:
    from calibre_plugins.MediaMetadataSync import binaries
    from calibre_plugins.MediaMetadataSync.settings import prefs

try:
    load_translations()
except NameError:
    pass


class ConfigWidget(QWidget):
    def __init__(self, parent=None):
        QWidget.__init__(self, parent)
        layout = QVBoxLayout()
        self.setLayout(layout)

        self.binaries = binaries.BinaryConfigWidget()
        layout.addWidget(self.binaries)

        row = QHBoxLayout()
        layout.addLayout(row)
        row.addWidget(QLabel(_('ID3 version for mp3:')))
        self.id3v2_version = QComboBox()
        self.id3v2_version.addItems(['3', '4'])
        self.id3v2_version.setToolTip(_(
            'v2.3 is understood by the widest range of players and hardware; '
            'v2.4 stores UTF-8 tags natively.'))
        row.addWidget(self.id3v2_version)
        row.addStretch(1)

        self.write_cover = QCheckBox(_('Write cover art into the media files'))
        layout.addWidget(self.write_cover)

        self.register_context_menu = QCheckBox(
            _('Add this action to the book list right-click menu automatically'))
        self.register_context_menu.setToolTip(_(
            'The entry appears in the right-click menu after the next calibre '
            'restart. Turn this off to place the action yourself in Preferences → '
            'Toolbars → “The context menu for the books in the calibre library”.'))
        layout.addWidget(self.register_context_menu)

        layout.addWidget(QLabel(_('Custom columns to sync:')))
        self.columns = QTableWidget(0, 3)
        self.columns.setHorizontalHeaderLabels(
            [_('Lookup name'), _('Label'), _('Tag key')])
        self.columns.verticalHeader().setVisible(False)
        self.columns.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        header = self.columns.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.columns, stretch=1)

        buttons = QHBoxLayout()
        layout.addLayout(buttons)
        add = QPushButton(_('Add column'))
        add.clicked.connect(self.add_column)
        buttons.addWidget(add)
        remove = QPushButton(_('Remove column'))
        remove.clicked.connect(self.remove_column)
        buttons.addWidget(remove)
        buttons.addStretch(1)

        note = QLabel(_(
            'Map a calibre custom column to a tag key, e.g. #narrator → album_artist. '
            'The lookup name is the one shown in Preferences → Add your own columns, '
            'with or without the leading #. Tag keys are ffmpeg names such as artist, '
            'album, genre, comment, date, publisher, album_artist, composer, isbn.'))
        note.setWordWrap(True)
        note.setStyleSheet('color: gray;')
        layout.addWidget(note)

        self.load_settings()

    def add_column(self):
        row = self.columns.rowCount()
        self.columns.insertRow(row)
        for column in range(3):
            self.columns.setItem(row, column, QTableWidgetItem(''))
        self.columns.setCurrentCell(row, 0)

    def remove_column(self):
        rows = sorted({i.row() for i in self.columns.selectedIndexes()}, reverse=True)
        for row in rows:
            self.columns.removeRow(row)

    def load_settings(self):
        self.binaries.load_settings()
        version = str(prefs['id3v2_version'] or 3)
        index = self.id3v2_version.findText(version)
        self.id3v2_version.setCurrentIndex(max(index, 0))
        self.write_cover.setChecked(bool(prefs['write_cover']))
        self.register_context_menu.setChecked(bool(prefs['register_context_menu']))

        self.columns.setRowCount(0)
        for item in prefs['custom_columns'] or ():
            row = self.columns.rowCount()
            self.columns.insertRow(row)
            for column, key in enumerate(('lookup', 'label', 'tag')):
                self.columns.setItem(row, column, QTableWidgetItem(str(item.get(key) or '')))

    def save_settings(self):
        self.binaries.save_settings()
        prefs['id3v2_version'] = int(self.id3v2_version.currentText())
        prefs['write_cover'] = bool(self.write_cover.isChecked())
        prefs['register_context_menu'] = bool(self.register_context_menu.isChecked())

        columns = []
        for row in range(self.columns.rowCount()):
            def text(column):
                item = self.columns.item(row, column)
                return (item.text().strip() if item else '')
            lookup, label, tag = text(0), text(1), text(2)
            if not lookup or not tag:
                continue
            columns.append({'lookup': lookup, 'label': label or lookup, 'tag': tag})
        prefs['custom_columns'] = columns

    def validate(self):
        # validate() 返回 True 之后，calibre 自己会调 save_settings()。
        self.binaries.refresh_status()
        return True
