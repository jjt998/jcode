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