# AGENTS.md

面向开发者和 agent 的实现说明。用户文档见 `README.md`，这里只讲怎么改、为什么这样改、
以及已经踩过的坑。

所有 `file.py:NNN` 引用都指 **calibre 9.18 源码**。
改插件前请核对 calibre 的 API，不要凭记忆——
calibre 在 Qt6 重构中删掉过一批老牌 API（`SizePersistedDialog`、
`add_generic_menu_callback`、`location_requested`、`about_to_be_shown` 都已不存在）。
在 Windows 上就用已装的 calibre 自带解释器临场核对：
`calibre-debug -c "import inspect, calibre.customize as c; print(inspect.signature(c.MetadataReaderPlugin.get_metadata))"`。

## 项目结构

```
common/        ffutil.py ffmeta.py binaries.py metadata.py  两个插件共用，build.py 打包时各复制一份
reader/        __init__.py (MetadataReaderPlugin) + config.py
sync/          __init__.py (InterfaceActionBase) + action.py + dialog.py + config.py + settings.py
translations/  zh_CN.po → build.py 编译成 zh_CN.mo 打进两个 zip
tools/         extract_po.py 提取 msgid；po2mo.py 编译 .po → .mo
tests/         全部可无头运行，不需要打开 GUI
build.py       编译翻译 + 覆盖率检查 + 打包 zip（不安装）
dist/          构建产物（.gitignore）
```

`common/ffutil.py`、`common/ffmeta.py` **刻意不 import calibre**，也不使用 Python 3.10+
语法，这样可以用系统 `python3` 直接跑单测（calibre 7.0 起自带 3.11，另外有人用 3.9 跑
测试）。改动这两个文件时请保持这个约束。
`common/binaries.py` 是 GUI 配置组件，依赖 Qt 和 calibre，只能用 `calibre-debug` 测。
`common/metadata.py` 也一样依赖 calibre：它把「媒体标签 → calibre 字段」的映射集中在一处
（`library_values()`），reader 插件和 sync 插件「用文件更新书库」都从它派生，
所以**新增字段只改这里**。它同时也持有时长列配置（`plugins/MediaMetadataReader`）。

测试脚本全部是 Python，`make_samples.sh` / `setup_library.sh` 已经退役：Windows 上没有
POSIX shell，两个脚本原来又硬编码了 macOS 的 `.app` 路径，所以改成
`tests/make_samples.py`（造样本，只依赖 ffmpeg）和 `tests/setup_library.py`
（用 calibre 自己的解释器建临时书库）。两者都是跨平台的，**不要**再把 shell 脚本加回来。


## calibre 插件机制的硬约束

这几条都验证过，违反任何一条插件都不会工作：

1. **一个 zip 只注册一个插件类。** `customize/zipplugin.py:347-356` 发现多个
   `Plugin` 子类时按模块深度排序，只取第一个。所以 reader 和 GUI action 必须分开打包，
   且 `sync/__init__.py` 里**不能在模块级 import `action.py`**（`SyncAction` 也是
   `Plugin` 子类，import 进来就会和 `MediaMetadataSyncBase` 冲突）。
   `action.py` 只通过 `actual_plugin` 字符串懒加载。

2. **zip 内必须有 `plugin-import-name-<Name>.txt`。** 否则
   `zipplugin.py:377-390` 会把模块名分配成 `calibre_plugins.dummyN`，
   `actual_plugin = 'calibre_plugins.MediaMetadataSync.action:SyncAction'` 直接失效。
   `build.py` 自动写入这个标记文件。

3. **插件 `name` 不能含 `/`。** `customize/ui.py:634` 用
   `os.path.join(plugin_dir, plugin.name + '.zip')` 作为安装路径，斜杠会被当成目录分隔符
   导致 `FileNotFoundError`。

4. **安装必须走 calibre 注册，不能只拷 zip。** `customize/ui.py:894` 从
   `config['plugins']` 注册表加载外部插件，不扫描插件目录。用户侧只有一条路：
   GUI 的「Load plugin from file」选 `dist/*.zip`，然后重启 calibre。
   仓库**不提供命令行安装**（`build.py` 只负责打包 zip）；开发机上要批量装，用
   calibre 自带的 `calibre-customize --add-plugin <zip>`，**一个 zip 一次**。

5. **翻译靠 calibre 注入，不是靠 import。** `zipplugin.py:239-247` 给 zip 里**每个**模块
   注入一个 `load_translations()`，调用后它用 `translations/<lang>.mo` 的 catalog 覆盖
   该模块的 `_` 和 `ngettext`。所以每个用到 `_()` 的模块都要在 import 之后、
   **任何模块级 `_()` 之前**调一次：

   ```python
   try:
       load_translations()
   except NameError:
       pass          # 不在 calibre 的 zip 加载器里（例如无头测试）
   ```

   顺序很关键：`sync/dialog.py` 的 `STATUS_TEXT` / `FIELD_LABELS` / `TOOLTIP_TEXT`
   都是 import 期构建的字典，`load_translations()` 晚一行就全是英文。

