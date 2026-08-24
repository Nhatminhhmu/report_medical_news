from ._common import collect_listing

def collect(source):
    return collect_listing(source, allowed=('/news-center/',), blocked=('/press-information', '/events/'), max_items=35)
