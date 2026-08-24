from ._common import collect_listing

def collect(source):
    return collect_listing(source, allowed=['/news/', '/blog/'], blocked=('/about', '/topics', '/resources'), max_items=35)