6. **`_(x)` 只接受一个参数**（`utils/localization.py:401`）。复数要用
   `ngettext(singular, plural, n)`（`:409`），然后自己 `.format(n)`。
   传三个参数给 `_()` 会在运行时抛 TypeError。

7. **想让「Customize」按钮可用，必须实现 `customization_help()`**，光有
   `config_widget()` 不够。`gui2/preferences/plugins.py:385` 的门禁是
   `if not plugin.is_customizable(): → 弹「插件不需要自定义」并 return`，
   而 `customize/__init__.py:319-324` 把 `is_customizable()` 实现成
   "调 `customization_help()`，抛 NotImplementedError 就返回 False"，
   基类的 `customization_help()`（`:306`）正是抛 NotImplementedError。
   这个 bug 真实发生过：两个插件的 `config_widget()` 都完好，但管理界面一律提示没有配置。
   `tests/test_plugins.py` 有专门的断言，别再漏。

   另外 `InterfaceActionBase` 还有第二道门禁（`plugins.py:391`）：
   `actual_iaction_plugin_loaded` 为假时提示必须重启 calibre。该属性由
   `gui2/ui.py:194` 在 GUI 启动实例化动作时设置，正常启动后就为真，不需要额外处理。

## 配置存储

三份 `JSONConfig`：

| 文件 | 内容 | 定义处 |
|---|---|---|
| `plugins/MediaMetadata.json` | ffmpeg / ffprobe 路径（**两个插件共用**） | `common/binaries.py:binary_prefs` |
| `plugins/MediaMetadataReader.json` | 时长列名与 datatype | `common/metadata.py:prefs` |
| `plugins/MediaMetadataSync.json` | ID3 版本、是否写封面、是否注册右键菜单、自定义列映射 | `sync/settings.py:prefs` |

ffmpeg 路径只存一份，用户在任意一个插件的配置界面里填一次即可。两个 zip 各自带一份
`binaries.py` 副本，但都指向同一个文件路径，所以能共享。

**`locate_binaries()` 里的 `binary_prefs.refresh()` 不能删。** `JSONConfig.__getitem__`
只读内存字典再回退 defaults（`utils/config.py:441-450`），而 `JSONConfig` 没有自己的
`__init__` / `refresh`，继承自 `XMLConfig`（`:318`）——`refresh()` 只在构造时被调一次
（`:339`）。这意味着：同一进程里两个指向同一文件的实例互相看不见对方的写入；而 reader
插件跑在 calibre 的 worker 子进程里、设置却是从 GUI 进程保存的。不 refresh 就会读到陈旧路径。
`ffutil.clear_cache()` 同理，清的是二进制定位结果的进程内缓存。

`tests/test_plugins.py` 会验证两个插件的 `binary_prefs.file_path` 相同、各自的私有 prefs
里不再有 `ffmpeg_path`、以及从一个插件的配置界面保存能被另一个读到。

## 界面文案

**ffutil / ffmeta 不产出面向用户的文案**，因为它们不能 import calibre、拿不到 `_()`：

- `ffmeta.FieldRow` 只给字段键和 tooltip 键，文案在 `sync/dialog.py`；
- `ffutil.ToolError.message` 是**英文技术描述**，只进日志和异常链。它带一个 `kind`
  （`not_found` / `cannot_run` / `timeout` / `failed`）和 `path` / `detail` / `stderr`，
  界面按 kind 组装中文：`binaries.tool_error_text()` 负责翻译，
  `sync/action.py:describe_error()` 负责在「问题：」列表里调用它。

所以**新增一条会显示给用户的 ffmpeg 提示时**：在 `binaries.tool_error_text()` 里加分支、
给 `ToolError` 加上对应的 kind，再把 msgid 加进 `translations/zh_CN.po`。
不要把英文 `str(err)` 直接塞进对话框——那是用户会在界面上看到的英文。
ffmpeg 自己写到 stderr 的内容保持原样（那是它的英文日志，翻译没有意义）。
`tests/test_plugins.py` 在 zh_CN 下断言这些提示都是中文。

## 右键菜单

菜单项**只**通过写 `gprefs['action-layout-context-menu']` 注册，由 calibre 在启动时
自己构建（`gui2/init.py:108`）。安装后需要重启一次 calibre。

不要尝试运行时往菜单里插动作。`gui2/bars.py:427-437` 的 `populate_menu` 放进去的是
`CloneAction` 包装对象而不是原动作，而且当快捷键注册成功时
（`gui2/actions/__init__.py:279` 设置 `shortcut_action_for_context_menu`）
它克隆的是**那个** action 而不是 `qaction`（`bars.py:434-436`）。
按 `qaction` 判重永远匹配不到，会产生重复菜单项——这个 bug 真实发生过两次。
`sync/action.py:updated_layout()` 是纯函数，好测；保持这个设计。

