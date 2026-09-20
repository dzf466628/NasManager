# NAS 管理器

群晖 / Linux NAS 的 Windows 桌面管理工具，基于 PySide6 + paramiko。

## 功能

| 模块 | 功能 |
|------|------|
| 连接管理 | 多 NAS 配置，密码存 Windows 凭据管理器（不明文），状态指示灯 |
| 仪表盘 | CPU / 内存 / 磁盘 / 网络速率 / 运行时间，刷新间隔可配置（默认 5 秒） |
| 服务管理 | Node / Nginx / Docker 一键启停重启，自定义服务，进程列表 |
| 端口管理 | netstat 解析表格，常用端口高亮，双击查看进程详情 |
| 网页面板 | Node 实时日志、HTTP 健康检查、反代配置（自动探测挂载点/尾斜杠兼容/写入验证） |
| 文件管理 | SSH 浏览、上传/下载/删除/重命名，内置文本编辑器直接改配置 |
| 内置终端 | SSH 交互式终端，命令历史，快捷命令栏 |
| 系统配置 | 一键复制 NAS 环境快照 + 网站能力评估，直接发给 AI 排查问题 |
| 自愈能力 | 站点异常停止自动重启、反代文件被 Web Station 清空自动重写 |

## 运行

```powershell
pip install -r requirements.txt
python main.py
```

需要 sudo 的操作（Nginx 重载等）：连接后点工具栏「设置 Sudo 密码」，仅本次会话缓存。

## 打包 exe

```powershell
pip install pyinstaller
.\build.bat                    # 或: python -m PyInstaller NasManager.spec
"C:\Program Files (x86)\Inno Setup 6\ISCC.exe" NasManager.iss   # 生成安装包
```

产物：
- `dist\NasManager\`（onedir：exe + `_internal` 依赖目录）
- `NasManager_Setup_X.Y.Z.exe`（Inno Setup 安装包，自动更新下载的就是它）

配置存在 `%APPDATA%\NasManager\`（connections.json / settings.json，密码在 Windows 凭据管理器）。

## 发版

版本号需两处同步：`main.py` 的 `APP_VERSION`（界面/更新比较）与 `NasManager.iss` 的 `MyAppVersion`（安装包文件名/版本），改完重新打包。

## 预设服务配置

编辑 `services/presets.json` 可修改 Node/Nginx 路径、添加自定义服务和快捷命令。
