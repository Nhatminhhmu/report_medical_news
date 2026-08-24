from ._common import collect_listing

def collect(source):
    return collect_listing(source, allowed=('/content-type/news/',), blocked=['/membership', '/events', '/about'], max_items=35)
