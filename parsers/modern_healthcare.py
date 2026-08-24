from ._common import collect_listing

def collect(source):
    return collect_listing(source, allowed=('/',), blocked=('/podcast', '/video', '/events', '/jobs', '/about'), max_items=35)
