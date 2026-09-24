"""Device selection and content-free capture diagnostics."""

def preferred_device(candidates, previous=None):
    """Preserve an explicit selection; initially use only a unique Windows default."""
    if previous is not None:
        fields = ("name", "defaultSampleRate", "maxInputChannels", "isLoopbackDevice")
        matches = [i for i, item in enumerate(candidates)
                   if all(item.get(field) == previous.get(field) for field in fields)]
    else:
        matches = [i for i, item in enumerate(candidates) if item.get("isSystemDefault")]
    return matches[0] if len(matches) == 1 else -1


def input_status(metric, now):
    last = metric.get("last_frame")
    if last is None or not metric.get("frames", 0):
        return "Keine Audiodaten vom Gerät"
    if now - last > 2:
        return "Aktuell keine Audiodaten vom Gerät"
    if metric.get("dbfs", -100) < -65:
        return "Audiodaten kommen an, aber Stille / sehr leise"
    return "Audiosignal vorhanden"
