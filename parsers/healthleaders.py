from ._common import collect_listing

def collect(source):
    return collect_listing(source, allowed=('/',), blocked=('/webinar', '/events', '/podcast', '/newsletter', '/about'), max_items=35)
