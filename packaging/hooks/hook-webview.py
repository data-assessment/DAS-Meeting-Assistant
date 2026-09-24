"""Only Windows/WebView2; do not collect pywebview's packaging hooks."""
from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files, get_package_paths

_, package = get_package_paths("webview")
root = Path(package)
datas = collect_data_files("webview", subdir="js")
for architecture in ("win-arm64", "win-x86"):
    datas.append((str(Path(__file__).with_name("webview-native-placeholder.txt")),
                  f"webview/lib/runtimes/{architecture}/native"))
binaries = []
for relative in (
    "lib/Microsoft.Web.WebView2.Core.dll",
    "lib/Microsoft.Web.WebView2.WinForms.dll",
    "lib/WebBrowserInterop.x64.dll",
    "lib/runtimes/win-x64/native/WebView2Loader.dll",
):
    path = root / relative
    if not path.is_file():
        raise RuntimeError(f"Required Windows webview component missing: {relative}")
    binaries.append((str(path), str(Path("webview") / Path(relative).parent)))

hiddenimports = ["webview.platforms.edgechromium", "webview.platforms.winforms"]
