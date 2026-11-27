# Markdown 完全语法参考手册

> 本文档覆盖主流 Markdown 渲染器（GitHub、Typora、Obsidian、VSCode、VitePress、Pandoc）支持的**全部语法**。
> 每一节都是 **「语法源码 → 效果预览」** 两段式：先给你可以照抄的源码，再给出真实渲染后的样子。

![Markdown 横幅](assets/markdown-banner.jpg)

---

## 📑 目录

1. [标题 (Headings)](#1-标题-headings)
2. [段落与换行 (Paragraphs & Line Breaks)](#2-段落与换行-paragraphs--line-breaks)
3. [文本格式 (Text Formatting)](#3-文本格式-text-formatting)
4. [引用块 (Blockquotes)](#4-引用块-blockquotes)
5. [列表 (Lists)](#5-列表-lists)
6. [代码块 (Code Blocks)](#6-代码块-code-blocks)
7. [链接 (Links)](#7-链接-links)
8. [图片 (Images)](#8-图片-images)
9. [表格 (Tables)](#9-表格-tables)
10. [分隔线 (Horizontal Rules)](#10-分隔线-horizontal-rules)
11. [任务列表 (Task Lists)](#11-任务列表-task-lists)
12. [删除线 (Strikethrough)](#12-删除线-strikethrough)
13. [上标与下标 (Superscript & Subscript)](#13-上标与下标-superscript--subscript)
14. [高亮 (Highlight)](#14-高亮-highlight)
15. [键盘键与徽章 (Keyboard & Badges)](#15-键盘键与徽章-keyboard--badges)
16. [脚注 (Footnotes)](#16-脚注-footnotes)
17. [定义列表 (Definition Lists)](#17-定义列表-definition-lists)
18. [数学公式 (Math Equations)](#18-数学公式-math-equations)
19. [Mermaid 图表 (Mermaid Diagrams)](#19-mermaid-图表-mermaid-diagrams)
20. [目录与锚点 (TOC & Anchors)](#20-目录与锚点-toc--anchors)
21. [表情符号 (Emoji)](#21-表情符号-emoji)
22. [特殊代码块 (Special Code Blocks)](#22-特殊代码块-special-code-blocks)
23. [HTML 嵌入 (HTML Embedding)](#23-html-嵌入-html-embedding)
24. [特殊字符与转义 (Escaping)](#24-特殊字符与转义-escaping)
25. [常见陷阱 (Pitfalls)](#25-常见陷阱-pitfalls)
26. [兼容性对照表 (Compatibility Matrix)](#26-兼容性对照表-compatibility-matrix)
27. [一页速查 (Cheat Sheet)](#27-一页速查-cheat-sheet)

---

## 1. 标题 (Headings)

### 1.1 ATX 语法（推荐）

**语法：**

```markdown
# 一级标题 H1
## 二级标题 H2
### 三级标题 H3
#### 四级标题 H4
##### 五级标题 H5
###### 六级标题 H6
```

**效果预览：**

# 一级标题 H1
## 二级标题 H2
### 三级标题 H3
#### 四级标题 H4
##### 五级标题 H5
###### 六级标题 H6

### 1.2 标题内可嵌套其他格式

**语法：**

```markdown
# 带 **粗体** 和 `代码` 的标题
## 带 [链接](https://example.com) 的标题
### 带 🚀 emoji 的标题
```

**效果预览：**

# 带 **粗体** 和 `代码` 的标题
## 带 [链接](https://example.com) 的标题
### 带 🚀 emoji 的标题

### 1.3 标题前后必须空行

**语法：**

```markdown
# 标题

正文段落和标题之间要有一个空行，否则会被吞进标题里。
```

**效果预览：**

# 标题

正文段落和标题之间要有一个空行，否则会被吞进标题里。

---

## 2. 段落与换行 (Paragraphs & Line Breaks)

### 2.1 空行分段

**语法：**

```markdown
这是第一段。

这是第二段，与第一段之间空一行，就会被解析成两个独立段落。
```

**效果预览：**

这是第一段。

这是第二段，与第一段之间空一行，就会被解析成两个独立段落。

### 2.2 软换行（行尾两个空格）

**语法：**

```markdown
这一行末尾有两个空格（不可见）：  
所以下面这行会在这里换行。  
可以连续换很多行。
```

**效果预览：**

这一行末尾有两个空格（不可见）：  
所以下面这行会在这里换行。  
可以连续换很多行。

### 2.3 强制换行 `<br>`

**语法：**

```markdown
第一行<br>第二行<br>第三行
```

**效果预览：**

第一行<br>第二行<br>第三行

### 2.4 段落内混排

**语法：**

```markdown
段落里可以混排 **粗体**、`行内代码`、[链接](https://example.com)、<kbd>Ctrl</kbd>，
渲染后依然保持正常的行内流。
```

**效果预览：**

段落里可以混排 **粗体**、`行内代码`、[链接](https://example.com)、<kbd>Ctrl</kbd>，
渲染后依然保持正常的行内流。

---

## 3. 文本格式 (Text Formatting)

### 3.1 粗体 / 斜体 / 粗斜体

**语法：**

```markdown
*斜体* 或 _斜体_
**粗体** 或 __粗体__
***粗斜体*** 或 ___粗斜体___
```

**效果预览：**

*斜体* 或 _斜体_

**粗体** 或 __粗体__

***粗斜体*** 或 ___粗斜体___

### 3.2 嵌套组合

**语法：**

```markdown
**粗体里套 *斜体***
*斜体里套 **粗体***
***三者叠加***
```

**效果预览：**

**粗体里套 *斜体***

*斜体里套 **粗体***

***三者叠加***

### 3.3 强调符号的边界规则

**语法：**

```markdown
斜体*两侧有空格*不成对
snake_case_name 不变斜体（intraword 下划线不生效）
a*b*c 会被拆成 斜体a + b + 斜体c
2 * 3 = 6 中间的星号会变斜体
```

**效果预览：**

斜体*两侧有空格*不成对

snake_case_name 保持原样（intraword 下划线不生效）

a*b*c 会被拆成 斜体a + b + 斜体c

2 * 3 = 6 中间的星号会变斜体

> 💡 规则：`*`/`_` 左侧必须是标点或空白，右侧是标点、空白或文字，才构成强调。

---

## 4. 引用块 (Blockquotes)

### 4.1 基本引用与懒写法

**语法：**

```markdown
> 第一行引用
> 第二行仍属于同一个引用块

这是懒写法，下面这行没有 `>` 前缀，
> 也会被并入上面的引用块中。
```

**效果预览：**

> 第一行引用
> 第二行仍属于同一个引用块

这是懒写法，下面这行没有 `>` 前缀，
> 也会被并入上面的引用块中。

### 4.2 多级嵌套

**语法：**

```markdown
> 一级引用
>
> > 二级引用
> >
> > > 三级引用
```

**效果预览：**

> 一级引用
>
> > 二级引用
> >
> > > 三级引用

### 4.3 引用块内放列表、表格、代码

**语法：**

```markdown
> **引用块里的粗体**、`代码` 和 [链接](https://example.com)
>
> - 列表项 A
> - 列表项 B
>
> | 表头 1 | 表头 2 |
> | :--- | ---: |
> | 左 | 右 |
>
> ```python
> print("引用块里也能放代码块")
> ```
```

**效果预览：**

> **引用块里的粗体**、`代码` 和 [链接](https://example.com)
>
> - 列表项 A
> - 列表项 B
>
> | 表头 1 | 表头 2 |
> | :--- | ---: |
> | 左 | 右 |
>
> ```python
> print("引用块里也能放代码块")
> ```

### 4.4 GitHub Alert 提示块

**语法：**

```markdown
> [!NOTE]
> 这是补充说明。
```

**效果预览：**

> [!NOTE]
> 这是补充说明 Useful information that users should know.

> [!TIP]
> 这是实用技巧。

> [!IMPORTANT]
> 这是重要信息。

> [!WARNING]
> 这是警告。

> [!CAUTION]
> 这是危险操作提醒。

---

## 5. 列表 (Lists)

### 5.1 无序列表的三种符号

**语法：**

```markdown
- 连字符
* 星号
+ 加号

三者等价，但同一列表内请保持统一。
```

**效果预览：**

- 连字符
* 星号
+ 加号

三者等价，但同一列表内请保持统一。

### 5.2 有序列表

**语法：**

```markdown
1. 第一项
2. 第二项
3. 第三项
```

**效果预览：**

1. 第一项
2. 第二项
3. 第三项

全写 `1.` 也能被正确编号：

1. 第一项
1. 第二项
1. 第三项

### 5.3 嵌套列表

**语法：**

```markdown
- 顶层项
    - 第二层
        - 第三层
- 另一个顶层

1. 有序顶层
    1. 有序第二层
        1. 有序第三层
```

**效果预览：**

- 顶层项
    - 第二层
        - 第三层
- 另一个顶层

1. 有序顶层
    1. 有序第二层
        1. 有序第三层

### 5.4 紧凑列表 vs 松散列表

**语法（紧凑）：**

```markdown
- 项一
- 项二
- 项三
```

**效果预览（紧凑，渲染时不套 `<p>`）：**

- 项一
- 项二
- 项三

**语法（松散）：**

```markdown
- 项一

- 项二

- 项三
```

**效果预览（松散，每项独立成段，可以放块级元素）：**

- 项一

- 项二

- 项三

### 5.5 列表项内放多段落和代码块

**语法：**

```markdown
1. 第一段内容。

   这是同一列表项的第二段（缩进 4 空格续接）。

2. 列表项里可以放代码块：

   ```js
   console.log("在列表中");
   ```

3. 列表项里可以放嵌套列表：
    - 子项 A
    - 子项 B
```

**效果预览：**

1. 第一段内容。

   这是同一列表项的第二段（缩进 4 空格续接）。

2. 列表项里可以放代码块：

   ```js
   console.log("在列表中");
   ```

3. 列表项里可以放嵌套列表：
    - 子项 A
    - 子项 B

---

## 6. 代码块 (Code Blocks)

### 6.1 行内代码

**语法：**

```markdown
安装命令是 `npm install`，函数是 `Array.prototype.map()`。
内容含反引号时用双反引号：`` 这叫 `反引号` ``
```

**效果预览：**

安装命令是 `npm install`，函数是 `Array.prototype.map()`。

内容含反引号时用双反引号：`` 这叫 `反引号` ``

### 6.2 围栏代码块与语法高亮

**语法：**

````markdown
```python
def greet(name: str) -> None:
    print(f"Hello, {name}!")
```
````

**效果预览：**

```python
def greet(name: str) -> None:
    print(f"Hello, {name}!")
```

```javascript
const nums = [1, 2, 3, 4, 5];

const evens = nums.filter((n) => n % 2 === 0);
const total = nums.reduce((acc, n) => acc + n, 0);

console.log(evens, total); // [2, 4] 15
```

```sql
SELECT u.name, COUNT(o.id) AS orders, SUM(o.total) AS spent
FROM users u
LEFT JOIN orders o ON o.user_id = u.id
WHERE o.created_at >= '2025-01-01'
GROUP BY u.id, u.name
HAVING COUNT(o.id) > 5
ORDER BY spent DESC
LIMIT 10;
```

```bash
# 常用命令速记
find . -name "*.log" -mtime +7 -delete   # 删除 7 天前的日志
tar -czvf backup.tar.gz ./data          # 打包压缩
grep -rn "TODO" --include="*.py" .      # 全局搜索
curl -sSL https://example.com | jq .    # 请求并格式化 JSON
```

```yaml
server:
  port: 8080
  host: 0.0.0.0
database:
  driver: postgres
  dsn: "postgres://user:pass@localhost:5432/app"
```

### 6.3 波浪号围栏 `~~~`

**语法：**

````markdown
~~~markdown
代码内容里可以直接写反引号 ` 和三个反引号 ``` 都不需要转义。
~~~
````

**效果预览：**

~~~markdown
代码内容里可以直接写反引号 ` 和三个反引号 ``` 都不需要转义。
~~~

### 6.4 常用语言标识符

**效果预览：**

| 分类 | 常用标识符 |
| :--- | :--- |
| 前端 | `javascript` `js` `jsx` `typescript` `ts` `tsx` `vue` `svelte` `html` `xml` `css` `scss` `less` `json` |
| 后端 | `java` `kotlin` `c` `cpp` `csharp` `go` `rust` `ruby` `php` `swift` `scala` |
| 脚本 | `python` `bash` `sh` `zsh` `shell` `powershell` `ps1` `bat` `lua` `perl` `r` |
| 数据配置 | `yaml` `yml` `toml` `ini` `properties` `env` `csv` `graphql` `prisma` |
| 数据库 | `sql` `mysql` `postgresql` `plsql` |
| 运维构建 | `dockerfile` `makefile` `cmake` `nginx` `terraform` `hcl` `k8s` |
| 文档 | `markdown` `md` `mdx` `latex` `tex` `bibtex` `rst` `adoc` |
| 差异文本 | `diff` `patch` `text` `plain` `txt` `log` |

### 6.5 缩进代码块（不推荐）

**语法：**

```markdown
    这是一个缩进代码块
    每行缩进 4 个空格或 1 个 Tab
    优先级最低，容易和列表混淆
```

**效果预览：**

    这是一个缩进代码块
    每行缩进 4 个空格或 1 个 Tab
    优先级最低，容易和列表混淆

### 6.6 嵌套围栏（在文档里展示代码块）

**语法：**

`````markdown
````markdown
外层用四个反引号，内层的三个反引号就变成普通内容：
```js
console.log("nested");
```
````
`````

**效果预览：**

````markdown
```js
console.log("nested");
```
````

---

## 7. 链接 (Links)

### 7.1 行内链接

**语法：**

```markdown
[CommonMark 规范](https://spec.commonmark.org/)
[带悬停标题的链接](https://example.com "鼠标悬停显示这段文字")
[空链接]()
```

**效果预览：**

[CommonMark 规范](https://spec.commonmark.org/)

[带悬停标题的链接](https://example.com "鼠标悬停显示这段文字")

[空链接]()

### 7.2 引用式链接

**语法：**

```markdown
这是对 [GitHub][gh] 和 [微软][ms] 的引用，还有 [简写][] 形式。

[gh]: https://github.com
[ms]: https://microsoft.com "Microsoft 官网"
[简写]: https://example.com
```

**效果预览：**

这是对 [GitHub][gh] 和 [微软][ms] 的引用，还有 [简写][] 形式。

[gh]: https://github.com
[ms]: https://microsoft.com "Microsoft 官网"
[简写]: https://example.com

### 7.3 自动链接

**语法：**

```markdown
<https://example.com>
<user@example.com>
```

**效果预览：**

<https://example.com>

<user@example.com>

### 7.4 相对路径与邮件链接

**语法：**

```markdown
[打开同目录文件](./Markdown-Syntax-Reference.md)
[回到本文开头](#1-标题-headings)
[发邮件](mailto:hello@example.com?subject=Hi)
```

**效果预览：**

[打开同目录文件](./Markdown-Syntax-Reference.md)

[回到本文开头](#1-标题-headings)

[发邮件](mailto:hello@example.com?subject=Hi)

### 7.5 锚点命名规则

**规则：**

| 步骤 | 示例 | 结果 |
| :-: | :--- | :--- |
| 1. 转小写 | `## My Heading` | `my heading` |
| 2. 删除标点 | `### A/B & C` | `ab c` |
| 3. 空格转连字符 | `## 二级 标题` | `二级-标题` |
| 4. 保留中文 | `## 中文标题` | `中文标题` |

**效果预览（本文档的真实锚点跳转）：**

[跳转到 Mermaid 章节](#19-mermaid-图表-mermaid-diagrams) ·
[跳转到数学公式章节](#18-数学公式-math-equations) ·
[跳转到 HTML 章节](#23-html-嵌入-html-embedding)

---

## 8. 图片 (Images)

### 8.1 本地图片

**语法：**

```markdown
![Markdown 横幅](assets/markdown-banner.jpg)
![带悬停标题](assets/markdown-banner.jpg "鼠标悬停显示这段文字")
```

**效果预览：**

![Markdown 横幅](assets/markdown-banner.jpg)

![带悬停标题](assets/markdown-banner.jpg "鼠标悬停显示这段文字")

### 8.2 网络图片

**语法：**

```markdown
![GitHub Logo](https://github.githubassets.com/images/modules/logos_page/GitHub-Mark.png)
```

**效果预览：**

![GitHub Logo](https://github.githubassets.com/images/modules/logos_page/GitHub-Mark.png)

### 8.3 引用式图片

**语法：**

```markdown
![替代文字][banner]

[banner]: assets/markdown-banner.jpg "可选标题"
```

**效果预览：**

![替代文字][banner]

[banner]: assets/markdown-banner.jpg "可选标题"

### 8.4 图片加链接

**语法：**

```markdown
[![点击跳转 GitHub](assets/markdown-banner.jpg)](https://github.com)
```

**效果预览：**

[![点击跳转 GitHub](assets/markdown-banner.jpg)](https://github.com)

### 8.5 指定尺寸与对齐

**语法：**

```markdown
<img src="assets/markdown-banner.jpg" width="260" alt="指定宽度" />
```

**效果预览：**

<img src="assets/markdown-banner.jpg" width="260" alt="指定宽度" />

<p align="center">
  <img src="assets/markdown-banner.jpg" width="360" alt="居中显示" />
</p>

> 💡 `alt` 文本在图片加载失败时显示，也是读屏软件朗读的内容，**请务必填写**。

---

## 9. 表格 (Tables)

### 9.1 标准表格与对齐

**语法：**

```markdown
| 左对齐 | 居中 | 右对齐 |
| :----- | :--: | ----: |
| a | b | c |
| 更长的内容会撑开列宽 | 中 | 右对齐 |
```

**效果预览：**

| 左对齐 | 居中 | 右对齐 |
| :----- | :--: | ----: |
| a | b | c |
| 更长的内容会撑开列宽 | 中 | 右对齐 |

### 9.2 单元格内支持行内格式

**语法：**

```markdown
| 类型 | 示例 | 说明 |
| ---- | ---- | ---- |
| 粗体 | **粗体** | 双星号 |
| 斜体 | *斜体* | 单星号 |
| 删除线 | ~~删除线~~ | 双波浪号 |
| 代码 | `code` | 反引号 |
| 链接 | [链接](https://example.com) | 方括号 + 圆括号 |
| 图片 | ![img](assets/markdown-banner.jpg) | 感叹号开头 |
| 公式 | $a^2+b^2=c^2$ | 美元符号 |
```

**效果预览：**

| 类型 | 示例 | 说明 |
| ---- | ---- | ---- |
| 粗体 | **粗体** | 双星号 |
| 斜体 | *斜体* | 单星号 |
| 删除线 | ~~删除线~~ | 双波浪号 |
| 代码 | `code` | 反引号 |
| 链接 | [链接](https://example.com) | 方括号 + 圆括号 |
| 图片 | ![img](assets/markdown-banner.jpg) | 感叹号开头 |
| 公式 | $a^2+b^2=c^2$ | 美元符号 |

### 9.3 转义竖线与缺失单元格

**语法：**

```markdown
| 键 | 值 |
| -- | -- |
| `a \| b` | 反斜杠转义 |
| `a &#124; b` | HTML 实体转义 |
```

**效果预览：**

| 键 | 值 |
| -- | -- |
| `a \| b` | 反斜杠转义 |
| `a &#124; b` | HTML 实体转义 |

**语法（缺失单元格）：**

```markdown
| A | B | C |
| - | - | - |
| 1 | 2 | 3 |
| 4 |
| 5 | 6 |
```

**效果预览：**

| A | B | C |
| - | - | - |
| 1 | 2 | 3 |
| 4 |
| 5 | 6 |

### 9.4 单元格内换行

**语法：**

```markdown
| 特性 | 说明 |
| :--- | :--- |
| 第一行<br>第二行<br>第三行 | 用 `<br>` 实现换行 |
```

**效果预览：**

| 特性 | 说明 |
| :--- | :--- |
| 第一行<br>第二行<br>第三行 | 用 `<br>` 实现换行 |


---

## 10. 分隔线 (Horizontal Rules)

### 10.1 三种等价写法

**语法：**

```markdown
---

***

___
```

**效果预览（三条都是同一种横线）：**

---

***

___

### 10.2 前后必须留空行

**语法：**

```markdown
这是段落。

---

这是另一个段落。
```

**效果预览：**

这是段落。

---

这是另一个段落。

> ⚠️ 分隔线前后如果没有空行，会被当作 Setext 标题的下划线。

---

## 11. 任务列表 (Task Lists)

### 11.1 基本用法

**语法：**

```markdown
- [x] 已完成
- [ ] 未完成
- [X] 大写 X 同样算完成
```

**效果预览：**

- [x] 已完成
- [ ] 未完成
- [X] 大写 X 同样算完成

### 11.2 嵌套与格式化

**语法：**

```markdown
- [x] **父任务：初始化项目**
    - [x] 创建 `package.json`
    - [x] 安装依赖
    - [ ] 配置 ESLint（进行中）
- [ ] 待办：编写 [文档](https://example.com)
```

**效果预览：**

- [x] **父任务：初始化项目**
    - [x] 创建 `package.json`
    - [x] 安装依赖
    - [ ] 配置 ESLint（进行中）
- [ ] 待办：编写 [文档](https://example.com)

### 11.3 有序任务列表

**语法：**

```markdown
1. [x] 第一步完成
2. [ ] 第二步未完成
3. [ ] 第三步未完成
```

**效果预览：**

1. [x] 第一步完成
2. [ ] 第二步未完成
3. [ ] 第三步未完成

---

## 12. 删除线 (Strikethrough)

### 12.1 基本用法

**语法：**

```markdown
~~这段文字被删除~~
价格：~~原价 ¥999~~ **现价 ¥199**
```

**效果预览：**

~~这段文字被删除~~

价格：~~原价 ¥999~~ **现价 ¥199**

### 12.2 与其他格式组合

**语法：**

```markdown
~~**加粗的删除线**~~
~~*斜体* 和 `代码` 一起删~~
```

**效果预览：**

~~**加粗的删除线**~~

~~*斜体* 和 `代码` 一起删~~

---

## 13. 上标与下标 (Superscript & Subscript)

### 13.1 HTML 标签（通用可靠）

**语法：**

```markdown
水的化学式：H<sub>2</sub>O
氢同位素：<sup>1</sup>H
勾股定理：a<sup>2</sup> + b<sup>2</sup> = c<sup>2</sup>
```

**效果预览：**

水的化学式：H<sub>2</sub>O

氢同位素：<sup>1</sup>H

勾股定理：a<sup>2</sup> + b<sup>2</sup> = c<sup>2</sup>

### 13.2 渲染器原生语法对照

| 渲染器 | 上标 | 下标 | 效果预览 |
| :--- | :--- | :--- | :--- |
| GitHub | `<sup>x</sup>` | `<sub>x</sub>` | H<sub>2</sub>O |
| Typora | `x^2^` | `x~2~` | H~2~O |
| Obsidian | `x^2^` | `x~2~` | H~2~O |
| Pandoc | `x^2^` | `x~2~` | H~2~O |

**效果预览（Typora / Obsidian / Pandoc 语法）：**

H~2~O 和 x^2^ + y^2^ = z^2^

---

## 14. 高亮 (Highlight)

### 14.1 `==` 语法

**语法：**

```markdown
==这里是高亮文本==
普通文字 ==高亮部分== 普通文字
```

**效果预览：**

==这里是高亮文本==

普通文字 ==高亮部分== 普通文字

### 14.2 HTML `<mark>` 替代方案

**语法：**

```markdown
<mark>黄色高亮（HTML）</mark>
<span style="background-color:#86efac;color:#14532d;padding:2px 8px;border-radius:4px;">绿色高亮</span>
```

**效果预览：**

<mark>黄色高亮（HTML）</mark>

<span style="background-color:#86efac;color:#14532d;padding:2px 8px;border-radius:4px;">绿色高亮</span>

---

## 15. 键盘键与徽章 (Keyboard & Badges)

### 15.1 `<kbd>` 键盘键

**语法：**

```markdown
<kbd>Ctrl</kbd> + <kbd>C</kbd> 复制
<kbd>⌘</kbd> + <kbd>Shift</kbd> + <kbd>P</kbd> 命令面板
```

**效果预览：**

<kbd>Ctrl</kbd> + <kbd>C</kbd> 复制

<kbd>Ctrl</kbd> + <kbd>V</kbd> 粘贴

<kbd>Ctrl</kbd> + <kbd>Alt</kbd> + <kbd>Del</kbd> 删除行

<kbd>⌘</kbd> + <kbd>Shift</kbd> + <kbd>P</kbd> 命令面板

<kbd>Esc</kbd> 退出

### 15.2 Shields.io 徽章

**语法：**

```markdown
![Build](https://img.shields.io/badge/Build-passing-brightgreen)
![License](https://img.shields.io/badge/License-MIT-blue)
```

**效果预览：**

![Build](https://img.shields.io/badge/Build-passing-brightgreen)
![Version](https://img.shields.io/badge/Version-1.0.0-blue)
![License](https://img.shields.io/badge/License-MIT-green)
![Stars](https://img.shields.io/badge/Stars-%E2%98%85-orange)

---

## 16. 脚注 (Footnotes)

### 16.1 基本脚注

**语法：**

```markdown
这段文字带脚注[^1]，还有命名脚注[^note]。

[^1]: 第一个脚注的定义。
[^note]: 第二个脚注，支持 **格式化**、`代码` 和 [链接](https://example.com)。
```

**效果预览：**

这段文字带脚注[^1]，还有命名脚注[^note]。

[^1]: 第一个脚注的定义。

[^note]: 第二个脚注，支持 **格式化**、`代码` 和 [链接](https://example.com)。

### 16.2 多段落脚注

**语法：**

```markdown
带多段落和列表的脚注[^long]。

[^long]: 脚注第一段。

    脚注第二段，缩进 4 空格。

    脚注里也能放列表：

    - 项目 1
    - 项目 2
```

**效果预览：**

带多段落和列表的脚注[^long]。

[^long]: 脚注第一段。

    脚注第二段，缩进 4 空格。

    脚注里也能放列表：

    - 项目 1
    - 项目 2

---

## 17. 定义列表 (Definition Lists)

### 17.1 基本用法

**语法：**

```markdown
术语 1
:   术语 1 的定义说明。

术语 2
:   术语 2 的第一个定义。
:   术语 2 的第二个定义。
```

**效果预览：**

术语 1
:   术语 1 的定义说明。

术语 2
:   术语 2 的第一个定义。
:   术语 2 的第二个定义。

> ⚠️ 定义列表**不是 GFM 标准**，Pandoc / Typora / Obsidian 支持，GitHub 会渲染成普通段落。

### 17.2 通用替代方案（表格）

**语法：**

```markdown
| 术语 | 定义 |
| :--- | :--- |
| 术语 1 | 术语 1 的定义说明。 |
| 术语 2 | 定义一。<br>定义二。 |
```

**效果预览：**

| 术语 | 定义 |
| :--- | :--- |
| 术语 1 | 术语 1 的定义说明。 |
| 术语 2 | 定义一。<br>定义二。 |

---

## 18. 数学公式 (Math Equations)

> 由 KaTeX / MathJax 渲染。

### 18.1 行内公式

**语法：**

```markdown
质能方程是 $E = mc^2$，光速 $c \approx 3 \times 10^8$ m/s。
其他例子：$\alpha + \beta = \gamma$、$\sum_{i=1}^n x_i$、$\forall x \in \mathbb{R},\ x^2 \ge 0$。
```

**效果预览：**

质能方程是 $E = mc^2$，光速 $c \approx 3 \times 10^8$ m/s。

其他例子：$\alpha + \beta = \gamma$、$\sum_{i=1}^n x_i$、$\forall x \in \mathbb{R},\ x^2 \ge 0$。

### 18.2 块级公式

**语法：**

```markdown
$$
\begin{aligned}
f(x) &= \int_{-\infty}^{\infty} \hat{f}(\xi)\, e^{2\pi i \xi x}\, d\xi \\
\hat{f}(\xi) &= \int_{-\infty}^{\infty} f(x)\, e^{-2\pi i \xi x}\, dx
\end{aligned}
$$
```

**效果预览：**

$$
\begin{aligned}
f(x) &= \int_{-\infty}^{\infty} \hat{f}(\xi)\, e^{2\pi i \xi x}\, d\xi \\
\hat{f}(\xi) &= \int_{-\infty}^{\infty} f(x)\, e^{-2\pi i \xi x}\, dx
\end{aligned}
$$

### 18.3 标注命令

**语法：**

```markdown
$$
\underbrace{a + b + c}_{\text{下标说明}}
\qquad
\overbrace{x^2 + y^2}^{\text{上标说明}}
\qquad
\overrightarrow{AB}
\qquad
\boxed{E = mc^2}
$$
```

**效果预览：**

$$
\underbrace{a + b + c}_{\text{下标说明}}
\qquad
\overbrace{x^2 + y^2}^{\text{上标说明}}
\qquad
\overrightarrow{AB}
\qquad
\boxed{E = mc^2}
$$

### 18.4 集合与逻辑符号

**语法：**

```markdown
$$
A \cup B \subseteq \mathbb{N} \subset \mathbb{R} \in \mathbb{C}
\qquad
A \cap B = \varnothing \subseteq A
$$
```

**效果预览：**

$$
A \cup B \subseteq \mathbb{N} \subset \mathbb{R} \in \mathbb{C}
\qquad
A \cap B = \varnothing \subseteq A
$$

### 18.5 矩阵与方程组

**语法：**

```markdown
$$
\begin{bmatrix} a & b \\ c & d \end{bmatrix}
=
\begin{pmatrix} a & b \\ c & d \end{pmatrix}
=
\begin{vmatrix} a & b \\ c & d \end{vmatrix}
$$
```

**效果预览：**

$$
\begin{bmatrix} a & b \\ c & d \end{bmatrix}
=
\begin{pmatrix} a & b \\ c & d \end{pmatrix}
=
\begin{vmatrix} a & b \\ c & d \end{vmatrix}
$$

$$
\begin{cases}
2x + y = 5 \\
x - y = 1
\end{cases}
\quad \Rightarrow \quad
x = 2,\; y = 1
$$

### 18.6 求和、求积、极限、微分

**语法：**

```markdown
$$
\sum_{i=1}^{n} i = \frac{n(n+1)}{2}, \qquad
\prod_{i=1}^{n} i = n!
$$
```

**效果预览：**

$$
\sum_{i=1}^{n} i = \frac{n(n+1)}{2},
\qquad
\prod_{i=1}^{n} i = n!,
\qquad
\lim_{n \to \infty} \left(1 + \frac{1}{n}\right)^{n} = e
$$

$$
\frac{\partial^2 u}{\partial x^2} + \frac{\partial^2 u}{\partial y^2} = 0,
\qquad
\int_a^b f(x)\,dx \leq \int_a^b g(x)\,dx
$$

### 18.7 根式与组合数

**语法：**

```markdown
$$
\sqrt{x^2 + y^2},
\qquad
\frac{\binom{n}{k}}{\binom{n-1}{k-1}} = \frac{n}{k}
$$
```

**效果预览：**

$$
\sqrt{x^2 + y^2},
\qquad
\sqrt[3]{x^3 + y^3 + z^3},
\qquad
\frac{\binom{n}{k}}{\binom{n-1}{k-1}} = \frac{n}{k}
$$

### 18.8 三角函数与复数

**语法：**

```markdown
$$
\sin\theta + \cos\theta = \sqrt{2}\sin\left(\theta + \frac{\pi}{4}\right)
$$
```

**效果预览：**

$$
\sin\theta + \cos\theta = \sqrt{2}\sin\left(\theta + \frac{\pi}{4}\right),
\qquad
e^{i\pi} + 1 = 0,
\qquad
\overline{z} = a - bi
$$

### 18.9 在代码块里写公式（不渲染）

**语法：**

````markdown
```markdown
行内：$E = mc^2$

块级：
$$
a^2 + b^2 = c^2
$$
```
````

**效果预览：**

```markdown
行内：$E = mc^2$

块级：
$$
a^2 + b^2 = c^2
$$
```

---

## 19. Mermaid 图表 (Mermaid Diagrams)

### 19.1 流程图与方向控制

**语法：**

````markdown
```mermaid
graph TD
    A[开始] --> B{判断条件?}
    B -->|是| C[执行操作]
    B -->|否| D[其他操作]
    C --> E[结束]
    D --> E
```
````

**效果预览：**

```mermaid
graph TD
    A[开始] --> B{判断条件?}
    B -->|是| C[执行操作]
    B -->|否| D[其他操作]
    C --> E[结束]
    D --> E
```

**语法（横向布局）：**

````markdown
```mermaid
graph LR
    A[输入] --> B[处理] --> C{校验}
    C -->|通过| D[(数据库)]
    C -->|失败| E[错误日志]
```
````

**效果预览：**

```mermaid
graph LR
    A[输入] --> B[处理] --> C{校验}
    C -->|通过| D[(数据库)]
    C -->|失败| E[错误日志]
    D --> F[输出]
    E --> F
```

方向关键字：`TD`/`TB`（上→下）、`BT`（下→上）、`LR`（左→右）、`RL`（右→左）。

### 19.2 节点形状全集

**语法：**

````markdown
```mermaid
graph TD
    A[矩形] --> B(圆角)
    B --> C([体育场形])
    C --> D[[子程序]]
    D --> E[(数据库)]
    E --> F((圆形))
    F --> G>菱形]
    G --> H{{六边形}}
    H --> I[/平行四边形/]
    I --> J[\梯形\]
```
````

**效果预览：**

```mermaid
graph TD
    A[矩形] --> B(圆角)
    B --> C([体育场形])
    C --> D[[子程序]]
    D --> E[(数据库)]
    E --> F((圆形))
    F --> G>菱形]
    G --> H{{六边形}}
    H --> I[/平行四边形/]
    I --> J[\梯形\]
    J --> K[/矩形/]
```

| 写法 | 形状 |
| :--- | :--- |
| `A[文本]` | 矩形 |
| `A(文本)` | 圆角矩形 |
| `A([文本])` | 体育场形 |
| `A[[文本]]` | 子程序 |
| `A[(文本)]` | 数据库 |
| `A((文本))` | 圆形 |
| `A>文本]` | 菱形 |
| `A{{文本}}` | 六边形 |
| `A[/文本/]` | 平行四边形 |
| `A[\文本\]` | 梯形 |

### 19.3 时序图

**语法：**

````markdown
```mermaid
sequenceDiagram
    participant 用户
    participant 前端
    participant 后端
    用户->>前端: 发起请求
    前端->>后端: API 调用
    后端-->>前端: 响应
    前端-->>用户: 渲染页面
```
````

**效果预览：**

```mermaid
sequenceDiagram
    autonumber
    participant U as 用户
    participant F as 前端
    participant B as 后端
    participant DB as 数据库
    U->>F: 发起请求
    F->>B: POST /api/orders
    B->>DB: SELECT * FROM orders
    DB-->>B: 返回数据行
    B-->>F: 200 OK {json}
    F-->>U: 渲染订单列表
```

消息类型：`->>` 实线箭头、`-->>` 虚线箭头、`-)` 实线空心、`-->)` 虚线空心。

### 19.4 时序图的控制块

**语法：**

````markdown
```mermaid
sequenceDiagram
    loop 轮询
        C->>S: GET /health
    end
    alt 成功
        S-->>C: up
    else 失败
        S-->>C: down
    end
```
````

**效果预览：**

```mermaid
sequenceDiagram
    participant C as 客户端
    participant S as 服务端
    loop 每秒轮询
        C->>S: GET /health
        S-->>C: 200 OK
    end
    alt 健康检查通过
        S-->>C: status: up
    else 服务异常
        S-->>C: status: down
    end
    opt 携带 Token
        Note right of C: Authorization: Bearer xxx
    end
```

### 19.5 类图

**语法：**

````markdown
```mermaid
classDiagram
    class Animal {
        +String name
        +eat()
    }
    Animal <|-- Dog
```
````

**效果预览：**

```mermaid
classDiagram
    direction TB
    class Shape {
        <<abstract>>
        +String name
        +area() double
    }
    class Circle {
        +double radius
        +area() double
    }
    class Rectangle {
        +double width
        +double height
        +area() double
    }
    Shape <|-- Circle
    Shape <|-- Rectangle
    class Animal {
        +String name
        +int age
        +eat()
    }
    class Dog {
        +String breed
        +bark()
    }
    Animal <|-- Dog
```

关系符号：`<|--` 继承、`*--` 组合、`o--` 聚合、`-->` 关联、`..>` 依赖、`..|>` 实现。

### 19.6 状态图

**语法：**

````markdown
```mermaid
stateDiagram-v2
    [*] --> 待处理
    待处理 --> 处理中: 接收
    处理中 --> 已完成: 成功
    处理中 --> 失败: 异常
```
````

**效果预览：**

```mermaid
stateDiagram-v2
    direction LR
    [*] --> Idle
    Idle --> Running: start
    Running --> Paused: pause
    Paused --> Running: resume
    Running --> Success: finish
    Running --> Failed: error
    Failed --> Running: retry
    Success --> [*]
    Idle --> [*]: cancel
```

### 19.7 甘特图

**语法：**

````markdown
```mermaid
gantt
    title 项目计划
    dateFormat YYYY-MM-DD
    section 开发
    需求分析 :a1, 2025-01-01, 10d
```
````

**效果预览：**

```mermaid
gantt
    title 项目开发计划
    dateFormat YYYY-MM-DD
    axisFormat %m/%d
    section 设计阶段
    需求分析           :done, a1, 2025-01-01, 10d
    UI 设计            :active, a2, after a1, 8d
    section 开发阶段
    后端开发           :b1, after a2, 15d
    前端开发           :b2, after a2, 15d
    联调               :crit, b3, after b1, 5d
    section 测试阶段
    集成测试           :c1, after b1, 7d
    部署上线           :milestone, c2, after c1, 3d
```

### 19.8 饼图

**语法：**

````markdown
```mermaid
pie title 语言占比
    "JavaScript" : 40
    "Python" : 30
    "Go" : 30
```
````

**效果预览：**

```mermaid
pie showData
    title 前端技术栈使用占比
    "JavaScript / TypeScript" : 40
    "CSS / Sass" : 20
    "HTML" : 15
    "框架 (Vue/React)" : 15
    "其他" : 10
```

### 19.9 ER 图

**语法：**

````markdown
```mermaid
erDiagram
    USER ||--o{ ORDER : places
    USER {
        int id PK
        string name
    }
```
````

**效果预览：**

```mermaid
erDiagram
    USER ||--o{ ORDER : places
    USER ||--|{ ADDRESS : has
    ORDER ||--|{ ORDER_ITEM : contains
    PRODUCT ||--o{ ORDER_ITEM : "is in"
    USER {
        int id PK
        string name
        string email UK
        datetime created_at
    }
    ORDER {
        int id PK
        int user_id FK
        string status
        decimal total
    }
    ORDER_ITEM {
        int id PK
        int order_id FK
        int product_id FK
        int quantity
    }
```

基数符号：`||--o{` 一对多（可选）、`||--|{` 一对多（必须）、`}|--||` 多对一、`}o--o{` 多对多。

### 19.10 Git 图

**语法：**

````markdown
```mermaid
gitGraph
    commit
    branch develop
    commit
    checkout main
    merge develop
```
````

**效果预览：**

```mermaid
gitGraph
    commit id: "init"
    commit id: "feat: a"
    branch feature
    checkout feature
    commit id: "feat: b"
    commit id: "fix: c"
    checkout main
    commit id: "docs: d"
    merge feature id: "merge feature"
    commit id: "release v1.0"
```

### 19.11 思维导图

**语法：**

````markdown
```mermaid
mindmap
  root((Markdown))
    基础
      标题
      列表
```
````

**效果预览：**

```mermaid
mindmap
  root((Markdown))
    基础语法
      标题
      列表
      引用
      分隔线
    扩展语法
      表格
      任务列表
      脚注
      删除线
    高级能力
      LaTeX 公式
      Mermaid 图表
      HTML 嵌入
```

### 19.12 用户旅程图

**语法：**

````markdown
```mermaid
journey
    title 用户的一天
    section 上午
      写文档: 5: 用户
```
````

**效果预览：**

```mermaid
journey
    title 用户使用 Markdown 编辑器的一天
    section 早晨
      打开编辑器: 5: 用户
      撰写文档: 4: 用户
    section 上午
      插入 Mermaid 图表: 4: 用户
      添加 LaTeX 公式: 3: 用户
    section 下午
      导出 PDF: 4: 用户
      分享给同事: 5: 用户
```

### 19.13 象限图

**语法：**

````markdown
```mermaid
quadrantChart
    title 工具选型矩阵
    x-axis 成本 低 --> 高
    y-axis 效率 低 --> 高
```
````

**效果预览：**

```mermaid
quadrantChart
    title 工具选型矩阵
    x-axis 学习成本 低 --> 高
    y-axis 使用频率 低 --> 高
    quadrant-1 明星方案
    quadrant-2 稳健之选
    quadrant-3 冷门备选
    quadrant-4 一次性方案
    "Markdown": [0.3, 0.9]
    "Typora": [0.35, 0.6]
    "Word": [0.2, 0.85]
    "LaTeX": [0.9, 0.35]
```

### 19.14 柱状折线图

**语法：**

````markdown
```mermaid
xychart-beta
    title "营收"
    x-axis [Q1, Q2, Q3, Q4]
    y-axis "营收" 0 --> 200
    bar [80, 120, 150, 180]
```
````

**效果预览：**

```mermaid
xychart-beta
    title "季度营收（万元）"
    x-axis [Q1, Q2, Q3, Q4]
    y-axis "营收" 0 --> 200
    bar [80, 120, 150, 180]
    line [60, 100, 130, 170]
```

### 19.15 C4 架构图

**语法：**

````markdown
```mermaid
C4Context
    title 系统上下文图
    Person(user, "用户", "浏览并下单")
    System(sys, "电商平台", "处理订单")
```
````

**效果预览：**

```mermaid
C4Context
    title 系统上下文图 - 电商平台
    Person(customer, "顾客", "浏览商品并下单")
    System(ecommerce, "电商平台", "处理订单与支付")
    System_Ext(payment, "支付网关", "第三方支付服务")
    System_Ext(shipping, "物流系统", "配送跟踪")
    Rel(customer, ecommerce, "使用", "HTTPS")
    Rel(ecommerce, payment, "调用", "API")
    Rel(ecommerce, shipping, "调用", "API")
```


---

## 20. 目录与锚点 (TOC & Anchors)

### 20.1 各平台目录语法

**语法：**

```markdown
[[toc]]
```

**效果预览（各平台支持情况）：**

| 平台 | 目录方式 | 支持 |
| :--- | :--- | :-: |
| VitePress | 配置 `themeConfig.outline` | ✅ |
| VuePress | 页面内写 `[[toc]]` | ✅ |
| GitBook | `SUMMARY.md` | ✅ |
| Docusaurus | 右侧边栏自动生成 | ✅ |
| MkDocs Material | 右侧边栏自动生成 | ✅ |
| Hexo / Next | 主题配置 `toc` | ✅ |
| GitHub | 无内置目录 | ❌ |

### 20.2 手动维护目录

**语法：**

```markdown
- [1. 标题](#1-标题-headings)
    - [1.1 ATX 语法](#11-atx-语法推荐)
- [2. 段落与换行](#2-段落与换行-paragraphs--line-breaks)
```

**效果预览：**

- [1. 标题 (Headings)](#1-标题-headings)
    - [1.1 ATX 语法](#11-atx-语法推荐)
    - [1.2 标题内可嵌套其他格式](#12-标题内可嵌套其他格式)
- [2. 段落与换行](#2-段落与换行-paragraphs--line-breaks)
    - [2.1 空行分段](#21-空行分段)
    - [2.2 软换行](#22-软换行行尾两个空格)
- [3. 文本格式](#3-文本格式-text-formatting)
- [19. Mermaid 图表](#19-mermaid-图表-mermaid-diagrams)
    - [19.1 流程图](#191-流程图与方向控制)
    - [19.3 时序图](#193-时序图)

---

## 21. 表情符号 (Emoji)

### 21.1 `:代号:` 语法

**语法：**

```markdown
:rocket: :tada: :heart: :fire:
```

**效果预览：**

:rocket: :tada: :heart: :fire: :bulb: :books: :computer: :warning:

### 21.2 直接输入 Unicode Emoji

**语法：**

```markdown
直接输入 Unicode 也可以：🚀 🎉 ❤️ 🔥
```

**效果预览：**

直接输入 Unicode 也可以：🚀 🎉 ❤️ 🔥 💡 ✅ ❌ ⚠️ 🎯

### 21.3 常用 Emoji 分类速查

**效果预览：**

| 分类 | 代号 → 效果 |
| :--- | :--- |
| 表情 | 😄 `:smile:` · 🤔 `:thinking:` · 😭 `:sob:` · 🥳 `:partying_face:` |
| 手势 | 👍 `:thumbsup:` · 👎 `:thumbsdown:` · 👏 `:clap:` · 🙌 `:raised_hands:` |
| 符号 | ✅ `:white_check_mark:` · ❌ `:x:` · ⚠️ `:warning:` · 💯 `:100:` |
| 开发 | 🐛 `:bug:` · 🔧 `:wrench:` · 📦 `:package:` · ⚡ `:zap:` |
| 状态 | 🚧 `:construction:` · 💥 `:boom:` · ❄️ `:snowflake:` |
| 装饰 | 🚀 `:rocket:` · 🎉 `:tada:` · ✨ `:sparkles:` · 🏆 `:trophy:` |

### 21.4 实际组合使用

**效果预览：**

- 🚀 **发布** v1.0.0 正式版
- 🐛 **修复** 3 个缺陷
- 📝 **更新** 文档
- ⚠️ **废弃** 旧版 API

---

## 22. 特殊代码块 (Special Code Blocks)

### 22.1 diff 代码块

**语法：**

````markdown
```diff
- 被删除的行
+ 新增的行
! 重要变更
```
````

**效果预览：**

```diff
 function add(a, b) {
-  return a + b;
+  return a + b + (c ?? 0);
 }
```

```diff
--- a/src/index.js
+++ b/src/index.js
@@ -1,5 +1,8 @@
 import express from 'express';
+import helmet from 'helmet';

 const app = express();
-app.listen(3000);
+const server = app.listen(process.env.PORT || 3000);
+server.keepAliveTimeout = 65000;
```

颜色规则：`-` 红色（删除）、`+` 绿色（新增）、`!` 黄色（重要）。

### 22.2 折叠代码块

**语法：**

````html
<details>
<summary>点击展开</summary>

```js
console.log("默认折叠");
```

</details>
````

**效果预览：**

<details>
<summary>🔽 点击展开代码</summary>

```js
console.log("默认折叠的内容");
```

</details>

### 22.3 高亮指定代码行

**语法：**

````markdown
```js {2,4-5}
const a = 1;
const b = 2;
const c = 3;
const d = 4;
const e = 5;
```
````

**效果预览：**

```js {2,4-5}
const a = 1;   // 第 1 行
const b = 2;   // 第 2 行 ★高亮
const c = 3;   // 第 3 行
const d = 4;   // 第 4 行 ★高亮
const e = 5;   // 第 5 行 ★高亮
```

### 22.4 终端会话

**语法：**

````markdown
```console
$ npm install
added 128 packages in 4s
```
````

**效果预览：**

```console
$ npm install
added 128 packages in 4s

$ npm run dev
  VITE v5.0.0  ready in 231 ms

  ➜  Local:   http://localhost:5173/
```

---

## 23. HTML 嵌入 (HTML Embedding)

Markdown 允许内嵌几乎所有 HTML，这是它"可扩展"的核心理念。

### 23.1 折叠内容 (details/summary)

**语法：**

```html
<details>
<summary>点击展开</summary>

折叠区可以放 Markdown 内容：

- 列表项
- 列表项

</details>
```

**效果预览：**

<details>
<summary>👆 点击展开更多内容</summary>

折叠区可以放 Markdown 内容：

- 列表项 1
- 列表项 2

> 也可以放引用块。

</details>

### 23.2 文本样式

**语法：**

```html
<span style="color:#e11d48">红色</span>
<span style="color:#2563eb;font-weight:bold">蓝色粗体</span>
<span style="background-color:#fef08a;padding:2px 6px;border-radius:4px;">黄底</span>
<small>小号</small> <big>大号</big>
<del>删除线</del> <ins>下划线</ins>
```

**效果预览：**

<span style="color:#e11d48">红色文字</span>
<span style="color:#2563eb;font-weight:bold">蓝色粗体</span>
<span style="color:#059669;font-style:italic">绿色斜体</span>
<span style="background-color:#fef08a;padding:2px 6px;border-radius:4px;">黄色底色</span>
<span style="background-color:#86efac;color:#14532d;padding:2px 6px;border-radius:4px;">绿色底色</span>

<small>小号文字</small> <big>大号文字</big> <del>删除线</del> <ins>下划线</ins> <sub>下标</sub> <sup>上标</sup>

### 23.3 对齐

**语法：**

```html
<p align="center">居中</p>
<p align="right">右对齐</p>
```

**效果预览：**

<p align="center">居中文字</p>

<p align="right">右对齐文字</p>

<p align="left">左对齐文字（默认）</p>

### 23.4 HTML 表格（colspan、对齐）

**语法：**

```html
<table>
  <tr><th>A</th><th>B</th><th>C</th></tr>
  <tr><td colspan="3">跨三列</td></tr>
</table>
```

**效果预览：**

<table>
  <thead>
    <tr><th>列 A</th><th align="center">列 B</th><th align="right">列 C</th></tr>
  </thead>
  <tbody>
    <tr><td>值 1</td><td align="center">居中值</td><td align="right">1,234</td></tr>
    <tr><td colspan="3" align="center"><em>这一行横跨三列</em></td></tr>
  </tbody>
</table>

### 23.5 自定义提示块

**语法：**

```html
<div style="border-left:4px solid #3b82f6;padding:8px 12px;background:#eff6ff;">
  <b>ℹ️ 说明</b><br>内容
</div>
```

**效果预览：**

<div style="border-left:4px solid #3b82f6;padding:8px 12px;background:#eff6ff;border-radius:4px;">
  <b>ℹ️ 说明</b><br>这是一个自定义提示块（info）。
</div>

<div style="border-left:4px solid #22c55e;padding:8px 12px;background:#f0fdf4;border-radius:4px;">
  <b>✅ 成功</b><br>操作已成功完成（success）。
</div>

<div style="border-left:4px solid #f59e0b;padding:8px 12px;background:#fffbeb;border-radius:4px;">
  <b>⚠️ 警告</b><br>请注意这个警告（warning）。
</div>

<div style="border-left:4px solid #ef4444;padding:8px 12px;background:#fef2f2;border-radius:4px;">
  <b>🚫 危险</b><br>这是一个危险提示（danger）。
</div>

### 23.6 进度条与其他标签

**语法：**

```html
<progress value="70" max="100"></progress>
<abbr title="HyperText Markup Language">HTML</abbr>
```

**效果预览：**

进度 70%：<progress value="70" max="100"></progress>
完成 100%：<progress value="100" max="100"></progress>

<abbr title="HyperText Markup Language">HTML</abbr> ·
<abbr title="Cascading Style Sheets">CSS</abbr> ·
<abbr title="JavaScript">JS</abbr>

### 23.7 视频 / 音频 / iframe

**语法：**

```html
<video src="demo.mp4" controls width="400"></video>
<audio src="demo.mp3" controls></audio>
<iframe src="https://example.com" width="100%" height="200"></iframe>
```

**效果预览：**

<video src="media/demo.mp4" controls width="400"></video>

<audio src="media/demo.mp3" controls></audio>

> ⚠️ 视频/音频需要真实存在的文件路径；`iframe` 在 GitHub 等平台会被安全策略过滤。

---

## 24. 特殊字符与转义 (Escaping)

### 24.1 反斜杠转义

**语法：**

```markdown
\* 不是斜体 \*
\# 不是标题 \#
\`不是代码\`
\> 不是引用 \>
\- 不是列表
!\[不是图片]
1\. 不是有序列表
```

**效果预览：**

\* 不是斜体 \*
\# 不是标题 \#
\`不是代码\`
\> 不是引用 \>
\- 不是列表
!\[不是图片]
1\. 不是有序列表
\[方括号不构成链接\]

### 24.2 可转义字符一览

**效果预览：**

| 字符 | 源码 | 效果 |
| :-: | :--- | :--- |
| `\` | `\\` | \ |
| `` ` `` | ``\` `` | ` |
| `*` | `\*` | \* |
| `_` | `\_` | \_ |
| `#` | `\#` | \# |
| `+` | `\+` | \+ |
| `-` | `\-` | \- |
| `.` | `\.` | \. |
| `!` | `\!` | \! |
| `[` | `\[` | \[ |
| `]` | `\]` | \] |
| `(` | `\(` | \( |
| `)` | `\)` | \) |
| `{` | `\{` | \{ |
| `}` | `\}` | \} |
| `\|` | `\|` | \| |
| `>` | `\>` | \> |
| `~` | `\~` | \~ |
| `=` | `\==` | \== |

### 24.3 HTML 实体

**效果预览：**

| 实体 | 字符 | 说明 |
| :-: | :-: | :--- |
| `&copy;` | © | 版权 |
| `&reg;` | ® | 注册商标 |
| `&trade;` | ™ | 商标 |
| `&hellip;` | … | 省略号 |
| `&mdash;` | — | 长破折号 |
| `&ndash;` | – | 短破折号 |
| `&lt;` | < | 小于号 |
| `&gt;` | > | 大于号 |
| `&amp;` | & | 与号 |
| `&nbsp;` | (空格) | 不间断空格 |
| `&laquo;` `&raquo;` | « » | 双尖引号 |
| `&times;` | × | 乘号 |
| `&divide;` | ÷ | 除号 |
| `&deg;` | ° | 度数 |
| `&bull;` | • | 项目符号 |

混排示例：&copy; 2025 &mdash; 版权所有 &bull; v1.0 &bull; MIT

### 24.4 Unicode 符号

**效果预览：**

✔ ✘ ✚ ✱ ★ ☆ ♥ ♦ ♠ ♣ ♤ ♢

← ↑ → ↓ ↔ ↕ ⇄ ⇅ ⇒ ⇔

∀ ∃ ∈ ∉ ⊂ ⊆ ⊇ ∪ ∩ ∅ ∞ ≈ ≠ ≤ ≥ ± × ÷ √ ∑ ∏ ∫ ∂ ∇

---

## 25. 常见陷阱 (Pitfalls)

### 25.1 标题与正文间要有空行

**❌ 错误：**

```markdown
# 标题
这段文字会被吞进标题里。
```

**✅ 正确：**

```markdown
# 标题

这段文字是独立段落。
```

**效果预览（正确写法）：**

# 标题

这段文字是独立段落。

### 25.2 列表前后要有空行

**❌ 错误：**

```markdown
这是段落：
- 列表项
```

**✅ 正确：**

```markdown
这是段落：

- 列表项
```

**效果预览（正确写法）：**

这是段落：

- 列表项 1
- 列表项 2

### 25.3 中英文之间加空格

**❌ 不推荐：**

```markdown
这是**加粗**，那是`代码`，还有[链接](https://example.com)。
```

**✅ 推荐：**

```markdown
这是 **加粗**，那是 `代码`，还有 [链接](https://example.com)。
```

**效果预览（推荐）：**

这是 **加粗**，那是 `代码`，还有 [链接](https://example.com)，以及 ~~删除线~~ 和 <kbd>Ctrl</kbd>。

### 25.4 块级元素之间空一行

**✅ 推荐写法：**

````markdown
段落文字。

- 列表项

> 引用内容

| 表头 | 示例 |
| :--- | ---: |
| a | 1 |

```js
// 代码
```
````

**效果预览：**

段落文字。

- 列表项

> 引用内容

| 表头 | 示例 |
| :--- | ---: |
| a | 1 |

```js
// 代码
```

### 25.5 用空行而不是换行来分段

**❌ 不推荐：**

```markdown
第一句
第二句
```

**✅ 推荐：**

```markdown
第一句。

第二句。
```

**效果预览（推荐）：**

第一句。

第二句。

**想连续换行而不断段，用行尾两空格或 `<br>`：**

第一句。  
第二句。<br>
第三句。

### 25.6 嵌套不超过 3 层

**效果预览：**

- **L1** 一级
    - **L2** 二级
        - **L3** 三级
            - **L4** 已较深，建议改用表格或 Mermaid 表达

### 25.7 项目文档结构模板

**语法：**

````markdown
# 项目名

一段简介。

## 目录

- [功能](#功能)

---

## 功能

### 1.1 子功能

正文。

- 列表项
  多段续接（缩进）。

| 表格 | 示例 |
| :--- | ---: |
| a | 1 |

> 引用。

```js
// 代码
```
````

**效果预览：**

````markdown
# 项目名

一段简介。

## 目录

- [功能](#功能)

---

## 功能

### 1.1 子功能

正文内容。

- 列表项

  多段续接（缩进）。

| 表格 | 示例 |
| :--- | ---: |
| a | 1 |

> 引用内容。

```js
// 代码
```
````

---

## 26. 兼容性对照表 (Compatibility Matrix)

**效果预览：**

| 语法 | GitHub | Typora | Obsidian | VSCode | VitePress | Pandoc |
| :--- | :-: | :-: | :-: | :-: | :-: | :-: |
| 标题 / 段落 / 引用 / 列表 | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| 行内代码 / 代码块 | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| 表格 | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| 删除线 `~~text~~` | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| 任务列表 | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| 自动链接 | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Emoji `:name:` | ✅ | ✅ | ✅ | ⚠️ 插件 | ✅ | ✅ |
| 脚注 `[^x]` | ✅ | ✅ | ✅ | ⚠️ 插件 | ✅ | ✅ |
| 上标/下标 | ➡️ HTML | ✅ | ✅ | ➡️ HTML | ➡️ HTML | ✅ |
| 高亮 `==text==` | ❌ | ✅ | ✅ | ⚠️ 插件 | ❌ | ❌ |
| 定义列表 | ❌ | ✅ | ✅ | ⚠️ | ❌ | ✅ |
| 数学公式 `$...$` | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Mermaid | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| diff 代码块 | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| 内嵌 HTML | ⚠️ 白名单 | ✅ | ✅ | ✅ | ✅ | ⚠️ 有限 |
| GitHub Alert | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ |
| 代码块内注释 `{1,3}` | ❌ | ✅ | ✅ | ⚠️ | ❌ | ❌ |

图例：✅ 支持 ｜ ⚠️ 需插件或有限支持 ｜ ❌ 不支持 ｜ ➡️ 需 HTML 替代

---

## 27. 一页速查 (Cheat Sheet)

**效果预览（下面是实际渲染的样子，可截图当速查卡）：**

| 用途 | 语法 | 渲染效果 |
| :--- | :--- | :--- |
| 标题 | `## H2` | ## 这是 H2 |
| 粗体 | `**text**` | **粗体** |
| 斜体 | `*text*` | *斜体* |
| 粗斜 | `***text***` | ***粗斜*** |
| 删除线 | `~~text~~` | ~~删除线~~ |
| 行内码 | `` `code` `` | `code()` |
| 引用 | `> quote` | > 引用内容 |
| 无序 | `- item` | - 无序项 |
| 有序 | `1. item` | 1. 有序项 |
| 任务 | `- [x] done` | - [x] 已完成 |
| 链接 | `[t](url)` | [链接](https://example.com) |
| 图片 | `![a](p.png)` | ![alt](assets/markdown-banner.jpg) |
| 分隔线 | `---` | --- |
| 公式 | `$x^2$` | $x^2 + y^2 = z^2$ |
| 强调 | `==text==` | ==高亮== |

---

## 🎓 总结

Markdown 的强大在于它在「**易读易写**」与「**无限扩展**」之间的平衡：

| 层次 | 能力 | 代表语法 |
| :-: | :--- | :--- |
| 1️⃣ | **核心** | 标题、段落、列表、引用、链接、图片、代码 |
| 2️⃣ | **GFM 扩展** | 表格、任务列表、删除线、自动链接、Emoji、脚注 |
| 3️⃣ | **图表** | Mermaid 流程图 / 时序图 / 类图 / 甘特图等 |
| 4️⃣ | **数学** | LaTeX / KaTeX 行内与块级公式 |
| 5️⃣ | **兜底** | 内嵌 HTML 补齐任意渲染器短板 |

掌握这套语法后，**一份 `.md` 文件即可包含结构化文档、可视化图表、数学公式与富文本排版**。

---

> 📚 文档持续完善中 · 需要补充某个语法的更多示例，随时告诉我 🚀
