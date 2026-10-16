# PostgreSQL 16 静默安装脚本
$installerPath = "$env:TEMP\postgresql-installer.exe"
$url = "https://get.enterprisedb.com/postgresql/postgresql-16.4-1-windows-x64.exe"

Write-Output "=== 下载 PostgreSQL 16.4 ==="
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
Invoke-WebRequest -Uri $url -OutFile $installerPath -UseBasicParsing
Write-Output "下载完成: $installerPath"

Write-Output "=== 静默安装 PostgreSQL ==="
# 静默安装参数：
# --mode unattended             静默模式
# --prefix "C:\Program Files\PostgreSQL\16"  安装路径
# --datadir "C:\Program Files\PostgreSQL\16\data"  数据路径
# --superpassword "postgres"    超级用户密码
# --port 5432                   端口
# --locale "Chinese_China.936"  地区
$installArgs = @(
    "--mode", "unattended"
    "--prefix", "C:\Program Files\PostgreSQL\16"
    "--datadir", "C:\Program Files\PostgreSQL\16\data"
    "--superpassword", "postgres"
    "--port", "5432"
    "--locale", "Chinese_China.936"
    "--serverport", "5432"
)

Write-Output "安装参数: $installArgs"
Start-Process -FilePath $installerPath -ArgumentList $installArgs -Wait -NoNewWindow
Write-Output "==="
Write-Output "安装完成。"

# 验证安装
$pgBin = "C:\Program Files\PostgreSQL\16\bin"
$env:Path += ";$pgBin"
$version = & "$pgBin\psql.exe" --version 2>&1
Write-Output "PostgreSQL 版本: $version"

# 检查服务
$svc = Get-Service -Name "postgresql*" -ErrorAction SilentlyContinue
if ($svc) {
    Write-Output "服务状态: $($svc.Status)"
} else {
    Write-Output "警告: PostgreSQL 服务未找到"
    Write-Output "可手动启动: net start postgresql-x64-16"
}
