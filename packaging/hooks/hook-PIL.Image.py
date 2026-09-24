"""The desktop tray reads ICO/PNG/BMP images; no optional image codecs."""
hiddenimports = ["PIL.IcoImagePlugin", "PIL.PngImagePlugin", "PIL.BmpImagePlugin"]
excludedimports = ["PIL._avif", "PIL._webp", "PIL._imagingft", "PIL._imagingcms",
                   "PIL.ImageTk", "PIL._imagingtk"]