动作名 `ACTION_NAME = 'Media Metadata Sync'` 是 layout pref 里的键
（对应 `InterfaceAction.name`，不是插件的 `name`），改动它会让已注册的用户丢失菜单项。

## 元数据读取链路

`MetadataReaderPlugin.get_metadata(stream, type)`（`customize/__init__.py:520`）拿到的是
**文件对象**，不是路径。calibre 用 `open(path,'rb')` 传入，所以 `stream.name` 可用；
拿不到真实路径时 `reader/__init__.py:stream_to_path()` 会把流落到临时文件。
ffmpeg/ffprobe 需要可 seek 的真实路径，不能喂管道。

分发：`customize/ui.py:482-505` 按 `file_types` 建 `_metadata_readers` 索引，
`:564-583` 的 `get_file_type_metadata` 查表调用。`file_types` 里有 `'mp3'` 就会被 mp3 调用。

**扩展名过滤只发生在目录扫描路径**：

| 入口 | 是否过滤 | 位置 |
|---|---|---|
| 添加书籍对话框里手动选中的文件 | 不过滤 | `gui2/add.py:246` 的 else 分支直接进 `file_groups` |
| `calibredb add <file>` | 不过滤 | `db/cli/cmd_add.py:233` 直接调 `metadata_from_formats` |
| 拖拽 / 从文件夹添加 / 自动添加 | **过滤** | `db/adding.py:161-165` 的 `allow_path()` 要求扩展名在 `metadata_extensions()` = `BOOK_EXTENSIONS \| {'opf'}` 里 |

`BOOK_EXTENSIONS`（`ebooks/__init__.py:40-104`）不含任何音视频扩展名，所以第三条路径
需要用户在「首选项 → 添加书籍 → 过滤所添加文件的规则」里加 allow 规则。
对话框的过滤器列表在 `gui2/actions/add.py:36-50`，只有 All files 能选到 mp3/mp4。

`meta.py:157` 的 `prefs['read_file_metadata']` 是总开关，关掉时任何 reader 都不会被调用。

### 时长写入自定义列

calibre 没有内置 duration 字段。`db/cache.py:2653-2663` 的 `_set_metadata` 会应用
`mi.user_metadata` 里的自定义列值，但有一个硬条件（`:2656`）：

```python
key in fm and user_mi[key]['datatype'] == fm[key]['datatype']
```

datatype 必须与真实列**完全一致**，否则静默忽略。而 metadata reader 跑在 worker 进程里
读不到书库结构，所以 datatype 只能由用户在插件配置里指定
（`reader/__init__.py:set_duration()` 按配置合成 `user_metadata`）。
注意 `create_book_entry` 只调 `_add_default_custom_column_values`
（`db/cache.py:184-199`），那个函数只处理配了 `default_value` 的列，帮不上忙。

### ffprobe 输出的坑

- **tag 键大小写不统一**：mp3/m4a/flac 的 `format.tags` 是小写，**mkv 是大写**
  （`ARTIST`、`MAJOR_BRAND`）。`ffmeta.collect_tags()` 统一小写化。
- **Vorbis 容器键名不同**：flac/ogg 用 `TRACKNUMBER` / `ALBUMARTIST` / `LABEL`，
  不是 `track` / `album_artist` / `publisher`。`FIELD_TO_TAGS` 里为每个字段列了别名。
- **format 级 tag 优先于 stream 级**：`collect_tags()` 先收音频流的 tag（`setdefault`），
  再用 format 级覆盖。mp3 的封面流（`codec_type == 'video'`）带 `comment: Other`，
  所以只收 `codec_type == 'audio'` 的流。
- **封面判定**：只有 `disposition.attached_pic == 1` 且 `codec_name` 属于
  `{mjpeg, png, bmp, gif}` 的流才算封面（`ffmeta.cover_stream()`）。
  真实视频流的 `attached_pic` 是 0，所以 mp4/mkv 视频不会把首帧当成封面。
  这条有专门的回归测试，别改坏。
- **容器能存的标签种类不同**：mp4/m4a/m4b 没有 publisher 标签，写进去也会被丢弃。
  这类字段在对比表里永远显示「一致」（两边都空），不是漏读。

## 对比与写回

`ffmeta.compare(lib, media, custom_columns)` 返回 `FieldRow` 列表。
`lib` 是**纯 Python 值字典**（由 `sync/action.py:lib_values()` 从 calibre 的
`Metadata` 降维而来），这样 compare 可以在无头环境测试。

**行顺序**由 `FIELD_TO_TAGS` → `UNSUPPORTED_FIELDS` → 自定义列（配置表行序）→ 封面 →
`_info_rows()` 的定义顺序决定，是确定的。唯一变化是 `_info_rows()` 里的 video 行
只在文件真有视频流时出现。改顺序就改这几处的定义顺序。

**ffmeta 不产出任何面向用户的文案**（因为它不能 import calibre）：

