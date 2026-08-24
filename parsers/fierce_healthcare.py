from ._common import collect_listing

def collect(source):
    return collect_listing(source, allowed=('/',), blocked=('/rss', '/events', '/webinars', '/podcasts', '/about'), max_items=35)
