from ._common import collect_listing

def collect(source):
    return collect_listing(source, allowed=('/news/',), blocked=('/about', '/author', '/category'), max_items=35)
