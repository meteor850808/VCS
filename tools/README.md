# tools/libredwg — 第三方 DWG 转换工具（按需 vendor）

本目录存放 **GNU LibreDWG 的 Windows 二进制发行版（DWGTools）**，供 `DWG → DXF`/SVG 转换链路在运行时 shell-out 调用。

## 为什么不入库

- `libredwg-0.dll` 约 30MB，全套约 35MB，为**第三方预编译二进制**，非本项目源码。
- 直接提交会永久膨胀 git 仓库（每次克隆/打包都携带），违背"收敛散乱历史"原则。
- 因此该目录已被 `.gitignore` 排除，**不在 git 版本控制内**。

## 从哪里获取

1. 官方项目：<https://github.com/LibreDWG/libredwg/releases>
2. 取 **Windows** 发行包，解压 `bin/` 下 DWGTools 可执行文件与 `bin/` 依赖 DLL
   （`libredwg-0.dll`、`libiconv-2.dll`、`libpcre2-16-0.dll`、`libpcre2-8-0.dll`）
   到本目录。

## 部署注意事项

- 转换 worker 运行时需能解析到本目录（脚本通过相对/绝对路径定位 `dwgread.exe` 等）。
- Docker 镜像构建若需 DWG 能力，应在镜像内 `apt` 安装 `libredwg-utils` 或 COPY 对应二进制，
  而非依赖 git 子模块（本项目未以 git 子模块管理这些大文件）。

## 联系人/更新

- 升级 LibreDWG 时：替换本目录二进制 + 同步转换脚本中的版本预期。
- 问题排查：转换失败常因 DLL 缺失/架构不匹配（x64 vs x86），先核对文件完整性。