- `FieldRow.label` 对标准字段是稳定的字段键（`title`、`cover`、`duration`…）
- `tooltip_key` + `tooltip_args` 标识一条消息
- 封面行只给 `lib_count` / `file_count` 字节数

本地化文案全部集中在 `sync/dialog.py` 的 `FIELD_LABELS`、`TOOLTIP_TEXT`、
`STATUS_TEXT`、`cover_text()`。`ffmeta.TOOLTIPS` 保留一份英文回退，供日志、测试和
非 GUI 调用方使用（`FieldRow.tooltip` property 渲染它）。
**新增界面字符串时必须同步加到 `sync/dialog.py` 和 `translations/zh_CN.po`**，
`build.py` 的覆盖率检查会报未翻译项。

**状态色**在 `dialog.py:STATUS_COLORS`，分明暗两套，用 `dialog.py:theme_is_dark()` 判断，
只给状态列着色。`EMPHASISED` 里的状态额外加粗，不只靠颜色单一通道区分。

`theme_is_dark()` 刻意**不用** calibre 自带的 `gui2.is_dark_theme()`（`gui2/__init__.py:1243`）：
后者依赖 `QPalette.is_dark_theme()`，需要 Qt 6.5+，而插件声明兼容到 calibre 7.0。
现在的实现先读 `qapplication_or_fail().is_dark_theme`（`gui2/__init__.py:308` 用的同一个属性），
取不到时退回按调色板 `WindowText` 的亮度推断。函数名也故意和 calibre 的不同，避免混淆。

### 标签只读写 keywords

`FIELD_TO_TAGS` 里标签那一行是 `TAG_KEYS = ('keywords',)`，**只认这一个键**：

- 读：`ffmeta.media_tags()` 只取 `keywords`，`genre` / `grouping` 一律不读。
  这是刻意的：流派不是标签，混在一起会让书库塞满无关内容。旧版本写到 `genre` 的标签
  不再兼容（README 里有迁移说明）。
- 写：`compare()` 的标签行 `tag_key` 就是 `keywords`，`rows_to_updates()` 直接用它。
- 为什么不是自定义键：实测 `keywords` 在 mp3、m4a、flac、mkv 都能原样存取；自定义键
  （如 `calibre-tags`）在 m4a 会被 ffmpeg 悄悄丢掉。
- 测试样本里同时写了 `keywords` 和一个 `genre=Legacy Genre`，用来断言 genre 被忽略
  （`tests/test_ffmeta.py:test_tags_keywords_mapping`）。

### 时长行（STATUS_LIB_ONLY）

书库配了时长列（`lib` 里有 `'duration'` 键，由 `action.lib_values()` 放进去）时，
`compare()` 在封面之后加一行时长：

- 状态：两边**一样**时就是普通的 `STATUS_SAME`（绿色，默认不勾）；只有不一样时才是
  `STATUS_LIB_ONLY`（界面显示「仅同步回书库」，蓝色）。`writable=True` 所以有复选框，
  但 `tag_key` / `write_value` 都是 `None`，`rows_to_updates()` 也显式跳过它——
  **媒体文件里没有可写的时长标签**，勾它只影响「更新书库」。
- `default_checked` 由 `FieldRow` 的 `default_checked` 参数控制（文件里有值、且和书库不同
  才勾选），不走 `DEFAULT_CHECKED`；`select_differing()` 也要单独把这种行按
  `checked_by_default` 算进去。
- 书库为空、文件有时长 → 不一致、勾上（需要补）；文件里没有时长、只有书库有 →
  不一致但不勾（没有东西可写），两种都显示「仅同步回书库」。
- 书库**没有**时长列时退回 `_info_rows(media, with_duration=True)` 的只读信息行
  （没有复选框），保持和以前一致。
- 拉平给 `lib` 的键名固定是 `'duration'`，不是 `'#duration'`：用户把列命名成什么都可以，
  ffmeta 不需要知道。

### 写回命令

`ffmeta.build_write_command()` 与 `write_media()` 分离，前者是纯函数便于断言。

```
ffmpeg -v error -y -i IN [-i cover.jpg]
  -map 0 [-map -0:<old_cover_index>] [-map 1:v]
  -map_metadata 0 -c copy
  -metadata key=value ...
  [-id3v2_version 3]        # 仅 mp3
  [-movflags +faststart] [-brand <IN 的 major_brand>]   # 仅 mp4 系
  [-disposition:v:0 attached_pic]   # 仅写封面时
  OUT
```

关键点：

- `-map_metadata 0` 保证**未勾选的既有 tag 不被清掉**；只有 `-metadata key=`（空值）
  才删除该 tag。
- 替换封面时必须 `-map -0:<index>` 排除旧的 attached_pic 流，否则文件里会有两个封面。
  仅在 `cover_action` 是 SET 或 REMOVE 时排除；KEEP（未勾选）时旧封面必须原样保留。
  `COVER_KEEP / COVER_SET / COVER_REMOVE` 是三态，不要用布尔。
