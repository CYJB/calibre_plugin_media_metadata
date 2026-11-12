# calibre 音视频元数据插件

- [⤓ MediaMetadataReader.zip](https://github.com/CYJB/calibre_plugin_media_metadata/releases/download/latest/MediaMetadataReader.zip)
- [⤓ MediaMetadataSync.zip](https://github.com/CYJB/calibre_plugin_media_metadata/releases/download/latest/MediaMetadataSync.zip)

给 calibre 补上它原生缺失的音视频元数据能力。calibre 自带的元数据读写只覆盖电子书格式，
音频和视频文件既读不出内嵌标签，改完书籍信息也写不回去。这两个插件补的就是这块：

| 插件 | 作用 |
|---|---|
| **Read media metadata** | 添加音视频文件时，自动从内嵌标签读取书名、作者、系列、标签、简介、出版社、出版日期、ISBN、封面、时长 |
| **Sync media metadata** | 书籍列表右键菜单新增一项，弹窗对比「calibre 书库中的值」与「文件里已有的标签」，按勾选写回文件 |

支持的格式：mp3、m4a、m4b、mp4、m4v、mov、mkv、flac、ogg、opus、wav、aiff、wma、webm、avi。

界面跟随 calibre 的语言，当前提供简体中文。
**两个 zip 都要装**：前者负责「文件 → 书库」，后者负责「书库 → 文件」，它们的 ffmpeg
路径配置是共用的一份。

## 兼容性

| 平台 | 验证环境 | 状态 |
|---|---|---|
| macOS (Apple silicon) | calibre 9.18，Homebrew ffmpeg | 首发平台，全部测试通过 |
| Windows 10/11 64 位 | calibre 9.17.0，gyan.dev ffmpeg 2026-03 build | 全部测试通过（见 AGENTS.md 的「Windows 验证」） |

最低声明 calibre 7.0；更早的版本未验证。ffmpeg 与 ffprobe 由系统提供，插件不捆绑。
自动探测按平台走：macOS 先找 Apple silicon 的 `/opt/homebrew/bin`，再找 Intel 的
`/usr/local/bin`；Windows 找 PATH、`C:\ffmpeg\bin`、Program Files、WinGet、Chocolatey、
Scoop，以及解压到用户目录的副本。

## 安装

1. calibre — 首选项 — [高级选项] 插件 — **从文件加载插件**（右下角）— 选
   `MediaMetadataReader.zip`
2. 再用同样的方式装 `MediaMetadataSync.zip`
3. **重启 calibre**，右键菜单项才会出现

两个 zip 都在 [Releases](https://github.com/CYJB/calibre_plugin_media_metadata/releases/latest) 页面里。

从源码构建就用 `python3 build.py`（Windows 上 `py -3 build.py`），它**只打包**到 `dist/`，
安装还是走上面三步。

## 依赖

需要系统已安装 **ffmpeg 和 ffprobe**：

- macOS：`brew install ffmpeg`
- Windows：`winget install Gyan.FFmpeg`，或 `choco install ffmpeg`、`scoop install ffmpeg`，
  也可以直接解压官方 build 到 `%USERPROFILE%\ffmpeg\bin`
- Linux：`sudo apt install ffmpeg`

**以图形界面启动的 calibre 不继承 shell 的 PATH**（Windows 尤其明显，双击启动的
calibre 只看系统 PATH），所以自动探测失败时，请在 首选项 → 插件 → 任意一个插件 →
Customize 里手填绝对路径（旁边的 Test 按钮可以验证并显示版本）。两个插件共用这份路径
配置，填一次就够。Windows 上路径写 `D:\tools\ffmpeg\bin\ffmpeg.exe` 或
`D:/tools/ffmpeg/bin/ffmpeg.exe` 都可以。

## 用法一：添加时自动读取元数据

有三个 calibre 自身的前提，不满足会以为插件没生效：

1. **首选项 → 添加书籍 → 「从文件内容读取元数据」必须开启**（默认是开的）。
   关掉时 calibre 不会调用任何元数据读取插件。

2. **「添加书籍」对话框要切到 All files 过滤器。**
   默认过滤器只列电子书格式，选不到 mp3/mp4。

3. **拖拽、从文件夹添加、自动添加会被 calibre 的扩展名白名单挡掉。**
   解决办法：首选项 → 添加书籍 → 「过滤所添加文件的规则」里加 allow 规则，
   例如 `*.mp3`、`*.m4b`、`*.mp4`、`*.mkv`。

   在对话框里手动选中的文件不受这条限制，`calibredb add` 也不受限制。

### 哪些字段会被同步

| calibre 字段 | 从文件读取的标签 | 写回文件的标签 |
|---|---|---|
| 书名 | `title` | `title` |
| 作者 | `artist` / `album_artist` / `composer` | `artist` |
| 系列 | `album` | `album` |
| 系列索引 | `track`（`3/12` 取 3） | `track` |
| 标签 | `keywords` | `keywords` |
| 简介 | `comment` / `description` / `synopsis` | `comment` |
| 出版社 | `publisher` / `label` | `publisher` |
| 出版日期 | `date` / `year` | `date` |
| ISBN | `isbn` | `isbn` |
| 封面 | 内嵌的封面图（见下） | 内嵌封面图 |
| 时长 | 自动 | 不可写（由媒体流决定） |

几点行为说明：

- **作者不按逗号拆分**，因为 "Smith, John" 是一个人。多个作者用 `&` 或 `;` 分隔。
- **封面只认内嵌的封面图**，真实视频流不会被当成封面。所以给 mp4/mkv 视频文件
  不会把画面首帧塞进 calibre 的封面里——这类文件的封面行会显示「无封面」。
- **评分无法同步**：ffmpeg 没有对应的标准标签，对话框里会标为「不可写入」。

### 标签只读写 keywords

calibre 的「标签」是任意分类，而音频文件里的 `genre` 是**流派**、`grouping` 是内容分组，
都不是标签。所以插件**只认 `keywords` 这一个键**：

- **读**：只看 `keywords`，拆开后填进 calibre 的「标签」。
- **写**：calibre 的标签写进 `keywords`。
- `genre`、`grouping` 既不读也不写，`genre` 里原来的内容（例如 `Rock`）不会被动。
- 为什么是 `keywords`：实测 mp3 / m4a / flac / mkv 四种容器都能原样存取它；
  自定义键（例如 `calibre-tags`）在 m4a 会被 ffmpeg 悄悄丢掉。
- 标签值按 `;` `,` `&` 拆分、写回时用 `; ` 连接。

### 时长需要自建自定义列

calibre 没有内置的时长字段。要用时长：

1. 首选项 → 添加你自己的列，新建一列（例如检索名 `duration`，类型选**浮点数**）。
2. 首选项 → 插件 → Read media metadata → Customize，填 `#duration`，
   并把**数据类型选成和你刚建的列完全一致**。

类型不一致时 calibre 会静默忽略，时长列会是空的。列名留空则不处理时长。

配好之后，对比对话框里会多出一行「时长」：左边显示书库里的值、右边显示文件里的值。
两边不一样时状态是「仅同步回书库」——它只能往书库写，因为文件里的时长由媒体流本身决定；
两边一样时就和其它行一样显示「一致」。
没配时长列时，这一行退回成只读的信息行。

## 用法二：右键写回

书籍列表右键 → **对比并写入媒体元数据…**

对话框左侧按书籍分组，右侧是逐字段对比表：

```
Edited In Calibre
  ├ MP3
  ├ M4A
  └ M4B
A Video Lecture
  └ MP4
```

书名是分组标题（加粗、不可选中），子项只显示格式——因为 calibre 的文件名模板本来就是
「书名 - 作者.扩展名」，显示文件名会和分组标题重复。完整文件路径在鼠标悬停的提示里。
点击子项即切换右侧对比表。

### 状态含义

| 状态 | 含义 | 颜色 | 默认勾选 |
|---|---|---|---|
| 一致 | 两边一致 | 绿 | 否 |
| 不一致 | 两边不一致 | 橙（加粗） | **是** |
| 文件中缺失 | 书库里有、文件里没有 | 橙（加粗） | **是** |
| 书库中为空 | 书库里空、文件里有 | 橙（加粗） | 否（勾选会**清空**文件里的标签） |
| 仅同步回书库 | 时长这一行、且两边不一样：只能从文件写回书库，文件里没有可写的标签 | 蓝 | **是** |
| 不可写入 | 评分等无法写入的字段 | 灰 | 不可勾 |
| 信息 | 容器、视频流、音频流、比特率 | 灰 | 不可勾，仅供对照 |

只有状态列着色。深色和浅色主题各有一套配色。

**行顺序是固定的**：书名、作者、系列、系列索引、标签、简介、出版社、出版日期、ISBN、
评分、自定义列、封面、时长（仅同步回书库），最后是只读的技术信息（容器、视频流、
音频流、比特率）。其中「视频流」一行只在文件真的有视频流时才出现，所以音频文件比视频文件
少一行。书库里**没配时长列**时，时长会退回成只读的信息行（没有复选框）。

### 按钮

**全选 / 全不选 / 只选不一致** 只作用于当前选中的那个文件。
要把当前文件的勾选套用到所有文件，勾上右侧的 **将此选择应用到所有文件**。

底部一行从右往左是 **取消**、**更新文件元数据**（原来的「确定」）、
**用文件中的元数据更新书库** 三个按钮——两个方向并排但顺序分明，不容易点错。它们的行为是：

* **更新文件元数据**：把右侧勾选的字段写进当前书籍的媒体文件（库 → 文件）。
* **用文件中的元数据更新书库**：反方向，把媒体文件里的标签写进 calibre 书库
  （文件 → 库），**同样只写勾选的行**。文件里没有的字段保持书库原值；书库里原来有、
  文件里没有的字段也不会被清空。适合「文件被别的工具改过」或「书是早些时候加的、
  元数据没读全」的情况。点它会先弹确认，说明要改几本书、几个字段，确认后对话框关闭，
  不再写回文件。
* 时长行的复选框只影响「更新书库」：时长来自媒体流，写不进文件。

### 写回是安全的

- **不重编码**，只改标签，音视频数据原样拷贝。
- 未勾选的既有标签**原样保留**，不会被清掉。
- 写到同目录临时文件后原子替换。ffmpeg 失败时原文件不受影响，也不会留下半成品。
- mp3 默认写 ID3v2.3（兼容性最好），可在配置里改成 v2.4。
- 写完会自动更新 calibre 里记录的文件大小，书库信息不会过期。
- 有些文件带着目标容器放不下的轨道（例如带章节的 m4b 里那条 ffmpeg 自己写的章节文字轨，
  或者 mp4 里的 subrip 字幕），直接复制会让 ffmpeg 整次写头失败。遇到这种情况插件会自动
  换一种更保守的映射重试（先跳过认不出编码的流，再只复制音频/视频流），**其余元数据照常
  写入**，章节也仍在，跳过了哪些轨道会写在结果对话框的「详细信息」里。
- m4b 的 `major_brand` 会原样保留（它不在标签里，重封装时不显式带上的话会退回 `M4A `，
  Apple Books 就不再把它当有声书）。

大视频文件的写回需要几秒到几十秒（取决于文件大小），期间有进度条，可以取消。

菜单项的位置可以在 首选项 → 工具栏 → 「calibre 书库中书籍的右键菜单」里调整或移除；
插件配置里也能关掉自动注册。

## 配置项

**ffmpeg / ffprobe 路径由两个插件共用，在任意一个里配置一次即可，另一个会自动读到。**

**Read media metadata**：ffmpeg / ffprobe 路径（共用）、时长列名与数据类型。

**Sync media metadata**：ffmpeg / ffprobe 路径（共用）、mp3 的 ID3 版本、是否写封面、
是否自动加入右键菜单、自定义列 → 标签键映射表（例如 `#narrator` → `album_artist`）。

## 限制

- 依赖系统 ffmpeg/ffprobe。未安装时添加文件不会报错，只是读不出元数据；
  右键写回会明确提示。
- wma/asf 的标签写入 ffmpeg 支持有限，可能报不支持。
- **容器能存的标签种类不一样**：mp4/m4a/m4b 没有出版社标签。这类字段在对比表里会一直
  显示「一致」（两边都为空），属于容器限制，不是插件漏读。
- mkv 的封面以视频流形式写入，部分播放器不显示。
- **「文件 → 书库」只能从对比对话框里触发**（表格下方的按钮），右键菜单里没有独立菜单项：
  calibre 一个 zip 只能注册一个界面动作，独立菜单项就得再装一个插件。
- 反向更新是「文件优先」：文件里有的字段覆盖书库，文件里没有的字段不动。不提供逐字段勾选。
- 没有实现导出时自动写标签，所以「保存到磁盘 / 导出」不会带上标签。
- 路径长度超出 Windows 的 260 字符限制时，ffmpeg 自己会打不开文件（插件会剥掉 calibre
  加的 `\\?\` 长路径前缀再传给它）；把书库放在较短的路径下最稳。

---

开发者与 agent 请阅读 [AGENTS.md](AGENTS.md)：项目结构、calibre 插件机制的硬约束、
元数据读写链路、构建与翻译流程、测试方法、Windows 验证清单，以及已经踩过的坑。
