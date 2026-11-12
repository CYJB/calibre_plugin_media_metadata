#!/usr/bin/env python3
"""把 .po 文件编译成 .mo。

系统里既没有 gettext 的 msgfmt 也没有 babel，而 .mo 的二进制格式简单到可以直接写。
输出结构与 msgfmt 产生的一致：一个 header 条目加按 msgid 排序的条目，没有哈希表。

    python3 tools/po2mo.py translations/zh_CN.po
"""

import os
import struct
import sys

MAGIC = 0x950412de

ESCAPES = {
    'n': '\n', 't': '\t', 'r': '\r', '\\': '\\', '"': '"',
    "'": "'", '0': '\0',
}


def unquote(text):
    """把一个 PO 引号字符串的内容解码成 Python str。"""
    out = []
    index = 0
    while index < len(text):
        char = text[index]
        if char == '\\' and index + 1 < len(text):
            nxt = text[index + 1]
            out.append(ESCAPES.get(nxt, nxt))
            index += 2
        else:
            out.append(char)
            index += 1
    return ''.join(out)


def strip_quotes(text):
    text = text.strip()
    if len(text) >= 2 and text[0] == '"' and text[-1] == '"':
        return text[1:-1]
    raise ValueError('not a quoted PO string: %r' % text)


def parse_po(path):
    """按文件顺序返回 (msgid, msgid_plural, [msgstr 各形式]) 列表。"""
    with open(path, encoding='utf-8') as fp:
        lines = fp.read().splitlines()

    entries = []
    current = None
    field = None

    def flush():
        nonlocal current, field
        if current is not None:
            entries.append(current)
        current = None
        field = None

    for raw in lines:
        line = raw.strip()
        if not line or line.startswith('#'):
            if not line and current is not None:
                flush()
            continue
        if line.startswith('msgid_plural'):
            field = 'plural'
            current['plural'] += unquote(strip_quotes(line.split(None, 1)[1]))
        elif line.startswith('msgid'):
            if current is not None:
                flush()
            current = {'id': '', 'plural': '', 'strs': {}}
            field = 'id'
            current['id'] += unquote(strip_quotes(line.split(None, 1)[1]))
        elif line.startswith('msgstr'):
            head, _, rest = line.partition(' ')
            if head.startswith('msgstr['):
                index = int(head[len('msgstr['):head.index(']')])
            else:
                index = 0
            field = ('str', index)
            current['strs'].setdefault(index, '')
            current['strs'][index] += unquote(strip_quotes(rest))
        elif line.startswith('"'):
            # 上一个字段的续行
            value = unquote(strip_quotes(line))
            if field == 'id':
                current['id'] += value
            elif field == 'plural':
                current['plural'] += value
            elif isinstance(field, tuple):
                current['strs'][field[1]] += value
        else:
            raise ValueError('%s: unparsable line %r' % (path, raw))
    flush()

    result = []
    for entry in entries:
        forms = [entry['strs'][k] for k in sorted(entry['strs'])]
        result.append((entry['id'], entry['plural'], forms))
    return result


def build_mo(entries):
    """把解析好的 PO 条目序列化成 .mo 字节。"""
    table = {}
    for msgid, plural, forms in entries:
        if not forms or not any(forms):
            continue  # untranslated entries are omitted, gettext falls back
        if plural:
            key = msgid.encode('utf-8') + b'\0' + plural.encode('utf-8')
            value = b'\0'.join(f.encode('utf-8') for f in forms)
        else:
            key = msgid.encode('utf-8')
            value = forms[0].encode('utf-8')
        table[key] = value

    keys = sorted(table)
    count = len(keys)
    keystart = 7 * 4 + 16 * count
    valuestart = keystart + sum(len(k) + 1 for k in keys)
    keyoffsets = []
    valueoffsets = []
    offset = keystart
    for key in keys:
        keyoffsets.append((len(key), offset))
        offset += len(key) + 1
    for key in keys:
        value = table[key]
        valueoffsets.append((len(value), offset))
        offset += len(value) + 1

    out = [
        struct.pack('<Iiiiiii', MAGIC, 0, count, 7 * 4, 7 * 4 + count * 8, 0, 0),
    ]
    for length, off in keyoffsets:
        out.append(struct.pack('<ii', length, off))
    for length, off in valueoffsets:
        out.append(struct.pack('<ii', length, off))
    for key in keys:
        out.append(key + b'\0')
    for key in keys:
        out.append(table[key] + b'\0')
    return b''.join(out)


def main(argv):
    if not argv:
        print(__doc__)
        return 1
    status = 0
    for source in argv:
        entries = parse_po(source)
        data = build_mo(entries)
        dest = os.path.splitext(source)[0] + '.mo'
        with open(dest, 'wb') as fp:
            fp.write(data)
        translated = sum(1 for m, p, f in entries if m and any(f))
        print('%s -> %s (%d entries, %d translated, %d bytes)'
              % (source, dest, len(entries), translated, len(data)))

        # 回读校验刚写出的内容确实能被 gettext 加载。
        import gettext
        from io import BytesIO
        catalog = gettext.GNUTranslations(BytesIO(data))
        probe = next((m for m, p, f in entries if m and any(f)), None)
        if probe:
            expected = dict((m, f[0]) for m, p, f in entries if m and any(f))[probe]
            got = catalog.gettext(probe)
            if got != expected:
                print('  FAILED round-trip: %r -> %r (want %r)' % (probe, got, expected))
                status = 1
            else:
                print('  round-trip ok: %r -> %r' % (probe, got))
        charset = catalog._charset
        if charset and charset.lower().replace('-', '') != 'utf8':
            print('  WARNING: unexpected charset %r' % charset)
    return status


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