- `write_value` 语义：`None` = 不写，`''` = 删除，其他 = 写入。
  `compare()` 对 `STATUS_SAME` 强制置 `None`，否则空值会去删一个不存在的 tag。
- 原子性：写同目录临时文件 → `os.chmod` 复制权限 → `os.replace()`。
  ffmpeg 失败时 `finally` 清理临时文件，原文件不受影响（有测试覆盖）。
- **容器放不下的轨道要能跳过，不能整次失败**：`write_media()` 按
  「全保留 → 跳过认不出编码的流（`uncopyable_streams()`，来自 ffprobe 的 `streams`）
  → 只复制音频/视频（`-map 0:a? -map 0:v?`）」三次尝试，只在 stderr 命中
  `RETRYABLE_ERRORS`（`Could not write header` / `incompatible with output codec id` /
  `codec not currently supported in container` …）时才降级，磁盘满之类的错误照旧一次就报。
  成功了返回 `WriteOutcome`（str 子类，仍可当路径用）带 `skipped`，`action.py` 会把它
  写进结果对话框的详细信息。两种映射方式都要处理旧封面排除（`-map -0:<index>`），
  否则兜底时旧封面会留下来。
- **作者不按逗号拆分**（`split_authors` 只拆 `&` 和 `;`），因为 "Smith, John" 是一个人。
  写回用 ` & ` 连接，与读取的拆分符一致，保证往返对比稳定。
  标签则按 `;` `,` `&` 拆（`split_tags`），写回用 `; ` 连接并按 casefold 排序。
  **`canonical_*` 会 casefold，只用于比较；写入值必须保留原大小写。**
- 写完调 `db.refresh_format_sizes({(book_id, fmt): size})`
  （`db/cache.py:1447`，docstring 明说是给"文件被 calibre 外部修改导致尺寸过期"用的），
  再 `library_view.model().refresh_ids(changed)`。

批量写入用 `QProgressDialog` + `QTimer.start(0)` 逐个文件处理，照
`gui2/actions/embed.py:58-127` 的模式。**不要在定时器槽里调 `QApplication.processEvents()`**，
0 间隔定时器会重入。`read_entries()` 是普通循环，那里可以用 processEvents。

`read_entries()` 按 `book_id` 缓存 `lib_values()`，否则一本有 30 个章节文件的书
会读 30 次封面。

### 反向：文件 → 书库

入口是对话框底部、紧贴在「更新文件元数据」左边的「用文件中的元数据更新书库」按钮，
**不是**独立菜单项：calibre 一个 zip 只能注册一个界面动作（硬约束 1），加独立菜单项
就得再发一个插件，所以走了按钮这条路（`CompareDialog(db=...)`，测试里不传 db 时按钮禁用）。

底部那一行是自己拼的 `QHBoxLayout`（`self.button_row`：stretch + 按钮 + `self.bb`）——
**不要**改成 `self.bb.addButton(..., ActionRole)`：Qt 会把 ActionRole 排到 Ok **后面**，
而这里要求反方向按钮在「确定」左边。`self.bb` 的 Ok 按钮同时改成「更新文件元数据」。

- **两个方向共用同一套复选框**：`library_plan()` 取 `checked_fields(index)`，
  只写用户勾选的行。一本书有多个文件时，只要其中任一文件勾了该字段就会被合并进去。
- `book_updates(entry, fields)` 是纯函数：用 `metadata.library_values()` 取文件里的值，
  按 `fields` 过滤，再和 `entry['lib']` 逐字段比对（对日期、列表、ISBN 用和对比表相同的归一化）。
- **时长（自定义列）必须单独处理**：它不在 `library_values()` 里，`book_updates()` 要按
  `metadata.duration_value()` 算出列名和值，再和 `entry['lib']['duration']` 比
  （`lib` 里的键固定是 `'duration'`，不是 `'#duration'`）。
  漏了这一步的后果是：一本书如果只有时长不同，`library_plan()` 会把它整本跳过，表现为
  「点按钮之后时长没同步」。只有列真的存在于书库时才算这个差异（`action.lib_values()`
  用 `db.field_metadata` 判断），否则会报一个永远写不进去的字段。
- 写库用 `get_metadata()` → `merge_file_metadata(mi, media, fields)` → `set_metadata()`。
  **不要**用 `set_field` 逐字段写：`set_metadata` 会连自定义列（时长）一起处理，而且基于
  现有元数据对象合并，文件里没有的字段不会被清空。`merge_file_metadata()` 只碰 `fields`
  里的字段，封面和时长也一样受复选框控制。
- 写完置 `self.library_updated = True` 并 `reject()` 关掉对话框；
  `run_sync()` 看到这个标记就直接返回，**不会**接着往文件里写。别把这里改成 `accept()`。
- 反向更新只覆盖标准字段、封面、时长；自定义列目前只有「库 → 文件标签」方向。

