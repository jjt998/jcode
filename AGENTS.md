# Project Instructions

## Python Environment

For all work in this repository, use the project-specific Python environment:

```powershell
D:\jt\ANACONDA\envs_dirs\jcode
```

Use the interpreter explicitly:

```powershell
& 'D:\jt\ANACONDA\envs_dirs\jcode\python.exe'
```

Use pip through that interpreter:

```powershell
& 'D:\jt\ANACONDA\envs_dirs\jcode\python.exe' -m pip
```

Do not use the system `python`, `py`, or the base Anaconda Python for this project unless the user explicitly asks for it.

Example commands:

```powershell
& 'D:\jt\ANACONDA\envs_dirs\jcode\python.exe' -m pip install -e .
& 'D:\jt\ANACONDA\envs_dirs\jcode\python.exe' -m src --help
```

## Git Workflow

After coding or documentation changes, do not create git commits unless the user explicitly asks for a commit in that turn.

## Local Test Services

If you start a local service while testing, stop that service before finishing the turn. This includes web servers, dev servers, API servers, background workers, and other long-running local processes.

## Conventions
写代码需要补充一些函数级和代码级的UTF-8的中文注释，简单易懂，废话不要说。
并且改代码时看到已存在的注释，除非你是为了前后注释的兼容性，否则你不要删掉它。

功能升级时，不用再做兼容式的兜底编程，按当前协议直接完成实现。
当做新代码开发时，遇到中间层只做转发的代码，融到外层，遇到感觉多余或者兼容的代码，大但删掉，保证支持最新版本的最适配代码量，而不是无脑的堆屎山代码。

当你定义DataClass时，你需要在成员变量的右边用#来注释写上该变量的含义。

当你完成了某次确切的计划或任务，且做完了一切编码任务时，那么你需要在最终总结输出用star法则总结的feat文本。如果你仅仅是给了答复，但是并没有编码，生成新文件，那么你就不需要生成这个feat文本。

当你定义类、接口、变量等名称时，要具体，不能太抽象了。

## Bug 文档记录规范

当发现并确认一个 Bug 时，需要同时创建两份相互对应的 Markdown 文档：

- Bug 记录：`docs/BUGS-after9.6/`
- 解决方案讨论：`docs/BUGS-solve/`

两份文件必须使用相同的中文主题名，并在文件名中包含发现日期，格式如下：

```text
YYYY-MM-DD-中文问题简述.md

## 禁止擅自增加兜底行为

实现功能时必须严格遵循已经确认的设计、协议和失败语义。设计中未明确规定的降级、兼容、重试、裁剪、删除、跳过、默认值、自动修复或其他兜底行为，一律不得擅自添加。

当既定流程无法满足条件时，应按照设计规定返回错误或停止执行，不得为了“尽量成功”“提高健壮性”或“避免报错”而自行改变业务语义。尤其禁止静默删除数据、压缩内容、吞掉异常、替换输入、降低校验标准或绕过失败出口。

如果发现设计存在未覆盖的边界情况：

1. 先保留既定语义；
2. 明确说明触发条件、影响和可选方案；
3. 等待用户确认后再修改设计或实现。

代码审查时，任何无法在已确认文档、协议或用户要求中找到依据的兜底逻辑，都应视为设计偏差并删除，而不是默认保留。