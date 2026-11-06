# Builds VidCatch two ways with a console window and tells which one starts. Run from the project folder:
#   powershell -ExecutionPolicy Bypass -File .\packaging\diagnose.ps1
$ErrorActionPreference = "Continue"
python -m pip install -q -r requirements.txt pyinstaller
foreach ($variant in @(@{name="with-curl"; nocurl=""}, @{name="no-curl"; nocurl="1"})) {
    Write-Host "=== Variant: $($variant.name) ===" -ForegroundColor Cyan
    $env:VC_CONSOLE = "1"; $env:VC_NO_CURL = $variant.nocurl
    python -m PyInstaller packaging/vidcatch.spec --noconfirm --log-level ERROR --distpath "output/diagnose-$($variant.name)/dist" --workpath "output/diagnose-$($variant.name)/build"
    python packaging/smoke_test.py "output/diagnose-$($variant.name)/dist/VidCatch/VidCatch.exe"
}
Remove-Item Env:VC_CONSOLE, Env:VC_NO_CURL -ErrorAction SilentlyContinue