## 构建与安装

```sh
python3 build.py                 # 编译翻译 + 覆盖率检查 + 打包到 dist/
```

Windows 上用 `py -3 build.py` 或 `python build.py`，输出一样：`dist/MediaMetadataReader.zip`
与 `dist/MediaMetadataSync.zip`。

`build.py` **只打包，不安装**——用户装的是 zip：calibre → 首选项 → 插件 → 从文件加载插件。
开发机上想省事，就用 calibre 自己的工具装（`calibre-customize` 在 portable 版里位于 calibre
的安装目录，不在 PATH 里时写全路径）。**一个 `--add-plugin` 只装一个 zip**，两个 zip 要跑两次：

```sh
calibre-customize --add-plugin dist/MediaMetadataReader.zip
calibre-customize --add-plugin dist/MediaMetadataSync.zip
# Windows 上写成 "D:\...\Calibre\calibre-customize.exe" --add-plugin dist\MediaMetadataReader.zip ...
```

`--add-plugin A.zip B.zip` 这种一次传两个路径的写法**不会报错，但只装第一个**（多余的位置
参数被忽略），很容易出现"装了一个插件、另一个静默缺失"，`tests/test_plugins.py` 会因此报
sync 插件缺失。

不要在 `build.py` 里加回安装逻辑（`--install`、`calibre-customize` 探测、直接拷到插件目录
都已删掉）：直接拷 zip 不会被 calibre 加载，而探测各平台安装路径只会带来平台分支和维护成本。

`build.py` 会把 `common/` 里的 `SHARED` 文件复制进每个 zip（插件之间不能跨 zip import），
所以 `common/` 是唯一权威副本，**不要手动往 `reader/` 或 `sync/` 里复制**。


## 翻译工作流

```sh
python3 tools/extract_po.py --counts   # AST 提取所有 msgid 及源码位置
python3 tools/po2mo.py translations/zh_CN.po   # 单独编译并回读校验
python3 build.py                       # 自动编译 + 报告未覆盖的字符串
```

系统没有 msgfmt 也没有 babel，`tools/po2mo.py` 直接按 .mo 二进制格式写，
写完用 `gettext.GNUTranslations` 回读校验。
`ffmpeg` / `ffprobe` 在 `build.py:NO_TRANSLATION` 里豁免（中英文相同）。
ngettext 的复数形式在 `.po` 里是 `msgid_plural`，`extract_po.py` 会单独列出，
覆盖率检查两边都算。

## 测试

```sh
python3 tests/make_samples.py                               # 用 ffmpeg 造样本到 tests/out/
python3 tests/test_ffutil.py                                # 三平台二进制定位逻辑（不需 ffmpeg）
python3 tests/test_platform.py                              # Windows 路径/子进程/原子写回（不需 ffmpeg）
python3 tests/test_ffmeta.py                                # 读写与对比（需真 ffmpeg）
calibre-debug tests/setup_library.py /tmp/media-lib-test    # 建临时书库 + #duration 列
calibre-debug tests/test_integration.py /tmp/media-lib-test # 改库→对比→写回全链路
calibre-debug tests/test_dialog.py /tmp/media-lib-test      # offscreen Qt 驱动 CompareDialog
calibre-debug tests/test_plugins.py                         # calibre 视角：注册、分发、可配置性、本地化
```

Windows 上（PowerShell）只是换了环境变量的写法和解释器名字：

```powershell
py -3 tests\make_samples.py
py -3 tests\test_ffutil.py
py -3 tests\test_platform.py
py -3 tests\test_ffmeta.py        # 需要 ffmpeg/ffprobe：set FFMPEG / FFPROBE 或放进 PATH
$env:QT_QPA_PLATFORM = 'offscreen'
calibre-debug tests\setup_library.py D:\tmp\media-lib-test
calibre-debug tests\test_integration.py D:\tmp\media-lib-test
calibre-debug tests\test_dialog.py D:\tmp\media-lib-test
calibre-debug tests\test_plugins.py
```

`test_dialog.py` / `test_plugins.py` 自己会在 `os.environ` 里补默认值，`QT_QPA_PLATFORM`
只是从外部再确认一次。offscreen 的 Qt 在 Windows 上会警告
`QFontDatabase: Cannot find font directory ...`，不影响结果，可以忽略。

`test_plugins.py` 要先装好插件才能跑（`python3 build.py` 打包后，用 GUI 的「Load plugin
from file」加载 `dist/` 的两个 zip 并重启 calibre；命令行装法见「构建与安装」），它检查的是
calibre 实际怎么看这两个插件（`initialized_plugins()`、`_metadata_readers` 分发表、
`is_customizable()`、`config_widget()` 能否构建、界面字符串是否已翻译）。

