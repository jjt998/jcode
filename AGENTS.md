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