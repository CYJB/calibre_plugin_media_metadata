from qt.core import QComboBox, QFormLayout, QLabel, QLineEdit, QVBoxLayout, QWidget

from calibre.utils.localization import _

try:
    from . import binaries
    from . import prefs, DURATION_DATATYPES
except ImportError:
    import binaries
    from calibre_plugins.MediaMetadataReader import prefs, DURATION_DATATYPES

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

        form = QFormLayout()
        layout.addLayout(form)

        self.duration_column = QLineEdit()
        form.addRow(_('Duration column:'), self.duration_column)

        self.duration_datatype = QComboBox()
        self.duration_datatype.addItems(list(DURATION_DATATYPES))
        form.addRow(_('Duration column datatype:'), self.duration_datatype)

        note = QLabel(_(
            'The media duration is stored in a calibre custom column, because calibre has '
            'no built-in duration field. Create the column first (Preferences → Add your own '
            'columns), then enter its lookup name here, e.g. #duration.\n\n'
            'The datatype must match the column you created exactly — calibre silently '
            'ignores the value if it does not. Leave the column name blank to skip duration.'))
        note.setWordWrap(True)
        note.setStyleSheet('color: gray;')
        form.addRow(note)

        self.duration_column.setText(prefs['duration_column'])
        index = self.duration_datatype.findText(prefs['duration_datatype'])
        self.duration_datatype.setCurrentIndex(max(index, 0))
        self.binaries.load_settings()

    def validate(self):
        return self.binaries.validate()

    def save_settings(self):
        self.binaries.save_settings()
        prefs['duration_column'] = self.duration_column.text().strip()
        prefs['duration_datatype'] = self.duration_datatype.currentText()