`test_ffutil.py` / `test_platform.py` / `test_ffmeta.py` 用系统 `python3` 跑；后三个必须用
`calibre-debug`（需要 `calibre_plugins.*` 可解析）。`test_platform.py` 把
`subprocess.run` 换成一个假 ffmpeg（照 `argv[-1]` 写文件），所以**不需要装 ffmpeg** 就能验证
`fetch → 临时文件 → os.replace` 这条链路和 `\\?\` 长路径前缀的处理。

Reader 的端到端验证不需要 GUI：`tests/setup_library.py` 已经在进程内跑了
`metadata_from_formats()`（calibredb 和「添加书籍」用的同一个分发器）并打印读到的字段，
所以跑一遍 setup_library 就能看出 reader 读到了什么。手工核对时才用 calibredb：

```sh
calibredb add --with-library /tmp/media-lib-test tests/out/tagged.mp3
calibredb list --with-library /tmp/media-lib-test -f template \
  --template "{title} | {authors} | {series} [{series_index}] | {tags} | {#duration}"
```

期望输出里有 `The Original Title | Original Author | Original Series [3] | Audiobook, Fiction | 2.0`，
`#duration` 有值说明时长列名和 datatype 配置都对。**注意 calibre GUI 开着时 calibredb 会
直接拒绝执行**，测试脚本因此不用它。

测试都是手写断言 + PASS/FAIL 打印，没有 pytest。
新增测试请沿用 `check()` / `eq()` 的风格并把函数加进 `main()` 的列表。

## Windows 验证

2026-11 在一台 Windows 11 机器上用 **calibre 9.17.0**（portable）+ **ffmpeg 2026-03
gyan.dev build** 实测过下面这些，全部通过；macOS 侧仍然是 calibre 9.18 + Homebrew ffmpeg：

