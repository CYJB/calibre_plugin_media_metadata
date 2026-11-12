#!/usr/bin/env python3
"""建一个临时 calibre 书库，里面放着带标签的样本 MP3。

    calibre-debug tests/setup_library.py [书库目录]

必须用 calibre 自己的解释器跑。整条链路都在进程内完成：

1. `Cache(DB())` 建库并加 `#duration` 自定义列；
2. `metadata_from_formats()` 读样本 —— 这就是 calibredb 和「添加书籍」用的那个分发器，
   所以已安装的 reader 插件会被真正调用一次（书名/作者/系列/标签/时长都从文件来）；
3. `create_book_entry()` + `add_format()` 把书和 MP3 放进书库。

不用 `calibredb`：它会先检查「另一个 calibre 程序正在运行」而拒绝执行，机器上开着
calibre GUI 调试插件时这条路直接走不通，而测试恰恰常在这种时候跑。

Windows 和 macOS/Linux 都能跑；旧的 setup_library.sh 需要 POSIX shell，
还硬编码了 macOS 的应用包路径。
"""

import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
LIB = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
    tempfile.gettempdir(), 'media-lib-test')
SAMPLE = os.path.join(HERE, 'out', 'tagged.mp3')
FORMAT = 'MP3'


def describe(db, book_id):
    """打印这本书的关键字段，方便肉眼确认 reader 插件真的读到了东西。"""
    mi = db.get_metadata(book_id)
    duration = ''
    try:
        duration = db.field_for('#duration', book_id)
    except Exception:
        pass
    print('  %s | %s | %s | %s | %s' % (
        mi.title, ' & '.join(mi.authors or ()), ' & '.join(mi.tags or ()),
        mi.series or '-', duration))


def main():
    if not os.path.isfile(SAMPLE):
        print('missing sample %s - run: python3 tests/make_samples.py' % SAMPLE,
              file=sys.stderr)
        return 1

    if os.path.isdir(LIB):
        shutil.rmtree(LIB)

    from calibre.db.cache import Cache
    from calibre.db.backend import DB

    db = Cache(DB(LIB))
    db.create_custom_column('duration', 'Duration', 'float', False)
    db.close()
    print('created library with #duration column')

    from calibre.db.legacy import LibraryDatabase
    from calibre.ebooks.metadata.meta import metadata_from_formats

    library = LibraryDatabase(LIB)
    db = library.new_api
    try:
        mi = metadata_from_formats([SAMPLE])
        print('read from the file: %s | %s | %s' % (
            mi.title, ' & '.join(mi.authors or ()), ' & '.join(mi.tags or ())))
        book_id = db.create_book_entry(mi)
        db.add_format(book_id, FORMAT, SAMPLE, run_hooks=False)
        print('library ready at %s (book id %s)' % (LIB, book_id))
        describe(db, book_id)
    finally:
        library.close()
    return 0


if __name__ == '__main__':
    sys.exit(main())
