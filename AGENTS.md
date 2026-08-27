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