| 覆盖的东西 | 手段 |
|---|---|
| 三平台二进制定位（含 `/opt/homebrew/bin` 优先） | `tests/test_ffutil.py` |
| `\\?\` / `\\?\UNC\` 前缀、`CREATE_NO_WINDOW`、UTF-8 解码、稀疏 argv | `tests/test_platform.py` |
| 临时文件 + `os.replace` 原子写、失败清理、封面增删 | `tests/test_platform.py`、`tests/test_ffmeta.py` |
| 真实 ffmpeg 读写 mp3/m4a/m4b/flac/mp4/mkv、封面替换不重复 | `tests/test_ffmeta.py` |
| 插件注册、扩展名分发、Customize 门禁、zh_CN 翻译 | `tests/test_plugins.py` |
| reader 端到端（`metadata_from_formats` → 书名/作者/系列/标签/时长进库） | `tests/setup_library.py` 的输出 |
| 标签只读写 `keywords`、`genre`/`grouping` 被忽略 | `tests/test_ffmeta.py`、`tests/test_integration.py` |
| 书库→文件写回、`refresh_format_sizes`、二次运行幂等 | `tests/test_integration.py` |
| 文件→书库（按钮、字段合并、时长、`library_updated`） | `tests/test_dialog.py` |
| `CompareDialog` 表格、勾选默认值、状态配色、`plan()` | `tests/test_dialog.py` |
| 显式 ffmpeg 路径（`D:\...\ffmpeg.exe`）被采纳、错路径不静默回退 | `tests/test_plugins.py` + 手工 |
| `build.py` 打包 + `calibre-customize --add-plugin` 注册两个 zip | `py -3 build.py` + `calibre-customize --add-plugin` |

Windows 上没覆盖的：wma/asf 的写标签（ffmpeg 支持本来就有限）、网络书库
（`\\server\share`）上的长路径、以及在非 portable 安装的 GUI 里手点「从文件加载插件」
（等价路径，走的是 calibre 自己的对话框）。

## 踩过的坑

改这个仓库前先看这里，每一条都真实发生过：

- **`Tag %s incompatible with output codec id '%d' (%s)` 是复用器在写头时拒绝某条轨道**
  （`libavformat/mux.c` 的 `validate_codec_tag()`）：输入里有一条 codec_tag 认得出、但当前
  ffmpeg 不认识的流，`-map 0 -c copy` 就会整次失败、一个文件都写不进去。真实来源是
  **ffmpeg 自己的章节文字轨**：`mov_create_chapter_track()` 会给每个带章节的 mp4/m4b 写一条
  handler 为 'text' 的轨（`nb_frames` 等于章节数），读回来是 codec `bin_data`（= 98314）、
  tag `'text'` 的 data 流；复用器没有 `bin_data` 的 tag 可写，所以任何 `-map 0` 重封装都会
  失败（用 `audiobook.cs` 生成的 m4b 就是这样，它自己的 `apply` 用 `-map 0:a` 规避）。
  所以 `write_media()` 必须能降级重试（见「写回命令」），不要直接把 `-map 0` 硬编码回去。
  章节本身由 `-map_chapters`（默认取第一个有章节的输入）重新写出来，不会丢。
- **mp4 的 `major_brand` 不是普通标签**，`-map_metadata 0` 带不过来：不显式传 `-brand`，
  重封装后 m4b 会从 `M4B ` 退回 `M4A `（Apple Books 就不再当有声书）。`technical_info()`
  把它读进 `tech['brand']`，`build_write_command()` 再作为 `-brand` 传回去。
- **`calibredb` 会以「另一个 calibre 程序正在运行」拒绝执行**（GUI 开着、甚至只是环境里有
  残留状态时都遇到过），所以 `tests/setup_library.py` 改走进程内 API：`Cache(DB())` 建库 →
  `metadata_from_formats()`（calibredb 用的同一个分发器，reader 插件照样被真正调用）→
  `create_book_entry()` + `add_format()`。只用 `calibredb` 做**手工**核对，不要把它写进
  测试脚本。
- **`question_dialog()` 没有 `show` 参数**（它本来就是模态的）：真实签名是
  `(parent, title, msg, det_msg='', show_copy_button=False, ...)`，只有
  `info_dialog()` / `error_dialog()` 才有 `show=False`。照着这两个的写法给
  `question_dialog` 加 `show=True` 会在点按钮时抛
  `TypeError: question_dialog() got an unexpected keyword argument 'show'`——
  `tests/test_dialog.py` 的桩函数现在照抄真实签名，正是为了让这类错误在测试里就炸出来。
- **`Metadata.get_user_metadata()` 的签名是 `(field, make_copy)`**，不是「拿全部自定义列」；
  `MetaInformation` 上也有同名方法。要拿/设某一列的值用 `mi.get('#col')` /
  `mi.set('#col', v)` + `mi.set_user_metadata('#col', {'datatype': ..., 'is_multiple': {}})`。
  `sync/dialog.py:merge_file_metadata()` 就是这样把时长合并进已有书的。
  （`get_all_user_metadata(make_copy)` 才是「拿全部」。）
- **`Cache(DB(path))` 直接构造不填充 `self.fields`**，`all_book_ids()` 会抛
  `KeyError: 'uuid'`；`Cache` 也没有 `initialize()` / `read_tables()`。
  要用 `from calibre.db.legacy import LibraryDatabase; db = LibraryDatabase(path).new_api`。
  注意 `calibre.library.database.LibraryDatabase` 是**另一个**类，构造函数要
  `metadata.db` 路径且没有 `new_api`，别搞混。
- `LibraryDatabase` 的 `create_custom_column` 之后，同进程内 `field_metadata` 是陈旧的，
  要重开一次才能看到新列。
- **启动期（`genesis()`）弹模态对话框会卡死 calibre**，而且连带导致之前写的 gprefs
  不落盘。别在 genesis 里弹任何模态框。
- **未保留 Python 引用的 `QAction` 会被 GC**，从 `menu.actions()` 里消失。
  写菜单相关测试时必须持有引用。
- `calibre-debug -c "..."` 的代码通过 exec 运行，**生成器表达式/推导式看不到 exec 的
  局部变量**（`NameError`）。用显式 for 循环代替。
- `calibre-debug -c` 里要用 `calibre_plugins.*`，必须先 import 一个 calibre 模块
  （例如 `from calibre.customize.ui import initialized_plugins`）触发插件 finder 注册。
- **Windows**：`subprocess` 必须带 `creationflags=CREATE_NO_WINDOW` 否则每次 probe 闪黑框；
  argv 永远以 list 传（路径含空格）；calibre 的 `make_long_path_useable` 会加 `\\?\`
  前缀而 ffmpeg 不认，`ffutil.strip_windows_long_path_prefix()` 负责剥掉（**`\\?\UNC\`
  是另一种形态**，要还原成 `\\server\share\...`，只切 4 个字符会得到 `UNC\server\...`）；
  ffprobe 输出要按 utf-8 解码否则中文 tag 乱码；`os.path.isabs('C:\\...')` 在 POSIX 上
  是 False，`ffutil._is_absolute()` 额外认盘符。
  子进程的 `stdin` 统一接 `DEVNULL`：GUI 版 calibre 可能给出无效的 stdin 句柄，某些
  ffmpeg build 会因此直接退出。`subprocess.run` 在 Windows 上还会抛 `OSError`
  （WinError 193，指向一个不能执行的文件），`run_tool()` 也把它转成 `ToolError`。
  以上每一条都有 Windows 真机验证，见上面的「Windows 验证」。
- Windows 的临时文件名要和 `MAX_PATH` 抢空间：`ffmeta.write_media()` 的临时文件用
  `.<书名前 24 字符>.<随机>.<ext>`，不要改成完整书名——深层书库里书名一长就越过 260。
- `tests/make_samples.py` 的 ogg 样本已经删掉：ffmpeg 在重编码音频时无法把封面以
  attached_pic 塞进 ogg，脚本只会留下一个空文件。测试也不需要 ogg 样本；要加回来前提是
  先确认能真的写出一个带封面的 ogg。
- `calibre-customize --list-plugins` 的输出是本地化的，grep 英文插件类型名会漏。
