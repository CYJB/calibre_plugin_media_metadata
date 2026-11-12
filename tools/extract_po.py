#!/usr/bin/env python3
"""从插件源码里提取可翻译字符串。

    python3 tools/extract_po.py            # 列出 msgid
    python3 tools/extract_po.py --counts   # 附带源码位置

用 AST 而不是正则，这样跨行的隐式字符串拼接和转义引号都能正确处理。
"""

import ast
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SOURCE_DIRS = ('common', 'reader', 'sync')
FUNCTIONS = ('_', 'ngettext')


def strings_in(node):
    """产出调用参数里的字面量字符串片段（能处理 'a' 'b' 这种写法）。"""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        yield node.value


def collect():
    found = {}
    for source_dir in SOURCE_DIRS:
        base = os.path.join(ROOT, source_dir)
        for name in sorted(os.listdir(base)):
            if not name.endswith('.py'):
                continue
            path = os.path.join(base, name)
            with open(path, encoding='utf-8') as fp:
                tree = ast.parse(fp.read(), path)
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                fname = func.id if isinstance(func, ast.Name) else getattr(func, 'attr', None)
                if fname not in FUNCTIONS:
                    continue
                for index, arg in enumerate(node.args):
                    if fname == 'ngettext' and index > 1:
                        break
                    for value in strings_in(arg):
                        found.setdefault(value, []).append(
                            '%s/%s:%d' % (source_dir, name, node.lineno))
    return found


def main():
    found = collect()
    counts = '--counts' in sys.argv
    for msgid in sorted(found):
        if counts:
            print('%s\n    %s' % (msgid, ', '.join(found[msgid])))
        else:
            print(msgid)
    print('\n# %d strings' % len(found), file=sys.stderr)
    return 0


if __name__ == '__main__':
    sys.exit(main())
