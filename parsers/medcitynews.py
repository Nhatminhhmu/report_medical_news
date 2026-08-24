from ._common import collect_listing

def collect(source):
    return collect_listing(source, allowed=('/',), blocked=('/events', '/about', '/author', '/podcast'), max_items=35)
