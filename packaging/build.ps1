# Build VidCatch.exe on Windows. Run from the project folder:
#   powershell -ExecutionPolicy Bypass -File .\packaging\build.ps1
$ErrorActionPreference = "Stop"

function Run($what) {
    & $args[0] @($args | Select-Object -Skip 1)
    if ($LASTEXITCODE -ne 0) { throw "$what failed (exit code $LASTEXITCODE)" }
}

Run "Installing packages" python -m pip install -r requirements.txt pyinstaller
Run "PyInstaller" python -m PyInstaller packaging/vidcatch.spec --noconfirm --distpath output/dist --workpath output/build
Run "Start-up check" python packaging/smoke_test.py output/dist/VidCatch/VidCatch.exe
Compress-Archive -Path output/dist/VidCatch -DestinationPath output/VidCatch-windows.zip -Force
Write-Host "Done: output\dist\VidCatch\VidCatch.exe and output\VidCatch-windows.zip"
