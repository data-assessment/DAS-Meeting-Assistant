"""Keep the LGPL tray library as replaceable source on Windows."""
hiddenimports = ["pystray._win32", "pystray._util.win32"]
excludedimports = ["pystray._appindicator", "pystray._darwin", "pystray._gtk", "pystray._xorg"]
module_collection_mode = "py"
