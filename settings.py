"""Validated numeric settings shared by services and the cleanup task."""


def number(config, key, default, minimum, maximum):
    value = config.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ValueError(f"{key} 必须是 {minimum}～{maximum} 之间的整数")
    return value
